from __future__ import annotations

from datetime import datetime
from typing import Optional

from textual import on
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, Label, Markdown, Static

from ...models import ChatMessage, ChatSession


class MessageSubmitted(Message):
    def __init__(self, content: str) -> None:
        """创建“用户提交消息”事件。

        Args:
            content: 用户输入并确认发送的消息内容。

        该事件由聊天输入区发出，供应用层接收并触发模型请求。
        """
        self.content = content
        super().__init__()


class StreamInterruptRequested(Message):
    pass


class ChatPanel(Vertical):
    def __init__(self, **kwargs):
        """初始化聊天面板缓存。

        缓存各会话的 Vertical 容器与消息签名，
        避免切换会话时频繁重建 DOM 节点，减少闪烁。
        """
        super().__init__(**kwargs)
        self._session_views: dict[str, Vertical] = {}
        self._session_signatures: dict[str, tuple] = {}
        self._active_session_id: Optional[str] = None

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _format_message_time(dt: datetime) -> str:
        """将消息时间格式化为简短时间字符串。

        同一天内只显示 HH:MM，其他日期显示 MM-DD HH:MM。

        Args:
            dt: 消息创建时间。

        Returns:
            格式化后的时间字符串。
        """
        now = datetime.now()
        if dt.date() == now.date():
            return dt.strftime("%H:%M")
        return dt.strftime("%m-%d %H:%M")

    @staticmethod
    def _format_compression_info(session: ChatSession) -> str:
        """格式化压缩分隔线的附加信息文本。

        Args:
            session: 当前会话对象。

        Returns:
            形如 '共压缩 2 次 · 最近: 2026-05-11 14:32 · 节省 ~800 tokens' 的字符串；
            无压缩记录时返回空字符串。
        """
        parts = []
        if session.compression_count > 0:
            parts.append(f"共压缩 {session.compression_count} 次")
        if session.compressed_at:
            ts = session.compressed_at.strftime("%Y-%m-%d %H:%M")
            parts.append(f"最近: {ts}")
        if session.last_compressed_token_count > 0 and session.token_estimate > 0:
            saved = session.last_compressed_token_count - session.token_estimate
            if saved > 0:
                parts.append(f"节省 ~{saved} tokens")
        return " · ".join(parts)

    def _build_bubble(self, bubble_role: str, content: str):
        """按消息角色构建气泡组件。

        Args:
            bubble_role: 气泡类型标识，支持 'user'、'assistant'、
                         'error'、'system'、'summary'、'compressed'。
            content: 气泡内的文本内容。

        Returns:
            对应角色的 Static 或 Markdown 组件。
        """
        if bubble_role == "user":
            return Static(content, classes=f"chat-bubble bubble-{bubble_role}")
        if bubble_role in ("summary", "compressed"):
            return Static(content, classes="chat-bubble bubble-summary")
        if bubble_role in ("error", "system"):
            return Static(content, classes=f"chat-bubble bubble-{bubble_role}")
        return Markdown(content, classes=f"chat-bubble bubble-{bubble_role}")

    def _message_signature(
        self, messages: list[ChatMessage]
    ) -> tuple[tuple[str, str, str], ...]:
        """构建消息列表的签名用于缓存判断。

        签名由每条消息的 (role, content, created_at) 三元组组成，
        用于判断消息是否发生变化，避免不必要的 DOM 重建。

        Args:
            messages: 当前会话消息列表。

        Returns:
            不可变元组签名。
        """
        return tuple((m.role, m.content, m.created_at.isoformat()) for m in messages)

    def _ensure_session_view(self, session_id: str) -> Vertical:
        """获取或创建指定会话的消息容器。

        Args:
            session_id: 目标会话 ID。

        Returns:
            已挂载到 #chat_messages 中的 Vertical 容器。
        """
        if session_id in self._session_views:
            return self._session_views[session_id]

        messages_view = self.query_one("#chat_messages", VerticalScroll)
        view = Vertical(classes="chat-session-view")
        self._session_views[session_id] = view
        messages_view.mount(view)
        return view

    def _build_compressed_divider(self, session: Optional[ChatSession] = None) -> Static:
        """构建'以上内容已压缩'分隔线组件。

        若传入 session 对象，会在分隔线中附加压缩次数和时间信息，
        帮助用户了解当前上下文被压缩的程度。

        Args:
            session: 当前会话对象（可选）。

        Returns:
            包含压缩信息的 Static 分隔线组件。
        """
        base = "── 以上内容已压缩 ──"
        if session:
            info = self._format_compression_info(session)
            if info:
                base = f"── 以上内容已压缩 · {info} ──"
        return Static(base, classes="compressed-divider")

    def _build_message_row(self, message: ChatMessage) -> Horizontal:
        """将单条消息构建为行组件。

        Args:
            message: 目标消息对象。

        Returns:
            包含气泡的 Horizontal 行组件。
        """
        role = message.role.lower()
        content = message.content or " "

        if role == "user":
            bubble_role = "user"
        elif role in ("summary", "compressed"):
            bubble_role = "summary"
        elif content.startswith("[系统错误]"):
            bubble_role = "error"
        elif content.startswith("[系统提示]"):
            bubble_role = "system"
        else:
            bubble_role = "assistant"

        bubble = self._build_bubble(bubble_role, content)
        if bubble_role == "user":
            return Horizontal(
                Static("", classes="bubble-spacer"),
                bubble,
                classes=f"message-row row-{bubble_role}",
            )
        return Horizontal(
            bubble,
            Static("", classes="bubble-spacer"),
            classes=f"message-row row-{bubble_role}",
        )

    def compose(self):
        """构建聊天面板结构。

        包括标题、状态栏、消息滚动区与输入操作区。
        输入区默认提供发送与停止两个按钮，
        以支持普通提问和流式中断两类核心操作。
        """
        yield Label("Chat", classes="panel-title")
        yield Label("状态: Idle", id="chat_status")
        yield VerticalScroll(id="chat_messages")
        with Horizontal(id="chat_input_row"):
            yield Input(placeholder="输入消息，回车或点击发送", id="chat_input")
            yield Button("Send", id="send_btn", variant="success")
            yield Button("Stop", id="stop_btn", variant="warning", disabled=True)

    @on(Button.Pressed, "#send_btn")
    def on_send_clicked(self) -> None:
        """处理发送按钮点击事件并提交消息。"""
        self.submit_message()

    @on(Input.Submitted, "#chat_input")
    def on_input_submitted(self) -> None:
        """处理输入框回车提交事件并发送消息。"""
        self.submit_message()

    @on(Button.Pressed, "#stop_btn")
    def on_stop_clicked(self) -> None:
        """处理停止按钮点击并向上抛出中断请求事件。"""
        self.post_message(StreamInterruptRequested())

    def submit_message(self) -> None:
        """读取输入框内容并发出提交消息事件。

        方法会先清洗空白字符并过滤空消息，
        有效输入会在发送后清空输入框，
        然后通过 `MessageSubmitted` 事件通知应用层。
        """
        input_widget = self.query_one("#chat_input", Input)
        text = input_widget.value.strip()
        if not text:
            return
        input_widget.value = ""
        self.post_message(MessageSubmitted(text))

    def append_to_input(self, text: str) -> None:
        """在输入框追加文本，并自动获取焦点。
        方便通过外部点击快速填入文件上下文标记。
        """
        input_widget = self.query_one("#chat_input", Input)
        if input_widget.value and not input_widget.value.endswith(" "):
            input_widget.value += " "
        input_widget.value += text
        input_widget.focus()
        input_widget.cursor_position = len(input_widget.value)

    def render_messages(
        self,
        messages: list[ChatMessage],
        session_id: str | None = None,
        session: Optional[ChatSession] = None,
    ) -> None:
        """全量重绘消息列表。

        Args:
            messages: 需要渲染的会话消息序列。
            session_id: 当前会话 ID，用于缓存视图。
            session: 可选的完整 ChatSession 对象；
                     提供时会在有压缩摘要的会话开头插入分隔线，
                     并在中断会话恢复时预填输入框。

        渲染时根据角色区分左右气泡布局，
        最后自动滚动到底部，确保最新消息可见。
        """
        messages_view = self.query_one("#chat_messages", VerticalScroll)
        sid = session_id or "__default__"
        target_view = self._ensure_session_view(sid)

        # 构建复合签名：消息内容 + 摘要内容 + 压缩次数（任一变化都触发重绘）
        summary_tag = session.summary if session else ""
        compression_tag = str(session.compression_count) if session else ""
        signature: tuple = (
            self._message_signature(messages)
            + (("__summary__", summary_tag, compression_tag),)
        )

        if self._session_signatures.get(sid) != signature:
            for child in list(target_view.children):
                child.remove()
            # 有摘要时先插入带元数据的压缩分隔线和摘要气泡
            if session and session.summary:
                target_view.mount(self._build_compressed_divider(session))
                summary_msg = ChatMessage(
                    role="summary",
                    content=f"【历史摘要】\n{session.summary}",
                )
                target_view.mount(self._build_message_row(summary_msg))
            for message in messages:
                target_view.mount(self._build_message_row(message))
            self._session_signatures[sid] = signature

        for current_sid, view in self._session_views.items():
            view.display = current_sid == sid

        self._active_session_id = sid

        # 中断恢复：若会话有未发送内容且输入框当前为空，则预填
        if session and session.interrupted and session.interrupted_context:
            try:
                input_widget = self.query_one("#chat_input", Input)
                if not input_widget.value.strip():
                    input_widget.value = session.interrupted_context
            except Exception:
                pass

        messages_view.scroll_end(animate=False)

    def update_last_message(self, message: ChatMessage) -> None:
        """仅增量更新最后一条消息内容。

        Args:
            message: 最新状态下的末条消息对象。

        该方法用于流式输出场景，减少全量重绘带来的闪烁。
        当结构不符合预期时会自动回退到安全的全量重绘。
        """
        messages_view = self.query_one("#chat_messages", VerticalScroll)
        sid = self._active_session_id or "__default__"
        active_view = self._session_views.get(sid)
        if active_view is None:
            self.render_messages([message], sid)
            return

        if not active_view.children:
            active_view.mount(self._build_message_row(message))
            messages_view.scroll_end(animate=False)
            return

        last_row = active_view.children[-1]
        if not isinstance(last_row, Horizontal):
            self.render_messages([message], sid)
            return

        role = message.role.lower()
        content = message.content or " "

        if role == "user":
            bubble_role = "user"
        elif content.startswith("[系统错误]"):
            bubble_role = "error"
        elif content.startswith("[系统提示]"):
            bubble_role = "system"
        else:
            bubble_role = "assistant"
        bubble_widget = None
        if bubble_role == "user" and len(last_row.children) >= 2:
            bubble_widget = last_row.children[1]
        elif bubble_role != "user" and len(last_row.children) >= 1:
            bubble_widget = last_row.children[0]

        if isinstance(bubble_widget, Static):
            bubble_widget.update(content)
        elif isinstance(bubble_widget, Markdown):
            bubble_widget.update(content)
        else:
            self.render_messages([message], sid)

        messages_view.scroll_end(animate=False)

    def set_busy(self, busy: bool, status_text: str) -> None:
        """更新聊天面板忙闲状态与按钮可用性。

        Args:
            busy: 是否处于流式任务中。
            status_text: 状态栏展示文本。

        忙碌时禁用发送并启用停止；空闲时反向切换。
        """
        self.query_one("#chat_status", Label).update(f"状态: {status_text}")
        self.query_one("#stop_btn", Button).disabled = not busy
        self.query_one("#send_btn", Button).disabled = busy
