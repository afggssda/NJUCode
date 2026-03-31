from __future__ import annotations

from textual import on
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, Label, Markdown, Static

from ...models import ChatMessage


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
        """初始化聊天面板缓存。"""
        super().__init__(**kwargs)
        self._session_views: dict[str, Vertical] = {}
        self._session_signatures: dict[str, tuple[tuple[str, str, str], ...]] = {}
        self._active_session_id: str | None = None

    def _build_bubble(self, bubble_role: str, content: str):
        """按消息角色构建气泡组件。"""
        if bubble_role == "user":
            return Static(content, classes=f"chat-bubble bubble-{bubble_role}")
        return Markdown(content, classes=f"chat-bubble bubble-{bubble_role}")

    def _message_signature(self, messages: list[ChatMessage]) -> tuple[tuple[str, str, str], ...]:
        """构建消息签名用于判断是否需要重绘。"""
        return tuple((m.role, m.content, m.created_at.isoformat()) for m in messages)

    def _ensure_session_view(self, session_id: str) -> Vertical:
        """获取或创建指定会话的消息容器。"""
        if session_id in self._session_views:
            return self._session_views[session_id]

        messages_view = self.query_one("#chat_messages", VerticalScroll)
        view = Vertical(classes="chat-session-view")
        self._session_views[session_id] = view
        messages_view.mount(view)
        return view

    def _build_message_row(self, message: ChatMessage) -> Horizontal:
        """将单条消息构建为行组件。"""
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

    def render_messages(self, messages: list[ChatMessage], session_id: str | None = None) -> None:
        """全量重绘消息列表。

        Args:
            messages: 需要渲染的会话消息序列。

        渲染时根据角色区分左右气泡布局，
        最后自动滚动到底部，确保最新消息可见。
        """
        messages_view = self.query_one("#chat_messages", VerticalScroll)
        sid = session_id or "__default__"
        target_view = self._ensure_session_view(sid)
        signature = self._message_signature(messages)

        if self._session_signatures.get(sid) != signature:
            for child in list(target_view.children):
                child.remove()
            for message in messages:
                target_view.mount(self._build_message_row(message))
            self._session_signatures[sid] = signature

        for current_sid, view in self._session_views.items():
            view.display = current_sid == sid

        self._active_session_id = sid
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
