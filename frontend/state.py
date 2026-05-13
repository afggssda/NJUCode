from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

if TYPE_CHECKING:
    from .services.context_compressor import ContextCompressor

from .models import ChatMessage, ChatSession, DEFAULT_TOOLS, MIRROR_PRESETS, ModelConfig, ToolToggle
from .services.settings_store import SettingsStore
from .services.context_compressor import ContextCompressor
from .skills.models import SkillToggle
from .skills.registry import SkillRegistry
from .skills.audit_log import AuditLogger
from .skills.executor import SkillExecutor
from .skills.permissions import PermissionChecker
from .skills.builtin import BUILTIN_AGENT_MANIFESTS, BUILTIN_MANIFESTS
from .mcp.manager import MCPManager
from .mcp.executor import MCPToolExecutor
from .mcp.models import MCPToolToggle
from .services.patch_engine import (
    PatchEngine,
    PatchExecutionResult,
    PatchHistoryStore,
    PatchTask,
)


class AppState:
    """集中管理应用运行状态与本地持久化。

    该类维护会话、当前激活会话、工具权限与模型配置等核心数据，
    并通过 `SettingsStore` 负责状态的加载与保存。
    UI 层和业务层都通过这个对象进行状态读写。
    """

    def __init__(self, workspace_root: Path) -> None:
        """创建状态容器并设置默认值。

        Args:
            workspace_root: 当前工程根目录，用于定位 `.nju_code/settings.json`。

        初始化时会创建默认会话、工具映射与模型配置，
        并将环境变量中的配置注入为初始值。
        """
        self.workspace_root = workspace_root
        self.settings_store = SettingsStore(workspace_root)
        self.sessions: List[ChatSession] = [ChatSession(title="Default Chat")]
        self.active_session_id = self.sessions[0].session_id
        self.tools: Dict[str, ToolToggle] = {tool.key: tool for tool in DEFAULT_TOOLS}
        self.model_config = ModelConfig(
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.atlascloud.ai/v1"),
            api_key=os.getenv("OPENAI_API_KEY", ""),
            model=os.getenv("OPENAI_MODEL", "deepseek-v3"),
            model_file=os.getenv("OPENAI_MODEL_FILE", ""),
            mirror=os.getenv("OPENAI_MIRROR", "atlascloud"),
        )
        self.left_ratio = 0.18
        self.right_ratio = 0.34

        # Skills system initialization
        self.skills: Dict[str, SkillToggle] = {}
        self.skill_registry: Optional[SkillRegistry] = None
        self._audit_logger: Optional[AuditLogger] = None
        self._skill_executor: Optional[SkillExecutor] = None
        self._analyzer: Any = None  # Set by app.py after CodeAnalyzer creation

        # MCP system initialization
        self.mcp_manager: Optional[MCPManager] = None
        self._mcp_executor: Optional[MCPToolExecutor] = None
        self.mcp_tools: Dict[str, MCPToolToggle] = {}

        # Patch/Rollback system (WBS-4)
        self._patch_engine: Optional[PatchEngine] = None
        self._patch_history_store: Optional[PatchHistoryStore] = None

    def load(self) -> None:
        """从磁盘加载已保存的设置并恢复到内存。

        包括模型配置、工具开关、会话列表和当前会话 ID。
        若读取失败或文件不存在，则保持默认状态不变。
        方法内部会对时间格式与缺失字段进行容错处理。
        """
        data = self.settings_store.load()
        if not data:
            return

        model_data = data.get("model", {})
        self.model_config.base_url = model_data.get("base_url", self.model_config.base_url)
        self.model_config.api_key = model_data.get("api_key", self.model_config.api_key)
        self.model_config.model = model_data.get("model", self.model_config.model)
        self.model_config.model_file = model_data.get("model_file", self.model_config.model_file)
        self.model_config.mirror = model_data.get("mirror", self.model_config.mirror)

        # Env vars should have highest priority so rotating keys in .env takes effect.
        env_base_url = os.getenv("OPENAI_BASE_URL", "").strip()
        env_api_key = os.getenv("OPENAI_API_KEY", "").strip()
        env_model = os.getenv("OPENAI_MODEL", "").strip()
        env_model_file = os.getenv("OPENAI_MODEL_FILE", "").strip()
        env_mirror = os.getenv("OPENAI_MIRROR", "").strip()

        if env_base_url:
            self.model_config.base_url = env_base_url
        if env_api_key:
            self.model_config.api_key = env_api_key
        if env_model:
            self.model_config.model = env_model
        if env_model_file:
            self.model_config.model_file = env_model_file
        if env_mirror:
            self.model_config.mirror = env_mirror

        ui_data = data.get("ui", {})
        self.left_ratio = max(0.0, min(0.9, float(ui_data.get("left_ratio", self.left_ratio))))
        self.right_ratio = max(0.0, min(0.9, float(ui_data.get("right_ratio", self.right_ratio))))

        tools_data = data.get("tools", {})
        for key, value in tools_data.items():
            if key in self.tools:
                self.tools[key].enabled = bool(value)

        sessions_data = data.get("sessions", [])
        loaded_sessions: List[ChatSession] = []
        for session_data in sessions_data:
            messages = []
            for message_data in session_data.get("messages", []):
                timestamp = message_data.get("created_at", "")
                try:
                    created_at = datetime.fromisoformat(timestamp)
                except Exception:
                    created_at = datetime.now()
                messages.append(
                    ChatMessage(
                        role=message_data.get("role", "assistant"),
                        content=message_data.get("content", ""),
                        created_at=created_at,
                        token_count=int(message_data.get("token_count", 0)),
                    )
                )

            loaded_sessions.append(
                ChatSession(
                    session_id=session_data.get("session_id", "") or str(uuid4()),
                    title=session_data.get("title", "New Chat"),
                    messages=messages,
                    summary=session_data.get("summary", ""),
                    compressed_at=(
                        datetime.fromisoformat(session_data["compressed_at"])
                        if session_data.get("compressed_at")
                        else None
                    ),
                    token_estimate=int(session_data.get("token_estimate", 0)),
                    interrupted=bool(session_data.get("interrupted", False)),
                    interrupted_context=session_data.get("interrupted_context") or None,
                    compression_count=int(session_data.get("compression_count", 0)),
                    last_compressed_token_count=int(
                        session_data.get("last_compressed_token_count", 0)
                    ),
                )
            )

        if loaded_sessions:
            self.sessions = loaded_sessions

        active_session_id = data.get("active_session_id", "")
        if any(session.session_id == active_session_id for session in self.sessions):
            self.active_session_id = active_session_id

    def save(self) -> None:
        """将当前状态序列化后写入设置文件。

        会话消息会带上 ISO 时间戳，
        以便下次启动时能准确恢复消息顺序与历史内容。
        该方法通常在关键用户操作后被调用。
        注意：API key 不保存到文件，仅从环境变量读取。
        """
        model_config_dict = asdict(self.model_config)
        # Never save API key to settings file for security
        model_config_dict["api_key"] = ""

        payload = {
            "model": model_config_dict,
            "ui": {
                "left_ratio": self.left_ratio,
                "right_ratio": self.right_ratio,
            },
            "tools": {key: tool.enabled for key, tool in self.tools.items()},
            "active_session_id": self.active_session_id,
            "sessions": [
                {
                    "session_id": session.session_id,
                    "title": session.title,
                    "summary": session.summary,
                    "compressed_at": session.compressed_at.isoformat() if session.compressed_at else None,
                    "token_estimate": session.token_estimate,
                    "interrupted": session.interrupted,
                    "interrupted_context": session.interrupted_context,
                    "compression_count": session.compression_count,
                    "last_compressed_token_count": session.last_compressed_token_count,
                    "messages": [
                        {
                            "role": message.role,
                            "content": message.content,
                            "created_at": message.created_at.isoformat(),
                            "token_count": message.token_count,
                        }
                        for message in session.messages
                    ],
                }
                for session in self.sessions
            ],
        }
        self.settings_store.save(payload)

        # Save skills registry state
        if self.skill_registry:
            self.skill_registry.save()

        # Save audit logs
        if self._audit_logger:
            self._audit_logger.save()

        # Save MCP manager state
        if self.mcp_manager:
            self.mcp_manager.save()

    @property
    def active_session(self) -> ChatSession:
        """返回当前激活会话对象。

        若 `active_session_id` 未命中任何会话，
        将回退到会话列表中的第一项，确保调用方总能拿到有效对象。
        """
        for session in self.sessions:
            if session.session_id == self.active_session_id:
                return session
        return self.sessions[0]

    def create_session(self, title: str | None = None) -> ChatSession:
        """创建一个新会话并切换为当前会话。

        Args:
            title: 可选标题；未提供时自动按序号生成默认标题。

        Returns:
            新创建的 `ChatSession` 实例。
        """
        if title is None:
            used_indexes: set[int] = set()
            for session in self.sessions:
                raw_title = session.title.strip()
                if not raw_title.startswith("Chat "):
                    continue
                suffix = raw_title[5:].strip()
                if suffix.isdigit():
                    used_indexes.add(int(suffix))

            session_index = 1
            while session_index in used_indexes:
                session_index += 1
            final_title = f"Chat {session_index}"
        else:
            final_title = title

        session = ChatSession(title=final_title)
        self.sessions.append(session)
        self.active_session_id = session.session_id
        return session

    def switch_session(self, session_id: str) -> None:
        """切换当前会话 ID。

        Args:
            session_id: 目标会话的唯一标识。

        调用方应保证目标会话存在；本方法仅更新指针。
        """
        self.active_session_id = session_id

    def rename_session(self, session_id: str, title: str) -> None:
        """重命名指定会话。

        Args:
            session_id: 待重命名会话 ID。
            title: 新会话标题。

        命中目标后立即更新并结束循环。
        """
        for session in self.sessions:
            if session.session_id == session_id:
                session.title = title
                break

    def delete_session(self, session_id: str) -> None:
        """删除指定会话并维护会话有效性。

        删除后若无会话，将自动创建默认会话作为兜底。
        若当前激活会话被删除，则自动切换到剩余列表首项。
        """
        self.sessions = [session for session in self.sessions if session.session_id != session_id]
        if not self.sessions:
            fallback = ChatSession(title="Default Chat")
            self.sessions = [fallback]
            self.active_session_id = fallback.session_id
            return
        if all(session.session_id != self.active_session_id for session in self.sessions):
            self.active_session_id = self.sessions[0].session_id

    def append_message(self, role: str, content: str) -> ChatMessage:
        """向当前会话追加一条消息。

        Args:
            role: 消息角色，例如 `user`、`assistant`、`tool`。
            content: 消息正文。

        Returns:
            新增的消息对象，便于后续增量更新。
        """
        token_count = ContextCompressor.estimate_message_tokens_from_content(content)
        message = ChatMessage(role=role, content=content, token_count=token_count)
        self.active_session.messages.append(message)
        self._refresh_session_token_cache(self.active_session, message_index=-1)
        return message

    def _refresh_session_token_cache(
        self,
        session: ChatSession,
        message_index: Optional[int] = None,
    ) -> None:
        """按当前内容刷新会话内消息 token 与总估算缓存。

        Args:
            session: 待刷新的会话对象。
            message_index: 指定仅刷新某一条消息；None 表示全量刷新。
        """
        if message_index is None:
            for message in session.messages:
                message.token_count = ContextCompressor.estimate_message_tokens_from_content(
                    message.content
                )
        elif session.messages:
            normalized_index = message_index
            if normalized_index < 0:
                normalized_index += len(session.messages)
            if 0 <= normalized_index < len(session.messages):
                target_message = session.messages[normalized_index]
                target_message.token_count = ContextCompressor.estimate_message_tokens_from_content(
                    target_message.content
                )

        session.token_estimate = sum(message.token_count for message in session.messages)
        if session.summary:
            session.token_estimate += ContextCompressor.estimate_text_tokens_static(
                session.summary
            )

    def sync_session_tokens(
        self,
        session_id: str,
        message_index: Optional[int] = None,
    ) -> None:
        """按消息当前内容刷新会话 token 缓存。

        Args:
            session_id: 目标会话 ID。
            message_index: 指定仅刷新某一条消息；None 表示全量刷新。
        """
        target = self.get_session_by_id(session_id)
        if target is None:
            return
        self._refresh_session_token_cache(target, message_index)

    def update_tool(self, key: str, enabled: bool) -> None:
        """更新工具开关状态。

        Args:
            key: 工具唯一键。
            enabled: 目标启用状态。

        仅在工具存在时进行更新。
        """
        if key in self.tools:
            self.tools[key].enabled = enabled

    def set_mirror(self, mirror: str) -> None:
        """切换模型镜像并同步基础地址。

        Args:
            mirror: 镜像预设名称。

        当镜像预设中存在 URL 时，会同时更新 `base_url`。
        """
        self.model_config.mirror = mirror
        if mirror in MIRROR_PRESETS and MIRROR_PRESETS[mirror]:
            self.model_config.base_url = MIRROR_PRESETS[mirror]

    def init_skills(self, analyzer: Any) -> None:
        """Initialize skills system.

        Args:
            analyzer: CodeAnalyzer instance from app.py
        """
        self._analyzer = analyzer

        # Create registry and audit logger
        self.skill_registry = SkillRegistry(self.workspace_root)
        self._audit_logger = AuditLogger(self.workspace_root)
        self._audit_logger.load()

        # Register builtin skills
        for manifest in BUILTIN_MANIFESTS:
            self.skill_registry.register_skill(manifest)

        # Register built-in Codex-style agent skills
        for manifest in BUILTIN_AGENT_MANIFESTS:
            self.skill_registry.register_skill(manifest)

        # Load Codex-style agent skills from .nju_code/skills/*/SKILL.md
        self.skill_registry.load_agent_skills()

        # Load plugins
        self.skill_registry.load_plugins()

        # Load saved state
        self.skill_registry.load()

        # Create permission checker and executor
        self.skills = self.skill_registry.skills

    def build_agent_skill_context(self, query: str, max_skills: int = 3) -> str:
        """Return selected agent skill instructions for model context."""
        if not self.skill_registry:
            return ""

        selected = self.skill_registry.select_agent_skills(query, max_skills=max_skills)
        if not selected:
            return ""

        sections: list[str] = [
            "The following Agent Skills were selected for this user request. "
            "Use them as procedural guidance, and call tools only when needed."
        ]
        for toggle in selected:
            manifest = toggle.manifest
            instructions = manifest.instructions
            if not instructions and manifest.instructions_path:
                try:
                    instructions = Path(manifest.instructions_path).read_text(
                        encoding="utf-8", errors="ignore"
                    )
                except OSError:
                    instructions = ""
            sections.append(
                f"## {manifest.name}\n"
                f"Description: {manifest.description}\n"
                f"Source: {manifest.instructions_path or 'inline'}\n\n"
                f"{instructions[:6000]}"
            )
        return "\n\n".join(sections)

    def execute_skill(
        self,
        skill_id: str,
        params: Dict[str, Any],
    ) -> Any:
        """Execute a skill by ID.

        Args:
            skill_id: Skill to execute
            params: Input parameters

        Returns:
            Skill output dict
        """
        if not self._skill_executor and self._analyzer:
            permission_checker = PermissionChecker(self.tools, self.skills)
            self._skill_executor = SkillExecutor(
                self.skill_registry,
                permission_checker,
                self._audit_logger,
                self._analyzer,
            )

        if not self._skill_executor:
            return {"type": "error", "error": "Skills system not initialized"}

        result = self._skill_executor.execute(
            skill_id,
            params,
            self.active_session_id,
            {"workspace_root": self.workspace_root},
        )
        return result.output if result.success else {"type": "error", "error": result.log.error_message}

    def execute_skill_command(self, command: str) -> Any:
        """Execute skill by command alias.

        Args:
            command: Command string like "/scan", "/search keyword"

        Returns:
            Skill output dict
        """
        if not self._skill_executor and self._analyzer:
            permission_checker = PermissionChecker(self.tools, self.skills)
            self._skill_executor = SkillExecutor(
                self.skill_registry,
                permission_checker,
                self._audit_logger,
                self._analyzer,
            )

        if not self._skill_executor:
            return {"type": "error", "error": "Skills system not initialized"}

        result = self._skill_executor.execute_by_command(
            command,
            self.active_session_id,
            {"workspace_root": self.workspace_root},
        )
        return result.output if result.success else {"type": "error", "error": result.log.error_message}

    def update_skill(self, skill_id: str, enabled: bool) -> None:
        """Update skill enabled status.

        Args:
            skill_id: Skill ID
            enabled: New enabled state
        """
        if self.skill_registry:
            self.skill_registry.update_skill_status(skill_id, enabled)

    def init_mcp(self) -> None:
        """Initialize MCP system.

        Creates MCPManager and loads server configurations.
        Async connection happens in app.py after Textual is ready.
        """
        self.mcp_manager = MCPManager(self.workspace_root)
        self.mcp_manager.load()
        self.mcp_tools = self.mcp_manager.tool_toggles

    def execute_mcp_tool(
        self,
        skill_id: str,
        params: Dict[str, Any],
    ) -> Any:
        """Execute an MCP tool synchronously.

        Args:
            skill_id: MCP tool skill_id (e.g., "mcp.filesystem.read_file")
            params: Tool input arguments

        Returns:
            Tool output dict
        """
        if not self._mcp_executor and self.mcp_manager and self._audit_logger:
            self._mcp_executor = MCPToolExecutor(
                self.mcp_manager,
                self._audit_logger,
            )

        if not self._mcp_executor:
            return {"type": "error", "error": "MCP system not initialized"}

        result = self._mcp_executor.execute_sync(
            skill_id,
            params,
            self.active_session_id,
            {"workspace_root": str(self.workspace_root)},
        )
        return result.output if result.success else {"type": "error", "error": result.log.error_message}

    def update_mcp_tool(self, skill_id: str, enabled: bool) -> None:
        """Update MCP tool enabled status.

        Args:
            skill_id: MCP tool skill_id
            enabled: New enabled state
        """
        if self.mcp_manager:
            self.mcp_manager.update_tool_status(skill_id, enabled)

    def init_patch_engine(self) -> None:
        """Initialize the Patch/Rollback engine and register it with builtin skills."""
        self._patch_history_store = PatchHistoryStore(self.workspace_root)
        self._patch_history_store.load()

        self._patch_engine = PatchEngine(
            workspace_root=self.workspace_root,
            history_store=self._patch_history_store,
            audit_logger=self._audit_logger,
        )

        # Register engine reference so builtin patch skill executors can use it
        from .skills.builtin import set_patch_engine
        set_patch_engine(self._patch_engine)

    def create_patch(
        self,
        file_changes: Dict[str, tuple],
        description: str = "",
        is_ai_generated: bool = False,
        reviewer: Optional[str] = None,
    ) -> Optional[PatchTask]:
        """Create a new PatchTask from file changes.

        Args:
            file_changes: {relative_path: (old_content, new_content)}
            description: Human-readable description of the change
            is_ai_generated: True if the change was produced by the LLM
            reviewer: Name of the human reviewer (for FR-10)

        Returns:
            PatchTask on success, None if engine not initialized.
        """
        if not self._patch_engine:
            return None
        return self._patch_engine.generate_patch(
            file_changes=file_changes,
            description=description,
            session_id=self.active_session_id,
            is_ai_generated=is_ai_generated,
            reviewer=reviewer,
        )

    def preview_patch(self, task_id: str) -> str:
        """Return formatted diff preview for a patch task."""
        if not self._patch_engine:
            return "Patch engine not initialized."
        task = self._patch_history_store.get_task(task_id) if self._patch_history_store else None
        if not task:
            return f"Task not found: {task_id}"
        return self._patch_engine.preview_patch(task)

    def confirm_patch(self, task_id: str) -> tuple:
        """Mark a patch task as confirmed (ready to apply)."""
        if not self._patch_engine:
            return False, "Patch engine not initialized."
        return self._patch_engine.confirm_patch(task_id)

    def apply_patch(
        self,
        task_id: str,
    ) -> Optional[PatchExecutionResult]:
        """Apply a confirmed patch task.

        Args:
            task_id: ID of the PatchTask to apply

        Returns:
            PatchExecutionResult, or None if engine/task not found.
        """
        if not self._patch_engine or not self._patch_history_store:
            return None
        task = self._patch_history_store.get_task(task_id)
        if not task:
            return None
        return self._patch_engine.apply_patch(task)

    def rollback_patch(self, task_id: str) -> Optional[PatchExecutionResult]:
        """Rollback an applied patch task.

        Args:
            task_id: ID of the PatchTask to rollback

        Returns:
            PatchExecutionResult, or None if engine not initialized.
        """
        if not self._patch_engine:
            return None
        return self._patch_engine.rollback_patch(task_id)

    def cancel_patch(self, task_id: str) -> tuple:
        """Cancel a pending patch task."""
        if not self._patch_engine:
            return False, "Patch engine not initialized."
        return self._patch_engine.cancel_patch(task_id)

    def get_pending_patches(self) -> List[PatchTask]:
        """Return all pending/previewed/confirmed patch tasks."""
        if not self._patch_engine:
            return []
        return self._patch_engine.get_pending_tasks()

    def get_patch_history(self, limit: int = 20) -> List[PatchTask]:
        """Return patch history, newest first."""
        if not self._patch_engine:
            return []
        return self._patch_engine.get_history(limit=limit)


    def get_token_estimate(self, session_id: str) -> int:
        """返回指定会话的 token 估算值。

        Args:
            session_id: 目标会话 ID。

        Returns:
            已缓存的 token_estimate；若会话不存在返回 0。
        """
        for session in self.sessions:
            if session.session_id == session_id:
                return session.token_estimate
        return 0

    def get_session_by_id(self, session_id: str) -> Optional[ChatSession]:
        """根据 ID 查找会话对象。

        Args:
            session_id: 目标会话 ID。

        Returns:
            找到的 ChatSession；不存在时返回 None。
        """
        for session in self.sessions:
            if session.session_id == session_id:
                return session
        return None

    def get_session_stats(self, session_id: str) -> Dict[str, Any]:
        """返回指定会话的详细统计信息字典。

        统计项包括：
        - message_count: 消息总数
        - user_message_count: 用户消息数
        - assistant_message_count: 助手消息数
        - token_estimate: token 估算值
        - compression_count: 历史压缩次数
        - has_summary: 是否有已生成摘要
        - summary_length: 摘要字符数
        - compressed_at_str: 最近压缩时间的格式化字符串（无则为空）
        - first_message_at: 最早消息时间（ISO 格式，无消息则为空）
        - last_message_at: 最新消息时间（ISO 格式，无消息则为空）
        - avg_message_length: 消息平均字符数（含双端，无消息则为 0）

        Args:
            session_id: 目标会话 ID。

        Returns:
            统计字典；会话不存在时返回空字典。
        """
        target = self.get_session_by_id(session_id)
        if target is None:
            return {}

        user_msgs = [m for m in target.messages if m.role == "user"]
        assistant_msgs = [m for m in target.messages if m.role == "assistant"]
        all_lengths = [len(m.content) for m in target.messages if m.content]
        avg_len = int(sum(all_lengths) / len(all_lengths)) if all_lengths else 0

        first_at = ""
        last_at = ""
        if target.messages:
            first_at = target.messages[0].created_at.isoformat()
            last_at = target.messages[-1].created_at.isoformat()

        return {
            "message_count": len(target.messages),
            "user_message_count": len(user_msgs),
            "assistant_message_count": len(assistant_msgs),
            "token_estimate": target.token_estimate,
            "compression_count": target.compression_count,
            "has_summary": bool(target.summary),
            "summary_length": len(target.summary),
            "compressed_at_str": (
                target.compressed_at.strftime("%Y-%m-%d %H:%M")
                if target.compressed_at
                else ""
            ),
            "first_message_at": first_at,
            "last_message_at": last_at,
            "avg_message_length": avg_len,
        }

    def recalculate_token_estimates(self, compressor: "ContextCompressor") -> None:
        """重新计算所有会话的 token 估算值并刷新缓存。

        在加载设置后或压缩策略变化后调用，确保 token_estimate 与
        实际消息内容保持一致（旧版本保存的估算值可能使用旧算法）。

        Args:
            compressor: 压缩器实例，用于调用最新的 estimate_tokens 方法。
        """
        for session in self.sessions:
            for message in session.messages:
                message.token_count = compressor.estimate_message_tokens(message)
            calculated = sum(message.token_count for message in session.messages)
            if session.summary:
                calculated += compressor.estimate_text_tokens(session.summary)
            session.token_estimate = calculated

    def auto_title_session(self, session_id: str, max_chars: int = 30) -> bool:
        """从会话第一条用户消息自动生成简短标题。

        仅在以下条件同时满足时执行：
        1. 会话存在且至少有一条用户消息
        2. 当前标题仍为默认格式（'New Chat' 或 'Chat N'）

        从第一条用户消息内容中取前 max_chars 个字符作为标题，
        去除首尾空白和换行符，以 '…' 截断过长内容。

        Args:
            session_id: 目标会话 ID。
            max_chars: 标题最大字符数，默认 30。

        Returns:
            True 表示标题已更新；False 表示未满足条件或无需更新。
        """
        target = self.get_session_by_id(session_id)
        if target is None:
            return False

        current = target.title.strip()
        # 仅对默认标题（New Chat 或 Chat N）执行自动命名
        is_default = current == "New Chat" or (
            current.startswith("Chat ") and current[5:].strip().isdigit()
        )
        if not is_default:
            return False

        user_messages = [m for m in target.messages if m.role == "user" and m.content.strip()]
        if not user_messages:
            return False

        raw = user_messages[0].content.strip().replace("\n", " ")
        # 去掉以 '/' 开头的命令消息（分析命令不应作为标题）
        if raw.startswith("/"):
            return False

        title = raw[:max_chars]
        if len(raw) > max_chars:
            title = title.rstrip() + "…"
        target.title = title
        return True

    def prune_empty_sessions(self) -> int:
        """清除没有任何消息的会话（默认会话除外）。

        若清除后会话列表为空，保留当前激活会话（即使是空会话）。
        若激活会话被清除，自动切换到剩余列表中第一项。

        Returns:
            被清除的空会话数量。
        """
        if len(self.sessions) <= 1:
            return 0

        active_id = self.active_session_id
        non_empty = [
            s for s in self.sessions
            if s.messages or s.session_id == active_id
        ]
        removed = len(self.sessions) - len(non_empty)
        if removed > 0:
            self.sessions = non_empty if non_empty else self.sessions[:1]
            if not any(s.session_id == self.active_session_id for s in self.sessions):
                self.active_session_id = self.sessions[0].session_id
        return removed

    def clone_session(self, session_id: str) -> Optional[ChatSession]:
        """克隆一个会话（复制所有消息和摘要，生成新 ID）。

        克隆的会话会被追加到会话列表末尾，但不会自动切换激活。

        Args:
            session_id: 待克隆的会话 ID。

        Returns:
            新创建的 ChatSession 克隆；原会话不存在时返回 None。
        """
        source = self.get_session_by_id(session_id)
        if source is None:
            return None

        from copy import deepcopy
        cloned = ChatSession(
            title=f"{source.title} (副本)",
            messages=deepcopy(source.messages),
            summary=source.summary,
            compressed_at=source.compressed_at,
            token_estimate=source.token_estimate,
            interrupted=False,
            interrupted_context=None,
            compression_count=source.compression_count,
            last_compressed_token_count=source.last_compressed_token_count,
        )
        self.sessions.append(cloned)
        return cloned

    def compress_session(
        self, session_id: str, compressor: "ContextCompressor"
    ) -> Optional[str]:
        """对指定会话执行上下文压缩。

        调用 ContextCompressor 生成摘要，裁剪旧消息，
        并将摘要、压缩时间及统计字段写回会话对象。
        支持增量压缩：若会话已有摘要，传入旧摘要以生成合并摘要。

        Args:
            session_id: 目标会话 ID。
            compressor: 已配置好模型参数的压缩器实例。

        Returns:
            压缩成功时返回生成的摘要文本；会话不存在或无需压缩时返回 None。
        """
        target = self.get_session_by_id(session_id)
        if target is None or not target.messages:
            return None

        # 传入已有摘要实现增量压缩，传入会话标题优化摘要质量
        result = compressor.compress(
            target.messages,
            existing_summary=target.summary or None,
            session_title=target.title,
        )
        if result.removed_count == 0:
            return None

        target.last_compressed_token_count = result.token_before
        target.messages = result.kept_messages
        target.summary = result.summary
        target.compressed_at = result.generated_at
        target.compression_count += 1
        self._refresh_session_token_cache(target)
        return result.summary

    def auto_compress_if_needed(self, compressor: "ContextCompressor") -> bool:
        """若当前激活会话超过 token 阈值，自动执行压缩。

        已在最近一次保存后被压缩过且 token 未再次超限的会话不会重复压缩，
        避免频繁触发对同一批消息的重复摘要请求。

        Args:
            compressor: 压缩器实例。

        Returns:
            True 表示本次执行了压缩；False 表示未触发。
        """
        session = self.active_session
        if not compressor.needs_compression(session.messages):
            return False
        summary = self.compress_session(session.session_id, compressor)
        return summary is not None

    def export_session(self, session_id: str, path: "Path") -> None:
        """将指定会话导出为 JSON 文件。

        Args:
            session_id: 目标会话 ID。
            path: 导出目标文件路径。

        Raises:
            ValueError: 会话不存在时抛出。
        """
        target: Optional[ChatSession] = None
        for session in self.sessions:
            if session.session_id == session_id:
                target = session
                break
        if target is None:
            raise ValueError(f"会话不存在: {session_id}")

        session_data = {
            "session_id": target.session_id,
            "title": target.title,
            "summary": target.summary,
            "compressed_at": target.compressed_at.isoformat() if target.compressed_at else None,
            "token_estimate": target.token_estimate,
            "interrupted": target.interrupted,
            "interrupted_context": target.interrupted_context,
            "compression_count": target.compression_count,
            "last_compressed_token_count": target.last_compressed_token_count,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat(),
                    "token_count": m.token_count,
                }
                for m in target.messages
            ],
        }
        self.settings_store.export_session_file(session_data, path)

    def import_session(self, path: "Path") -> ChatSession:
        """从 JSON 文件恢复会话并追加到会话列表。

        Args:
            path: 会话 JSON 文件路径。

        Returns:
            导入后的 ChatSession 对象。

        Raises:
            ValueError: 文件格式非法时抛出（由 SettingsStore 校验）。
        """
        data = self.settings_store.import_session_file(path)
        messages: List[ChatMessage] = []
        for msg_data in data.get("messages", []):
            timestamp = msg_data.get("created_at", "")
            try:
                created_at = datetime.fromisoformat(timestamp)
            except Exception:
                created_at = datetime.now()
            messages.append(
                ChatMessage(
                    role=msg_data.get("role", "user"),
                    content=msg_data.get("content", ""),
                    created_at=created_at,
                    token_count=int(msg_data.get("token_count", 0)),
                )
            )

        compressed_at_raw = data.get("compressed_at")
        compressed_at: Optional[datetime] = None
        if compressed_at_raw:
            try:
                compressed_at = datetime.fromisoformat(compressed_at_raw)
            except Exception:
                compressed_at = None

        session = ChatSession(
            session_id=str(uuid4()),  # 重新生成 ID 避免冲突
            title=data.get("title", "Imported Chat"),
            messages=messages,
            summary=data.get("summary", ""),
            compressed_at=compressed_at,
            token_estimate=int(data.get("token_estimate", 0)),
            interrupted=False,
            interrupted_context=None,
            compression_count=int(data.get("compression_count", 0)),
            last_compressed_token_count=int(data.get("last_compressed_token_count", 0)),
        )
        # 防止重复导入：若已有同标题同消息数的会话则不追加
        for existing in self.sessions:
            if (
                existing.title == session.title
                and len(existing.messages) == len(session.messages)
                and existing.summary == session.summary
            ):
                return existing
        self._refresh_session_token_cache(session)
        self.sessions.append(session)
        return session

    def mark_interrupted(self, session_id: str, content: str) -> None:
        """标记会话为中断恢复状态，并保存待发送内容。

        Args:
            session_id: 目标会话 ID。
            content: 用户当时输入但未发送成功的内容。
        """
        for session in self.sessions:
            if session.session_id == session_id:
                session.interrupted = True
                session.interrupted_context = content
                break

    def clear_interrupted(self, session_id: str) -> None:
        """清除会话的中断恢复标记。

        Args:
            session_id: 目标会话 ID。
        """
        for session in self.sessions:
            if session.session_id == session_id:
                session.interrupted = False
                session.interrupted_context = None
                break

    def build_context_messages(self, session_id: str) -> List[Dict[str, str]]:
        """构建发送给模型的消息列表，正确处理摘要前缀和角色过滤。

        若会话存在已压缩摘要，则在首部插入一条 system 消息包含摘要内容，
        后续跟上当前保留的消息列表，确保模型了解历史背景。
        同时过滤掉仅在 UI 中展示的内部角色（'summary'、'compressed'），
        以及内容为空的占位消息（除最后一条 assistant 消息外）。

        Args:
            session_id: 目标会话 ID。

        Returns:
            可直接传入 OpenAIRequest.messages 的字典列表。
        """
        target = self.get_session_by_id(session_id)
        if target is None:
            return []

        result: List[Dict[str, str]] = []
        if target.summary:
            result.append(
                {
                    "role": "system",
                    "content": (
                        "以下是本次会话的历史对话摘要，请在后续回复中以此为背景参考：\n"
                        f"{target.summary}"
                    ),
                }
            )

        valid_roles = {"user", "assistant", "system", "tool"}
        msgs = target.messages
        for i, m in enumerate(msgs):
            # 过滤 UI 专用角色
            if m.role.lower() not in valid_roles:
                continue
            # 允许最后一条 assistant 消息为空（流式占位），其余空内容跳过
            is_last = i == len(msgs) - 1
            if not m.content.strip() and not (is_last and m.role == "assistant"):
                continue
            result.append({"role": m.role, "content": m.content})
        return result
