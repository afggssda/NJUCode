from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import Static


class SplitterDragged(Message):
    def __init__(self, splitter_id: str, screen_x: int) -> None:
        """创建分割条拖拽事件。

        Args:
            splitter_id: 分割条标识（left/right）。
            screen_x: 当前鼠标在屏幕坐标系中的 X 位置。
        """
        self.splitter_id = splitter_id
        self.screen_x = screen_x
        super().__init__()


class SplitterDragEnded(Message):
    def __init__(self, splitter_id: str) -> None:
        """创建分割条拖拽结束事件。"""
        self.splitter_id = splitter_id
        super().__init__()


class VerticalSplitter(Static):
    def __init__(self, splitter_id: str, **kwargs):
        """初始化竖向分割条组件。

        Args:
            splitter_id: 分割条唯一名称。

        组件内部维护拖拽状态标记，用于在鼠标移动时判断是否发事件。
        """
        super().__init__(**kwargs)
        self.splitter_id = splitter_id
        self._dragging = False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """按下鼠标时开始拖拽并捕获鼠标。"""
        self._dragging = True
        self.capture_mouse()
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        """释放鼠标时结束拖拽并释放鼠标捕获。"""
        if self._dragging:
            self._dragging = False
            self.release_mouse()
            self.post_message(SplitterDragEnded(self.splitter_id))
            event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        """拖拽过程中持续上报当前位置。

        仅在 `_dragging` 为真时发送 `SplitterDragged` 事件，
        避免普通鼠标移动产生无效布局计算。
        """
        if not self._dragging:
            return
        self.post_message(SplitterDragged(self.splitter_id, event.screen_x))
        event.stop()
