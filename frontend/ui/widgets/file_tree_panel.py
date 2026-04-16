from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from textual import work, on
from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widgets import DirectoryTree, Label, Button, Input
from textual.screen import ModalScreen
from textual.widgets._tree import TreeNode
from textual.message import Message


class WorkspaceChanged(Message):
    """工作区变更事件"""
    def __init__(self, new_path: Path):
        super().__init__()
        self.new_path = new_path


class OpenFolderScreen(ModalScreen[Path]):
    """输入路径切换工作区的弹窗"""

    def compose(self) -> ComposeResult:
        with Vertical(id="open_folder_dialog"):
            yield Label("输入工作区绝对路径：", classes="dialog-title")
            yield Input(placeholder="e.g. D:\\MyProject", id="folder_input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("确认", id="folder_ok", variant="success")
                yield Button("取消", id="folder_cancel")

    def on_mount(self) -> None:
        self.query_one("#folder_input", Input).focus()

    @on(Button.Pressed, "#folder_cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#folder_ok")
    def confirm(self) -> None:
        self._submit()

    @on(Input.Submitted, "#folder_input")
    def input_submit(self) -> None:
        self._submit()

    def _submit(self) -> None:
        val = self.query_one("#folder_input", Input).value.strip()
        if val:
            path = Path(val).resolve()
            if path.is_dir():
                self.dismiss(path)
            else:
                self.app.notify("无效的文件夹路径", severity="error")


class NewFileScreen(ModalScreen[str]):
    """输入需要创建的新文件/文件夹名称的弹窗"""
    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory

    def compose(self) -> ComposeResult:
        with Vertical(id="open_folder_dialog"):
            yield Label(f"在 {self.directory.name} 中新建：", classes="dialog-title")
            yield Input(placeholder="e.g. new_file.py 或 new_folder/", id="new_file_input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("确认", id="new_file_ok", variant="success")
                yield Button("取消", id="new_file_cancel")

    def on_mount(self) -> None:
        self.query_one("#new_file_input", Input).focus()

    @on(Button.Pressed, "#new_file_cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#new_file_ok")
    def confirm(self) -> None:
        self._submit()

    @on(Input.Submitted, "#new_file_input")
    def input_submit(self) -> None:
        self._submit()

    def _submit(self) -> None:
        val = self.query_one("#new_file_input", Input).value.strip()
        if val:
            self.dismiss(val)


class ConfirmDeleteFileScreen(ModalScreen[bool]):
    """确认删除文件弹窗"""
    def __init__(self, target: Path):
        super().__init__()
        self.target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm_dialog"):
            yield Label(f"Delete this item?\n\n{self.target.name}", id="confirm_title")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="btn_cancel")
                yield Button("Delete", id="btn_confirm", variant="error")

    @on(Button.Pressed, "#btn_cancel")
    def cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#btn_confirm")
    def confirm(self) -> None:
        self.dismiss(True)


class FileTreePanel(Vertical):
    BINDINGS = [
        ("n", "new_file", "New File/Dir"),
        ("delete", "delete_file", "Delete"),
        ("ctrl+z", "undo_delete", "Undo Delete"),
    ]

    def __init__(self, workspace_root: Path, **kwargs):
        """初始化文件树面板。

        Args:
            workspace_root: 文件树根目录。

        同时初始化刷新互斥标记，避免定时任务重入。
        """
        super().__init__(**kwargs)
        self.workspace_root = workspace_root
        self._refresh_running = False
        self._trash_dir = self.workspace_root / ".nju_code" / "trash"
        self._deleted_history: list[tuple[Path, Path]] = []

    def compose(self):
        """构建 Explorer 面板组件。"""
        with Horizontal(id="explorer_title_row"):
            yield Button("Open Folder", id="btn_open_folder", variant="primary", classes="small_btn")
            yield Button("Undo Delete", id="btn_undo_delete", classes="small_btn", disabled=True)
        yield DirectoryTree(str(self.workspace_root), id="workspace_tree")

    @on(Button.Pressed, "#btn_open_folder")
    def on_open_folder(self) -> None:
        def check_reply(new_path: Path | None) -> None:
            if new_path:
                self.workspace_root = new_path
                self._trash_dir = self.workspace_root / ".nju_code" / "trash"
                self._deleted_history = []
                tree = self.query_one("#workspace_tree", DirectoryTree)
                tree.path = str(new_path)
                self._update_undo_button()
                self.post_message(WorkspaceChanged(new_path))
                self.app.notify(f"Workspace changed: {new_path}")
        self.app.push_screen(OpenFolderScreen(), check_reply)

    def _update_undo_button(self) -> None:
        button = self.query_one("#btn_undo_delete", Button)
        while self._deleted_history and not self._deleted_history[-1][1].exists():
            self._deleted_history.pop()
        button.disabled = not self._deleted_history

    def _move_to_trash(self, path: Path) -> None:
        self._trash_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_path = self._trash_dir / f"{stamp}_{path.name}"
        shutil.move(str(path), str(backup_path))
        self._deleted_history.append((path, backup_path))
        self._update_undo_button()

    def action_undo_delete(self) -> None:
        self._update_undo_button()
        if not self._deleted_history:
            self.app.notify("Tip: No delete action to undo", severity="warning")
            return

        original_path, backup_path = self._deleted_history.pop()
        if not backup_path.exists():
            self._update_undo_button()
            self.app.notify("Tip: Undo failed, backup no longer exists", severity="error")
            return
        if original_path.exists():
            self._deleted_history.append((original_path, backup_path))
            self._update_undo_button()
            self.app.notify(
                f"Tip: Undo failed, {original_path.name} already exists",
                severity="error",
            )
            return

        try:
            original_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup_path), str(original_path))
            restored_name = original_path.name
            self._update_undo_button()
            self._refresh_tree()
            self.app.notify(f"Tip: Restored {restored_name}")
        except Exception as error:
            self._deleted_history.append((original_path, backup_path))
            self._update_undo_button()
            self.app.notify(f"Tip: Undo failed: {error}", severity="error")

    def action_new_file(self) -> None:
        """在新选中的目录下或同级新建文件/文件夹。"""
        tree = self.query_one("#workspace_tree", DirectoryTree)
        node = tree.cursor_node
        if node is None:
            target_dir = self.workspace_root
        else:
            data = getattr(node, "data", None)
            path = getattr(data, "path", None)
            if path is None:
                target_dir = self.workspace_root
            elif path.is_dir():
                target_dir = path
            else:
                target_dir = path.parent

        def check_reply(name: str | None) -> None:
            if name:
                new_path = target_dir / name
                try:
                    if name.endswith("/") or name.endswith("\\"):
                        new_path.mkdir(parents=True, exist_ok=True)
                    else:
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        new_path.touch(exist_ok=True)
                    self._refresh_tree()
                    self.app.notify(f"Created: {new_path.name}")
                except Exception as e:
                    self.app.notify(f"Create failed: {e}", severity="error")

        self.app.push_screen(NewFileScreen(target_dir), check_reply)

    def action_delete_file(self) -> None:
        """删除当前选中的文件/文件夹。"""
        tree = self.query_one("#workspace_tree", DirectoryTree)
        node = tree.cursor_node
        if node is None:
            return
            
        data = getattr(node, "data", None)
        path = getattr(data, "path", None)
        if path is None or path == self.workspace_root:
            return

        def check_reply(confirm: bool) -> None:
            if confirm:
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    self._refresh_tree()
                    self.app.notify(f"Deleted: {path.name}")
                except Exception as e:
                    self.app.notify(f"Delete failed: {e}", severity="error")

        self.app.push_screen(ConfirmDeleteFileScreen(path), check_reply)

    def action_new_file(self) -> None:
        tree = self.query_one("#workspace_tree", DirectoryTree)
        node = tree.cursor_node
        if node is None:
            target_dir = self.workspace_root
        else:
            data = getattr(node, "data", None)
            path = getattr(data, "path", None)
            if path is None:
                target_dir = self.workspace_root
            elif path.is_dir():
                target_dir = path
            else:
                target_dir = path.parent

        def check_reply(name: str | None) -> None:
            if name:
                new_path = target_dir / name
                try:
                    if name.endswith("/") or name.endswith("\\"):
                        new_path.mkdir(parents=True, exist_ok=True)
                    else:
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        new_path.touch(exist_ok=True)
                    self._refresh_tree()
                    self.app.notify(f"Created: {new_path.name}")
                except Exception as error:
                    self.app.notify(f"Create failed: {error}", severity="error")

        self.app.push_screen(NewFileScreen(target_dir), check_reply)

    def action_delete_file(self) -> None:
        tree = self.query_one("#workspace_tree", DirectoryTree)
        node = tree.cursor_node
        if node is None:
            return

        data = getattr(node, "data", None)
        path = getattr(data, "path", None)
        if path is None or path == self.workspace_root:
            return

        def check_reply(confirm: bool) -> None:
            if confirm:
                try:
                    self._move_to_trash(path)
                    self._refresh_tree()
                    self.app.notify(f"Deleted: {path.name}. Click Undo Delete to restore")
                except Exception as error:
                    self.app.notify(f"Delete failed: {error}", severity="error")

        self.app.push_screen(ConfirmDeleteFileScreen(path), check_reply)

    @on(Button.Pressed, "#btn_undo_delete")
    def on_undo_delete(self) -> None:
        self.action_undo_delete()

    def on_mount(self) -> None:
        """组件挂载后启动周期刷新定时器。

        当前策略为每 10 秒尝试一次异步刷新，
        用于捕获外部文件系统变化。
        """
        self._update_undo_button()
        self.set_interval(10, self._schedule_refresh)

    def _schedule_refresh(self) -> None:
        """调度一次刷新任务并控制重入。

        若上一次刷新尚未完成，则直接跳过，
        以避免并发刷新导致树状态错乱。
        """
        if self._refresh_running:
            return
        self._refresh_running = True
        self._refresh_tree()

    def _collect_expanded_paths(self, node: TreeNode) -> list[str]:
        """递归收集当前已展开节点路径。

        Args:
            node: 遍历起点节点。

        Returns:
            所有展开节点对应的路径字符串列表。
        """
        expanded: list[str] = []
        data = getattr(node, "data", None)
        path = getattr(data, "path", None)
        if path is not None and node.is_expanded:
            expanded.append(str(path))

        for child in node.children:
            expanded.extend(self._collect_expanded_paths(child))
        return expanded

    def _find_node_by_path(self, node: TreeNode, target_path: str) -> TreeNode | None:
        """在树中递归查找目标路径对应节点。

        Args:
            node: 当前递归节点。
            target_path: 目标路径字符串。

        Returns:
            命中时返回节点对象，否则返回 `None`。
        """
        data = getattr(node, "data", None)
        path = getattr(data, "path", None)
        if path is not None and str(path) == target_path:
            return node
        for child in node.children:
            result = self._find_node_by_path(child, target_path)
            if result is not None:
                return result
        return None

    @work(exclusive=True, thread=False)
    async def _refresh_tree(self) -> None:
        """异步刷新目录树并尽量恢复用户视图状态。

        刷新前保存展开路径和光标位置，
        刷新后按路径重新展开节点并恢复光标，
        保持用户浏览上下文稳定。
        """
        try:
            tree = self.query_one("#workspace_tree", DirectoryTree)

            expanded_paths = self._collect_expanded_paths(tree.root)
            cursor_path = None
            if tree.cursor_node is not None:
                cursor_data = getattr(tree.cursor_node, "data", None)
                cursor_raw_path = getattr(cursor_data, "path", None)
                if cursor_raw_path is not None:
                    cursor_path = str(cursor_raw_path)

            await tree.reload()

            expanded_paths_sorted = sorted(expanded_paths, key=len)
            for path in expanded_paths_sorted:
                node = self._find_node_by_path(tree.root, path)
                if node is not None and not node.is_expanded:
                    node.expand()
                    await tree.reload_node(node)

            if cursor_path:
                cursor_node = self._find_node_by_path(tree.root, cursor_path)
                if cursor_node is not None:
                    tree.move_cursor(cursor_node)
        finally:
            self._refresh_running = False
