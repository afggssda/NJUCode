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
        token_count = max(1, len(content) // 3) + 4
        message = ChatMessage(role=role, content=content, token_count=token_count)
        self.active_session.messages.append(message)
        self.active_session.token_estimate = sum(
            m.token_count for m in self.active_session.messages
        )
        return message

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

    def compress_session(
        self, session_id: str, compressor: "ContextCompressor"
    ) -> Optional[str]:
        """对指定会话执行上下文压缩。

        调用 ContextCompressor 生成摘要，裁剪旧消息，
        并将摘要和压缩时间写回会话对象。

        Args:
            session_id: 目标会话 ID。
            compressor: 已配置好模型参数的压缩器实例。

        Returns:
            压缩成功时返回生成的摘要文本；会话不存在或无需压缩时返回 None。
        """
        target: Optional[ChatSession] = None
        for session in self.sessions:
            if session.session_id == session_id:
                target = session
                break
        if target is None or not target.messages:
            return None

        result = compressor.compress(target.messages)
        if result.removed_count == 0:
            return None

        target.messages = result.kept_messages
        target.summary = result.summary
        target.compressed_at = datetime.now()
        target.token_estimate = result.token_after
        return result.summary

    def auto_compress_if_needed(self, compressor: "ContextCompressor") -> bool:
        """若当前激活会话超过 token 阈值，自动执行压缩。

        Args:
            compressor: 压缩器实例。

        Returns:
            True 表示本次执行了压缩；False 表示未触发。
        """
        session = self.active_session
        if not compressor.needs_compression(session.messages):
            return False
        self.compress_session(session.session_id, compressor)
        return True

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
        )
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
        """构建发送给模型的消息列表。

        若会话存在已压缩摘要，则在首部插入一条 system 消息包含摘要内容，
        后续跟上当前保留的消息列表，确保模型了解历史背景。

        Args:
            session_id: 目标会话 ID。

        Returns:
            可直接传入 OpenAIRequest.messages 的字典列表。
        """
        target: Optional[ChatSession] = None
        for session in self.sessions:
            if session.session_id == session_id:
                target = session
                break
        if target is None:
            return []

        result: List[Dict[str, str]] = []
        if target.summary:
            result.append(
                {
                    "role": "system",
                    "content": f"【历史对话摘要】\n{target.summary}",
                }
            )
        result.extend({"role": m.role, "content": m.content} for m in target.messages)
        return result
