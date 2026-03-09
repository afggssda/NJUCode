from __future__ import annotations

from pathlib import Path

from textual import work
from textual.containers import Vertical
from textual.widgets import DirectoryTree, Label
from textual.widgets._tree import TreeNode


class FileTreePanel(Vertical):
    def __init__(self, workspace_root: Path, **kwargs):
        """初始化文件树面板。

        Args:
            workspace_root: 文件树根目录。

        同时初始化刷新互斥标记，避免定时任务重入。
        """
        super().__init__(**kwargs)
        self.workspace_root = workspace_root
        self._refresh_running = False

    def compose(self):
        """构建 Explorer 面板组件。"""
        yield Label("Explorer", classes="panel-title")
        yield DirectoryTree(str(self.workspace_root), id="workspace_tree")

    def on_mount(self) -> None:
        """组件挂载后启动周期刷新定时器。

        当前策略为每 10 秒尝试一次异步刷新，
        用于捕获外部文件系统变化。
        """
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
