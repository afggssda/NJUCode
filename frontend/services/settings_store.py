from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


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
