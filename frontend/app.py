from __future__ import annotations

import os
from pathlib import Path
from threading import Event

from dotenv import load_dotenv
from textual import work
from textual.app import App
from textual.containers import Horizontal
from textual.widgets import DirectoryTree, Footer, Header, TabPane, TabbedContent
from textual.widgets import TextArea

from .services.openai_client import OpenAICompatibleClient, OpenAIRequest
from .services.runtime_tools import run_hello_world
from .state import AppState
from .ui.widgets.chat_panel import ChatPanel, MessageSubmitted, StreamInterruptRequested
from .ui.widgets.code_viewer_panel import CodeViewerPanel
from .ui.widgets.config_panel import ConfigPanel, ConfigSaved, MirrorSelected
from .ui.widgets.file_tree_panel import FileTreePanel
from .ui.widgets.session_panel import (
    SessionCreateRequested,
    SessionDeleteRequested,
    SessionPanel,
    SessionRenameRequested,
    SessionSelected,
)
from .ui.widgets.splitter import SplitterDragged, VerticalSplitter
from .ui.widgets.tools_panel import HelloWorldRequested, ToolToggled, ToolsPanel


class NjuCodeApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "NJU Code (Textual Frontend)"
    SUB_TITLE = "Claude Code-like MVP"

    BINDINGS = [
        ("ctrl+n", "new_chat", "New Chat"),
        ("ctrl+c", "interrupt_stream", "Interrupt"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        """初始化应用实例。

        该方法负责加载环境变量、创建全局状态对象、初始化模型客户端，
        并设置流式会话所需的中断标志与三栏分屏默认比例。
        所有跨组件共享的运行态数据都在这里集中完成初始化。
        """
        super().__init__()
        load_dotenv()
        workspace_root = Path(os.getenv("WORKSPACE_ROOT", ".")).resolve()
        self.state = AppState(workspace_root=workspace_root)
        self.client = OpenAICompatibleClient()
        self.stream_cancel_event: Event | None = None
        self.stream_session_id: str | None = None
        self.stream_active = False
        self.left_ratio = 0.22
        self.right_ratio = 0.30

    def compose(self):
        """声明并构建主界面组件树。

        布局按“左-中-右”三栏组织，并在栏位之间插入可拖拽分割条。
        左栏用于文件与会话，中栏用于聊天，右栏用于代码/工具/模型配置。
        返回的组件树将由 Textual 在启动阶段自动挂载。
        """
        yield Header()
        with Horizontal(id="root"):
            with TabbedContent(initial="explorer", id="left_tabs"):
                with TabPane("Explorer", id="explorer"):
                    yield FileTreePanel(workspace_root=self.state.workspace_root)
                with TabPane("Chats", id="chats"):
                    yield SessionPanel(id="session_panel")
            yield VerticalSplitter(splitter_id="left", id="splitter_left")
            with TabbedContent(initial="chat", id="center_tabs"):
                with TabPane("Chat", id="chat"):
                    yield ChatPanel(id="chat_panel")
            yield VerticalSplitter(splitter_id="right", id="splitter_right")
            with TabbedContent(initial="tools", id="right_tabs"):
                with TabPane("Code", id="code"):
                    yield CodeViewerPanel(id="code_view")
                with TabPane("Tools", id="tools"):
                    yield ToolsPanel(id="tools_panel")
                with TabPane("Model", id="model"):
                    yield ConfigPanel(id="config_panel")
        yield Footer()

    def on_mount(self) -> None:
        """界面挂载完成后的启动钩子。

        挂载后立即恢复持久化状态并刷新 UI，
        同时执行语法高亮可用性诊断与分栏宽度应用。
        该方法确保用户打开程序时看到的是可直接交互的稳定界面。
        """
        self.state.load()
        self.refresh_ui()
        self._diagnose_syntax_highlighting()
        self._apply_pane_widths()

    def _clamp(self, value: float, low: float, high: float) -> float:
        """将数值限制在指定闭区间内。

        Args:
            value: 待约束的原始值。
            low: 允许的最小值。
            high: 允许的最大值。

        Returns:
            处于 [low, high] 区间内的安全值。
        """
        return max(low, min(value, high))

    def _apply_pane_widths(self) -> None:
        """根据当前比例应用三栏宽度。

        方法会先修正极端比例，保证中间聊天区留有最小可视空间，
        然后将比例转换为百分比并写入对应栏位样式。
        该逻辑用于启动初始化与拖拽分割条后的实时更新。
        """
        total = self.left_ratio + self.right_ratio
        if total > 0.90:
            scale = 0.90 / total
            self.left_ratio *= scale
            self.right_ratio *= scale
        center_ratio = 1.0 - self.left_ratio - self.right_ratio
        if center_ratio < 0.10:
            center_ratio = 0.10

        left_tabs = self.query_one("#left_tabs", TabbedContent)
        center_tabs = self.query_one("#center_tabs", TabbedContent)
        right_tabs = self.query_one("#right_tabs", TabbedContent)
        left_tabs.styles.width = f"{self.left_ratio * 100:.2f}%"
        center_tabs.styles.width = f"{center_ratio * 100:.2f}%"
        right_tabs.styles.width = f"{self.right_ratio * 100:.2f}%"

    def on_splitter_dragged(self, message: SplitterDragged) -> None:
        """处理分割条拖拽事件并更新布局比例。

        通过屏幕坐标推导新的左右栏目标宽度，
        同时结合最小宽度约束，防止任何一栏被压缩到不可用。
        计算完成后会立即触发 `_apply_pane_widths` 刷新界面。
        """
        total_width = max(self.size.width, 80)
        min_left = 28 / total_width
        min_center = 30 / total_width
        min_right = 36 / total_width

        x = self._clamp(message.screen_x / total_width, 0.0, 1.0)
        if message.splitter_id == "left":
            max_left = 1.0 - self.right_ratio - min_center
            self.left_ratio = self._clamp(x, min_left, max_left)
        elif message.splitter_id == "right":
            proposed_right = 1.0 - x
            max_right = 1.0 - self.left_ratio - min_center
            self.right_ratio = self._clamp(proposed_right, min_right, max_right)

        self._apply_pane_widths()

    def _diagnose_syntax_highlighting(self) -> None:
        """检查常见语言语法高亮可用性并给出提示。

        该方法创建临时编辑器读取可用语言集合，
        与预设常见语言列表做差集。
        若存在缺失项，则通过通知提醒用户补齐依赖。
        """
        common_languages = {
            "python",
            "javascript",
            "typescript",
            "json",
            "yaml",
            "markdown",
            "bash",
            "html",
            "css",
            "sql",
            "cpp",
            "c",
            "java",
            "go",
            "rust",
            "toml",
        }
        editor = TextArea.code_editor("", language=None)
        available = set(editor.available_languages)
        missing = sorted(common_languages - available)
        if missing:
            self.notify(
                f"部分语法高亮不可用: {', '.join(missing[:6])}{'...' if len(missing) > 6 else ''}",
                severity="warning",
            )

    def refresh_ui(self) -> None:
        """将全局状态同步到各 UI 面板。

        该方法会统一刷新会话列表、聊天内容、工具开关和模型配置。
        若当前没有流式输出任务，还会将聊天状态恢复为 Idle。
        这是应用内部最核心的“状态 -> 视图”同步入口。
        """
        session_panel = self.query_one("#session_panel", SessionPanel)
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        tools_panel = self.query_one("#tools_panel", ToolsPanel)
        config_panel = self.query_one("#config_panel", ConfigPanel)

        session_panel.refresh_sessions(self.state.sessions, self.state.active_session_id)
        chat_panel.render_messages(self.state.active_session.messages)
        if not self.stream_active:
            chat_panel.set_busy(False, "Idle")
        tools_panel.refresh_tools(list(self.state.tools.values()))
        config_panel.load_config(self.state.model_config)

    def on_session_create_requested(self, _: SessionCreateRequested) -> None:
        """响应新建会话请求。

        收到事件后创建新会话、持久化状态，
        然后触发全界面刷新以显示新会话并自动切换激活。
        """
        self.state.create_session()
        self.state.save()
        self.refresh_ui()

    def on_session_selected(self, message: SessionSelected) -> None:
        """响应会话切换事件。

        根据事件携带的会话 ID 更新当前激活会话，
        持久化后刷新界面，确保聊天区显示正确历史消息。
        """
        self.state.switch_session(message.session_id)
        self.state.save()
        self.refresh_ui()

    def on_session_rename_requested(self, message: SessionRenameRequested) -> None:
        """响应会话重命名请求。

        该方法更新目标会话标题并保存，
        随后刷新左侧会话列表与输入框回显内容，保持 UI 一致。
        """
        self.state.rename_session(message.session_id, message.title)
        self.state.save()
        self.refresh_ui()

    def on_session_delete_requested(self, message: SessionDeleteRequested) -> None:
        """响应会话删除请求。

        删除指定会话后会自动处理“最后一个会话”兜底逻辑，
        最终保存状态并刷新界面，保证不会出现空会话集合。
        """
        self.state.delete_session(message.session_id)
        self.state.save()
        self.refresh_ui()

    def on_message_submitted(self, message: MessageSubmitted) -> None:
        """处理用户发送消息并启动模型流式回复。

        若当前已有流式任务在执行，则给出提示并拒绝重复提交。
        否则会写入用户消息和占位 assistant 消息，
        然后创建请求对象并异步启动流式线程任务。
        """
        if self.stream_active:
            self.notify("模型正在输出中，请先中断或等待完成。")
            return

        session_id = self.state.active_session_id
        self.state.append_message("user", message.content)
        self.state.append_message("assistant", "")
        self.state.save()
        self.query_one("#chat_panel", ChatPanel).render_messages(self.state.active_session.messages)

        self.stream_active = True
        self.stream_session_id = session_id
        self.stream_cancel_event = Event()
        self.query_one("#chat_panel", ChatPanel).set_busy(True, "正在等待模型响应...")

        request = OpenAIRequest(
            base_url=self.state.model_config.base_url,
            api_key=self.state.model_config.api_key,
            model=self.state.model_config.model,
            message=message.content,
            model_file=self.state.model_config.model_file,
        )
        self._stream_assistant_reply(request, session_id, self.stream_cancel_event)

    def on_stream_interrupt_requested(self, _: StreamInterruptRequested) -> None:
        """响应聊天面板的中断请求事件。"""
        self.action_interrupt_stream()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """处理文件树文件选择事件。

        当用户在 Explorer 里选中文件时，
        会尝试在右侧 Code 面板中打开该文件。
        打开失败时只弹通知不崩溃，成功则切换到 Code 标签页。
        """
        file_path = Path(event.path)
        if file_path.is_dir():
            return
        try:
            self.query_one("#code_view", CodeViewerPanel).show_file(file_path)
        except Exception as error:
            self.notify(f"打开文件失败: {error}", severity="error")
            return
        self.query_one("#right_tabs", TabbedContent).active = "code"

    def on_tool_toggled(self, message: ToolToggled) -> None:
        """处理工具权限开关变更并持久化。"""
        self.state.update_tool(message.tool_key, message.enabled)
        self.state.save()

    def on_hello_world_requested(self, _: HelloWorldRequested) -> None:
        """执行内置 Hello World 工具并将结果回写聊天区。

        这是一个最小后端执行示例，
        用于验证工具按钮、执行逻辑和消息回显链路是否连通。
        """
        result = run_hello_world(self.state.workspace_root)
        self.state.append_message("assistant", result)
        self.state.save()
        self.query_one("#chat_panel", ChatPanel).render_messages(self.state.active_session.messages)
        self.notify(result)

    def on_mirror_selected(self, message: MirrorSelected) -> None:
        """处理模型镜像预设切换。

        更新全局模型配置中的镜像与 base_url，
        并将结果重新加载到配置面板输入框中展示。
        """
        self.state.set_mirror(message.mirror)
        self.query_one("#config_panel", ConfigPanel).load_config(self.state.model_config)

    def on_config_saved(self, message: ConfigSaved) -> None:
        """处理模型配置保存事件。

        将面板输入值写入状态对象并持久化到本地设置，
        保存成功后显示通知，便于用户确认配置已生效。
        """
        self.state.model_config.base_url = message.base_url or self.state.model_config.base_url
        self.state.model_config.api_key = message.api_key
        self.state.model_config.model = message.model or self.state.model_config.model
        self.state.model_config.model_file = message.model_file
        self.state.save()
        self.notify("配置已保存到 .nju_code/settings.json")

    def action_new_chat(self) -> None:
        """快捷键动作：快速创建新会话并刷新界面。"""
        self.state.create_session()
        self.refresh_ui()

    def action_interrupt_stream(self) -> None:
        """快捷键动作：中断当前流式输出。

        该动作不会立刻销毁线程，而是设置取消事件标记，
        让流式循环在下一次检查点安全退出。
        """
        if self.stream_active and self.stream_cancel_event:
            self.stream_cancel_event.set()
            self.query_one("#chat_panel", ChatPanel).set_busy(True, "正在中断输出...")

    def _append_stream_chunk(self, session_id: str, chunk: str) -> None:
        """把一个流式分片追加到指定会话最后一条消息。

        为减少闪烁，该方法仅增量更新最后一个气泡组件，
        而不是重绘整个消息列表。
        """
        for session in self.state.sessions:
            if session.session_id != session_id:
                continue
            if not session.messages:
                return
            session.messages[-1].content += chunk
            if self.state.active_session_id == session_id:
                self.query_one("#chat_panel", ChatPanel).render_messages(session.messages)
            return

    def _finish_stream(self, session_id: str, cancelled: bool, error_message: str | None) -> None:
        """在流式任务结束时收尾状态与界面。

        根据是否中断或报错补充最终提示文本，
        重置流式运行标记，并在必要时刷新聊天状态条为 Idle。
        最后执行状态持久化，避免内容丢失。
        """
        self.stream_active = False
        self.stream_cancel_event = None
        self.stream_session_id = None

        target_session = None
        for session in self.state.sessions:
            if session.session_id == session_id:
                target_session = session
                break

        if target_session and target_session.messages and error_message:
            target_session.messages[-1].content = f"[系统错误] {error_message}"
        elif target_session and target_session.messages and cancelled:
            if not target_session.messages[-1].content.strip():
                target_session.messages[-1].content = "[输出已中断]"
            else:
                target_session.messages[-1].content += "\n[输出已中断]"

        if self.state.active_session_id == session_id and target_session is not None:
            chat_panel = self.query_one("#chat_panel", ChatPanel)
            chat_panel.render_messages(target_session.messages)
            chat_panel.set_busy(False, "Idle")
        self.state.save()

    @work(thread=True, exclusive=True)
    def _stream_assistant_reply(self, request: OpenAIRequest, session_id: str, cancel_event: Event) -> None:
        """在线程中执行模型流式调用。

        该方法持续读取模型分片并回投到主线程更新 UI，
        支持取消事件中断与异常捕获。
        无论正常完成还是异常结束，都会统一触发 `_finish_stream` 收尾。
        """
        cancelled = False
        error_message: str | None = None

        try:
            for chunk in self.client.stream_chat(request, stop_event=cancel_event):
                if cancel_event.is_set():
                    cancelled = True
                    break
                self.call_from_thread(self._append_stream_chunk, session_id, chunk)
            if cancel_event.is_set():
                cancelled = True
        except Exception as error:
            error_message = str(error)

        self.call_from_thread(self._finish_stream, session_id, cancelled, error_message)
