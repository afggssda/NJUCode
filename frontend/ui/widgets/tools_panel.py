from __future__ import annotations

from textual import on
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, Input, Label, Switch

from ...models import ToolToggle


class ToolToggled(Message):
    def __init__(self, tool_key: str, enabled: bool) -> None:
        """创建工具开关变化事件。

        Args:
            tool_key: 工具唯一键。
            enabled: 新的启用状态。
        """
        self.tool_key = tool_key
        self.enabled = enabled
        super().__init__()


class HelloWorldRequested(Message):
    pass


class AnalysisCommandRequested(Message):
    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__()


class ToolsPanel(Vertical):
    def compose(self):
        """构建工具权限面板。

        面板默认包含标题和一个示例执行按钮，
        各工具开关控件由 `refresh_tools` 根据状态动态生成。
        """
        yield Label("Tool Permissions", classes="panel-title")
        yield Button("Run Hello World", id="run_hello_btn", variant="primary")
        yield Label("Analysis Workbench", classes="panel-title")
        yield Input(placeholder="检索关键字/需求描述/符号名", id="analysis_query_input")
        yield Input(placeholder="文件相对路径 (e.g. frontend/app.py)", id="analysis_path_input")
        with Horizontal():
            yield Input(placeholder="depth (1-2)", id="analysis_depth_input", value="2")
            yield Input(placeholder="top_k", id="analysis_top_input", value="10")
        with Horizontal():
            yield Button("Help", id="analysis_help_btn")
            yield Button("Scan", id="analysis_scan_btn", variant="primary")
            yield Button("Search", id="analysis_search_btn", variant="success")
            yield Button("Symbol", id="analysis_symbol_btn")
        with Horizontal():
            yield Button("Summary", id="analysis_summary_btn")
            yield Button("Deps", id="analysis_deps_btn")
            yield Button("Recall", id="analysis_recall_btn")
            yield Button("Impact", id="analysis_impact_btn", variant="warning")

    def refresh_tools(self, tools: list[ToolToggle]) -> None:
        """根据工具列表创建或更新开关组件。

        Args:
            tools: 需要显示的工具配置列表。

        该方法采用幂等更新策略：存在则更新，不存在则挂载，
        以避免重复 ID 或重复挂载异常。
        """
        for tool in tools:
            label_id = f"tool-label-{tool.key}"
            switch_id = f"tool-switch-{tool.key}"

            try:
                label = self.query_one(f"#{label_id}", Label)
                label.update(f"{tool.label}: {tool.description}")
            except NoMatches:
                self.mount(Label(f"{tool.label}: {tool.description}", id=label_id, classes="tool-label"))

            try:
                tool_switch = self.query_one(f"#{switch_id}", Switch)
                tool_switch.value = tool.enabled
            except NoMatches:
                self.mount(Switch(value=tool.enabled, id=switch_id))

    @on(Button.Pressed, "#run_hello_btn")
    def on_run_hello_pressed(self) -> None:
        """处理示例工具按钮点击并发出执行请求。"""
        self.post_message(HelloWorldRequested())

    def _analysis_command_from_button(self, button_id: str) -> str | None:
        query = self.query_one("#analysis_query_input", Input).value.strip()
        path = self.query_one("#analysis_path_input", Input).value.strip()
        depth_raw = self.query_one("#analysis_depth_input", Input).value.strip() or "2"
        top_raw = self.query_one("#analysis_top_input", Input).value.strip() or "10"

        try:
            depth = max(1, min(2, int(depth_raw)))
        except ValueError:
            depth = 2

        try:
            top_k = max(1, min(30, int(top_raw)))
        except ValueError:
            top_k = 10

        if button_id == "analysis_help_btn":
            return "/help"
        if button_id == "analysis_scan_btn":
            return "/scan"
        if button_id == "analysis_search_btn":
            return f"/search {query}" if query else None
        if button_id == "analysis_symbol_btn":
            return f"/symbol {query}" if query else None
        if button_id == "analysis_summary_btn":
            return f"/summary {path}" if path else None
        if button_id == "analysis_deps_btn":
            return f"/deps {path} --depth {depth}" if path else None
        if button_id == "analysis_recall_btn":
            return f"/recall {query} --top {top_k}" if query else None
        if button_id == "analysis_impact_btn":
            target = query or path
            return f"/impact {target} --depth {depth}" if target else None
        return None

    @on(Button.Pressed)
    def on_analysis_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if not button_id.startswith("analysis_"):
            return

        command = self._analysis_command_from_button(button_id)
        if not command:
            self.app.notify("请先填写必需参数（query/path）。", severity="warning")
            return
        self.post_message(AnalysisCommandRequested(command))

    @on(Switch.Changed)
    def on_switch_changed(self, event: Switch.Changed) -> None:
        """处理任意工具开关变化事件。

        仅对 `tool-switch-*` 命名的开关生效，
        并将结果转换为统一的 `ToolToggled` 事件上报。
        """
        if not event.switch.id:
            return
        if not event.switch.id.startswith("tool-switch-"):
            return
        key = event.switch.id.replace("tool-switch-", "")
        self.post_message(ToolToggled(key, bool(event.value)))
