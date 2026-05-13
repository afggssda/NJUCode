from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class SettingsStore:
    """应用设置持久化存储。

    负责设置文件的读写、备份、导出会话文件的管理，
    以及导入会话文件的格式校验。

    目录结构（位于 workspace_root/.nju_code/）：
    - settings.json          — 主设置文件
    - settings.backup.json   — 最近一次保存前的自动备份
    - exports/               — 会话导出 JSON 文件目录
    """

    def __init__(self, workspace_root: Path) -> None:
        """初始化设置存储路径。

        Args:
            workspace_root: 工作区根目录。

        设置文件固定存放在 `.nju_code/settings.json`，
        导出文件存放在 `.nju_code/exports/` 目录下，
        便于与业务代码隔离并支持跨次启动恢复。
        """
        self.settings_dir = workspace_root / ".nju_code"
        self.settings_path = self.settings_dir / "settings.json"
        self.backup_path = self.settings_dir / "settings.backup.json"
        self.exports_dir = self.settings_dir / "exports"

    # ------------------------------------------------------------------
    # 主设置文件读写
    # ------------------------------------------------------------------

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

        保存前会自动：
        1. 创建父目录（如不存在）。
        2. 若旧设置文件存在，先备份到 settings.backup.json。
        3. 将新内容以 UTF-8 + 缩进格式写入 settings.json。

        Args:
            payload: 待保存的完整设置数据。
        """
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        # 保存前备份旧文件（覆盖旧备份，只保留最近一次）
        if self.settings_path.exists():
            try:
                shutil.copy2(self.settings_path, self.backup_path)
            except OSError:
                pass  # 备份失败不阻断主流程
        self.settings_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def restore_from_backup(self) -> Dict[str, Any]:
        """从备份文件恢复设置。

        Returns:
            备份文件内容字典；备份不存在或损坏时返回空字典。
        """
        if not self.backup_path.exists():
            return {}
        try:
            return json.loads(self.backup_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def has_backup(self) -> bool:
        """返回是否存在设置备份文件。"""
        return self.backup_path.exists()

    # ------------------------------------------------------------------
    # 会话导出文件管理
    # ------------------------------------------------------------------

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
            合法的会话字典。

        Raises:
            ValueError: 文件不存在、格式非法或必要字段缺失时抛出。

        校验规则：
        - 根节点必须为 JSON 对象
        - 必须包含 session_id、title、messages 三个字段
        - messages 必须为列表，每条消息须含 role 与 content
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

    def list_export_files(self) -> List[Tuple[Path, datetime, str]]:
        """列出 exports 目录下所有会话导出文件，按修改时间倒序排列。

        Returns:
            列表中每项为 (文件路径, 修改时间, 会话标题) 的三元组。
            读取标题失败时会话标题为空字符串。
            若导出目录不存在则返回空列表。
        """
        if not self.exports_dir.exists():
            return []

        result: List[Tuple[Path, datetime, str]] = []
        for json_file in self.exports_dir.glob("session_*.json"):
            try:
                mtime = datetime.fromtimestamp(json_file.stat().st_mtime)
            except OSError:
                continue
            title = self._read_export_title(json_file)
            result.append((json_file, mtime, title))

        result.sort(key=lambda x: x[1], reverse=True)
        return result

    def _read_export_title(self, path: Path) -> str:
        """从导出文件中快速读取会话标题，不加载完整 messages。

        Args:
            path: 导出文件路径。

        Returns:
            会话标题字符串；读取失败时返回空字符串。
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return str(data.get("title", ""))
        except (json.JSONDecodeError, OSError):
            pass
        return ""

    def read_export_header(self, path: Path) -> Dict[str, Any]:
        """读取导出文件的元数据头（排除 messages 字段），用于轻量预览。

        Args:
            path: 导出文件路径。

        Returns:
            不含 messages 字段的字典；读取失败返回空字典。
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if k != "messages"}
        except (json.JSONDecodeError, OSError):
            pass
        return {}

    def cleanup_old_exports(self, keep_count: int = 20) -> int:
        """清理 exports 目录中多余的旧导出文件。

        按文件修改时间倒序排列，保留最新的 keep_count 个文件，
        删除其余文件。

        Args:
            keep_count: 最多保留的导出文件数量，默认 20。

        Returns:
            实际删除的文件数量。
        """
        if not self.exports_dir.exists():
            return 0

        all_files = sorted(
            self.exports_dir.glob("session_*.json"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )

        to_delete = all_files[keep_count:]
        deleted_count = 0
        for f in to_delete:
            try:
                f.unlink()
                deleted_count += 1
            except OSError:
                pass
        return deleted_count

    def get_exports_dir_size_bytes(self) -> int:
        """返回 exports 目录中所有 JSON 文件的总字节数。

        Returns:
            总字节数；目录不存在时返回 0。
        """
        if not self.exports_dir.exists():
            return 0
        total = 0
        for f in self.exports_dir.glob("session_*.json"):
            try:
                total += f.stat().st_size
            except OSError:
                pass
        return total
