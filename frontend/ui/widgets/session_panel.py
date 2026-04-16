from __future__ import annotations

from typing import Iterable

from textual import on
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView

from ...models import ChatSession


class SessionSelected(Message):
    def __init__(self, session_id: str) -> None:
        """创建会话选中事件。

        Args:
            session_id: 被选中的会话 ID。
        """
        self.session_id = session_id
        super().__init__()


class SessionCreateRequested(Message):
    pass


class SessionRenameRequested(Message):
    def __init__(self, session_id: str, title: str) -> None:
        """创建会话重命名请求事件。

        Args:
            session_id: 目标会话 ID。
            title: 新标题。
        """
        self.session_id = session_id
        self.title = title
        super().__init__()


class SessionDeleteRequested(Message):
    def __init__(self, session_id: str) -> None:
        """创建会话删除请求事件。

        Args:
            session_id: 待删除会话 ID。
        """
        self.session_id = session_id
        super().__init__()


class ConfirmDeleteScreen(ModalScreen[bool]):
    def __init__(self, title: str, message: str) -> None:
        """初始化确认弹窗内容。"""
        super().__init__()
        self.title = title
        self.message = message

    def compose(self):
        """渲染确认弹窗的主体内容与按钮。"""
        with Vertical(id="confirm_dialog"):
            yield Label(self.title, id="confirm_title")
            yield Label(self.message, id="confirm_message")
            with Horizontal(id="confirm_actions"):
                yield Button("Cancel", id="confirm_cancel")
                yield Button("Delete", id="confirm_ok", variant="error")

    @on(Button.Pressed, "#confirm_ok")
    def on_confirm(self) -> None:
        """用户确认删除。"""
        self.dismiss(True)

    @on(Button.Pressed, "#confirm_cancel")
    def on_cancel(self) -> None:
        """用户取消删除。"""
        self.dismiss(False)


class SessionPanel(Vertical):
    def __init__(self, **kwargs):
        """初始化会话管理面板。

        面板维护当前选中会话与删除确认状态，
        以支持重命名、删除二次确认等交互逻辑。
        """
        super().__init__(**kwargs)
        self.selected_session_id: str | None = None
        self.delete_confirm_session_id: str | None = None

    def compose(self):
        """构建会话列表与操作区。

        包含新建按钮、会话列表、重命名输入框，
        以及 Rename/Delete 操作按钮。
        """
        yield Label("Chats", classes="panel-title")
        yield Button("+ New Chat", id="new_chat_btn", variant="primary")
        yield ListView(id="session_list")
        yield Input(placeholder="重命名当前会话", id="rename_session_input")
        with Horizontal(id="session_action_row"):
            yield Button("Rename", id="rename_session_btn")
            yield Button("Delete", id="delete_session_btn", variant="error")

    @on(Button.Pressed, "#new_chat_btn")
    def on_new_chat(self) -> None:
        """处理新建会话按钮并发送创建事件。"""
        self.post_message(SessionCreateRequested())

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected) -> None:
        """处理会话列表选中变化。

        选中后会更新当前 session_id，重置删除确认状态，
        并将会话标题回填到重命名输入框，最后通知上层切换会话。
        """
        item = event.item
        if not item:
            return
        session_id = getattr(item, "session_id", "")
        if session_id:
            self.selected_session_id = session_id
            self.delete_confirm_session_id = None
            self._reset_delete_button()
            title = getattr(item, "session_title", "")
            if title:
                self.query_one("#rename_session_input", Input).value = title
            self.post_message(SessionSelected(session_id))

    @on(Button.Pressed, "#rename_session_btn")
    def on_rename_clicked(self) -> None:
        """处理重命名按钮点击并发出重命名请求。"""
        if not self.selected_session_id:
            return
        title = self.query_one("#rename_session_input", Input).value.strip()
        if not title:
            return
        self.delete_confirm_session_id = None
        self._reset_delete_button()
        self.post_message(SessionRenameRequested(self.selected_session_id, title))

    @on(Button.Pressed, "#delete_session_btn")
    def on_delete_clicked(self) -> None:
        """处理删除按钮点击并弹出确认框。"""
        if not self.selected_session_id:
            return
        target_session_id = self.selected_session_id
        self.delete_confirm_session_id = target_session_id

        def handle_confirm(confirmed: bool) -> None:
            if not confirmed:
                self.delete_confirm_session_id = None
                return
            if self.delete_confirm_session_id != target_session_id:
                return
            self.delete_confirm_session_id = None
            self.post_message(SessionDeleteRequested(target_session_id))

        self.app.push_screen(
            ConfirmDeleteScreen("Delete chat", "Are you sure you want to delete this chat?"),
            handle_confirm,
        )

    def _reset_delete_button(self) -> None:
        """将删除按钮文案恢复为默认值。"""
        self.query_one("#delete_session_btn", Button).label = "Delete"

    def refresh_sessions(self, sessions: Iterable[ChatSession], active_session_id: str) -> None:
        """根据最新状态重绘会话列表。

        Args:
            sessions: 当前所有会话集合。
            active_session_id: 当前激活会话 ID。

        重绘后会同步更新选中状态、重命名输入框与删除确认状态。
        """
        list_view = self.query_one("#session_list", ListView)
        list_view.clear()
        for session in sessions:
            marker = "●" if session.session_id == active_session_id else "○"
            item = ListItem(Label(f"{marker} {session.title}"))
            item.session_id = session.session_id
            item.session_title = session.title
            list_view.append(item)

        self.selected_session_id = active_session_id
        self.delete_confirm_session_id = None
        self._reset_delete_button()
        for session in sessions:
            if session.session_id == active_session_id:
                self.query_one("#rename_session_input", Input).value = session.title
                break
