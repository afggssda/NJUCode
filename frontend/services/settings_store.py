from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class SettingsStore:
    def __init__(self, workspace_root: Path) -> None:
        """初始化设置存储路径。

        Args:
            workspace_root: 工作区根目录。

        设置文件固定存放在 `.nju_code/settings.json`，
        便于与业务代码隔离并支持跨次启动恢复。
        """
        self.settings_dir = workspace_root / ".nju_code"
        self.settings_path = self.settings_dir / "settings.json"

    def load(self) -> Dict[str, Any]:
        """读取并解析本地设置 JSON。

        Returns:
            成功时返回字典对象；
            文件不存在或解析失败时返回空字典。

        该容错策略可以避免损坏配置导致应用启动失败。
        """
        if not self.settings_path.exists():
            return {}
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save(self, payload: Dict[str, Any]) -> None:
        """将设置字典写入本地 JSON 文件。

        Args:
            payload: 待保存的完整设置数据。

        保存前会自动创建父目录，并使用 UTF-8 + 缩进格式化输出，
        便于人工检查和版本对比。
        """
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def export_session_file(self, session_data: Dict[str, Any], path: Path) -> None:
        """将单个会话数据写入指定 JSON 文件。

        Args:
            session_data: 已序列化的会话字典（含 session_id、title、messages 等）。
            path: 目标文件路径，父目录不存在时自动创建。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def import_session_file(self, path: Path) -> Dict[str, Any]:
        """从指定 JSON 文件读取并校验会话数据。

        Args:
            path: 会话 JSON 文件路径。

        Returns:
            合法的会话字典；若文件不存在或格式非法则抛出 ValueError。

        校验规则：必须包含 session_id、title、messages 三个字段，
        且 messages 为列表类型，每条消息须含 role 与 content。
        """
        if not path.exists():
            raise ValueError(f"文件不存在: {path}")
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc

        if not isinstance(data, dict):
            raise ValueError("会话文件格式错误：根节点必须为 JSON 对象")

        required_keys: List[str] = ["session_id", "title", "messages"]
        for key in required_keys:
            if key not in data:
                raise ValueError(f"会话文件缺少必要字段: {key}")

        if not isinstance(data["messages"], list):
            raise ValueError("会话文件格式错误：messages 必须为列表")

        for i, msg in enumerate(data["messages"]):
            if not isinstance(msg, dict) or "role" not in msg or "content" not in msg:
                raise ValueError(f"第 {i} 条消息缺少 role 或 content 字段")

        return data
