from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import os
from pathlib import Path
from typing import Dict, List
from uuid import uuid4

from .models import ChatMessage, ChatSession, DEFAULT_TOOLS, MIRROR_PRESETS, ModelConfig, ToolToggle
from .services.settings_store import SettingsStore


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
                    )
                )

            loaded_sessions.append(
                ChatSession(
                    session_id=session_data.get("session_id", "") or str(uuid4()),
                    title=session_data.get("title", "New Chat"),
                    messages=messages,
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
        """
        payload = {
            "model": asdict(self.model_config),
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
                    "messages": [
                        {
                            "role": message.role,
                            "content": message.content,
                            "created_at": message.created_at.isoformat(),
                        }
                        for message in session.messages
                    ],
                }
                for session in self.sessions
            ],
        }
        self.settings_store.save(payload)

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
        message = ChatMessage(role=role, content=content)
        self.active_session.messages.append(message)
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
