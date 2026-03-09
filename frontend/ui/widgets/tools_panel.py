from __future__ import annotations

from textual import on
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Button, Label, Switch

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


class ToolsPanel(Vertical):
    def compose(self):
        """构建工具权限面板。

        面板默认包含标题和一个示例执行按钮，
        各工具开关控件由 `refresh_tools` 根据状态动态生成。
        """
        yield Label("Tool Permissions", classes="panel-title")
        yield Button("Run Hello World", id="run_hello_btn", variant="primary")

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
