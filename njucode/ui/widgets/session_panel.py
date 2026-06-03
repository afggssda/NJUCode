from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

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


class SessionExportRequested(Message):
    def __init__(self, session_id: str) -> None:
        """触发会话导出事件。

        Args:
            session_id: 需要导出的会话 ID。
        """
        self.session_id = session_id
        super().__init__()


class SessionImportRequested(Message):
    pass


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
        self.selected_session_id: Optional[str] = None
        self.delete_confirm_session_id: Optional[str] = None

    def compose(self):
        """构建会话列表与操作区。

        包含会话搜索输入框、新建按钮、会话列表、重命名输入框，
        Rename/Delete 操作按钮，以及导出/导入按钮、
        Token 估算标签、摘要预览标签和会话详情统计标签。
        """
        yield Label("Chats", classes="panel-title")
        yield Input(placeholder="搜索会话…", id="session_search_input")
        yield Button("+ New Chat", id="new_chat_btn", variant="primary")
        yield ListView(id="session_list")
        yield Input(placeholder="重命名当前会话", id="rename_session_input")
        with Horizontal(id="session_action_row"):
            yield Button("Rename", id="rename_session_btn")
            yield Button("Delete", id="delete_session_btn", variant="error")
        with Horizontal(id="session_export_row"):
            yield Button("Export", id="export_session_btn")
            yield Button("Import", id="import_session_btn")
        yield Label("", id="session_token_label")
        yield Label("", id="session_stats_label")
        yield Label("", id="session_summary_label")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _format_relative_time(dt: Optional[datetime]) -> str:
        """将 datetime 格式化为相对时间描述。

        Args:
            dt: 目标时间；为 None 时返回空字符串。

        Returns:
            形如 '刚刚'、'5 分钟前'、'2 小时前'、'3 天前' 的字符串。
        """
        if dt is None:
            return ""
        delta = datetime.now() - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{seconds // 60} 分钟前"
        if seconds < 86400:
            return f"{seconds // 3600} 小时前"
        days = seconds // 86400
        if days < 30:
            return f"{days} 天前"
        return dt.strftime("%Y-%m-%d")

    @staticmethod
    def _format_token_label(token_estimate: int, threshold: int = 3000) -> str:
        """根据 token 估算值生成带使用率的标签文本。

        Args:
            token_estimate: 当前 token 估算值。
            threshold: 压缩触发阈值（默认 3000）。

        Returns:
            形如 '~1234 tokens (41%)' 的字符串，
            超过阈值时附加 '⚠ 即将压缩' 提示。
        """
        pct = int(token_estimate / threshold * 100) if threshold > 0 else 0
        base = f"~{token_estimate} tokens ({pct}%)"
        if token_estimate >= threshold:
            base += " ⚠ 已超限"
        elif pct >= 80:
            base += " ⚠ 即将压缩"
        return base

    @staticmethod
    def _build_session_label(session: ChatSession, is_active: bool) -> str:
        """为会话列表条目构建显示文本。

        Args:
            session: 目标会话对象。
            is_active: 是否为当前激活会话。

        Returns:
            形如 '● Chat 1 [C×2]' 的字符串，
            [C×N] 标记表示已被压缩 N 次。
        """
        marker = "●" if is_active else "○"
        compression_mark = ""
        if session.compression_count > 0:
            compression_mark = f" [C×{session.compression_count}]"
        elif session.compressed_at:
            compression_mark = " [C]"
        return f"{marker} {session.title}{compression_mark}"

    def _matches_search(self, session: ChatSession, query: str) -> bool:
        """判断会话是否与搜索关键词匹配。

        匹配范围：会话标题（忽略大小写）。

        Args:
            session: 目标会话。
            query: 搜索关键词（已去除首尾空格）。

        Returns:
            query 为空时始终返回 True；否则检查标题是否包含关键词。
        """
        if not query:
            return True
        return query.lower() in session.title.lower()

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------

    @on(Button.Pressed, "#new_chat_btn")
    def on_new_chat(self) -> None:
        """处理新建会话按钮并发送创建事件。"""
        self.post_message(SessionCreateRequested())

    @on(Input.Changed, "#session_search_input")
    def on_search_changed(self, event: Input.Changed) -> None:
        """搜索输入变化时触发会话列表过滤刷新。

        通过重新筛选列表来实现即时搜索效果。
        """
        # 触发父级刷新——由 app.py 调用 refresh_sessions 带入最新数据
        # 此处直接对现有 ListItem 做显示/隐藏实现即时过滤
        query = event.value.strip()
        list_view = self.query_one("#session_list", ListView)
        for item in list_view.children:
            if not isinstance(item, ListItem):
                continue
            session_title: str = getattr(item, "session_title", "")
            item.display = not query or query.lower() in session_title.lower()

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

    @on(Button.Pressed, "#export_session_btn")
    def on_export_clicked(self) -> None:
        """处理导出按钮点击，发出导出请求事件。"""
        if not self.selected_session_id:
            return
        self.post_message(SessionExportRequested(self.selected_session_id))

    @on(Button.Pressed, "#import_session_btn")
    def on_import_clicked(self) -> None:
        """处理导入按钮点击，发出导入请求事件。"""
        self.post_message(SessionImportRequested())

    # ------------------------------------------------------------------
    # 刷新方法
    # ------------------------------------------------------------------

    def refresh_sessions(
        self,
        sessions: Iterable[ChatSession],
        active_session_id: str,
        token_threshold: int = 3000,
    ) -> None:
        """根据最新状态重绘会话列表及底部信息区。

        Args:
            sessions: 当前所有会话集合。
            active_session_id: 当前激活会话 ID。
            token_threshold: 自动压缩触发阈值，用于计算 token 使用率显示。

        重绘后会同步更新选中状态、重命名输入框与删除确认状态。
        同时刷新 Token 估算标签、会话统计标签与摘要标签。
        已有搜索关键词时，会对新列表项应用即时过滤。
        """
        list_view = self.query_one("#session_list", ListView)
        search_query = ""
        try:
            search_query = self.query_one("#session_search_input", Input).value.strip()
        except Exception:
            pass

        list_view.clear()
        active_session: Optional[ChatSession] = None
        session_list = list(sessions)

        for session in session_list:
            label_text = self._build_session_label(session, session.session_id == active_session_id)

            # 最后一条消息时间（用于列表辅助信息）
            last_time = ""
            if session.messages:
                last_time = self._format_relative_time(session.messages[-1].created_at)
            if last_time:
                label_text += f"  {last_time}"

            item = ListItem(Label(label_text))
            item.session_id = session.session_id  # type: ignore[attr-defined]
            item.session_title = session.title  # type: ignore[attr-defined]

            # 即时搜索过滤：搜索框有内容时隐藏不匹配项
            if search_query and not self._matches_search(session, search_query):
                item.display = False

            list_view.append(item)
            if session.session_id == active_session_id:
                active_session = session

        self.selected_session_id = active_session_id
        self.delete_confirm_session_id = None
        self._reset_delete_button()

        if active_session:
            self.query_one("#rename_session_input", Input).value = active_session.title
            self._refresh_token_label(active_session, token_threshold)
            self._refresh_stats_label(active_session)
            self._refresh_summary_label(active_session)

    def _refresh_token_label(self, session: ChatSession, threshold: int) -> None:
        """刷新 token 估算标签。

        Args:
            session: 当前激活会话。
            threshold: 压缩阈值。
        """
        token_label = self.query_one("#session_token_label", Label)
        token_label.update(self._format_token_label(session.token_estimate, threshold))

    def _refresh_stats_label(self, session: ChatSession) -> None:
        """刷新会话统计标签，显示消息数和压缩状态。

        Args:
            session: 当前激活会话。
        """
        stats_label = self.query_one("#session_stats_label", Label)
        msg_count = len(session.messages)
        user_count = sum(1 for m in session.messages if m.role == "user")
        parts = [f"{msg_count} 条消息（用户 {user_count} 条）"]
        if session.compression_count > 0:
            compressed_time = self._format_relative_time(session.compressed_at)
            parts.append(f"已压缩 {session.compression_count} 次")
            if compressed_time:
                parts.append(f"上次: {compressed_time}")
        stats_label.update(" | ".join(parts))

    def _refresh_summary_label(self, session: ChatSession) -> None:
        """刷新摘要预览标签。

        Args:
            session: 当前激活会话。
        """
        summary_label = self.query_one("#session_summary_label", Label)
        if not session.summary:
            summary_label.update("")
            return
        # 取摘要第一行作为预览（通常是【用户意图】行）
        first_line = session.summary.split("\n")[0].strip()
        if len(first_line) > 60:
            first_line = first_line[:60] + "…"
        summary_label.update(f"摘要: {first_line}")

