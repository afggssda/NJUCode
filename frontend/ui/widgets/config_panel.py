from __future__ import annotations

from textual import on
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Label

from ...models import ModelConfig


class MirrorSelected(Message):
    def __init__(self, mirror: str) -> None:
        """创建镜像预设选择事件。

        Args:
            mirror: 被选择的镜像键名。

        应用层收到后通常会更新 base_url 与当前镜像状态。
        """
        self.mirror = mirror
        super().__init__()


class ConfigSaved(Message):
    def __init__(self, base_url: str, api_key: str, model: str, model_file: str) -> None:
        """创建模型配置保存事件。

        Args:
            base_url: OpenAI 兼容接口地址。
            api_key: 接口鉴权密钥。
            model: 模型名称。
            model_file: 可选文件路径，用于注入上下文。
        """
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.model_file = model_file
        super().__init__()


class ConfigPanel(Vertical):
    def compose(self):
        """构建模型配置面板。

        包含镜像快捷按钮、连接参数输入项以及保存按钮。
        该面板负责采集配置，不直接落盘，
        具体保存动作由上层应用处理。
        """
        yield Label("Model (OpenAI Compatible)", classes="panel-title")
        yield Label("Mirror Presets", classes="sub-title")
        with Horizontal(id="mirror_buttons"):
            yield Button("AtlasCloud", id="mirror-atlascloud")
            yield Button("ModelScope", id="mirror-modelscope")
            yield Button("Official", id="mirror-official")
            yield Button("OpenRouter", id="mirror-openrouter")
            yield Button("Azure-Compatible", id="mirror-azure_compatible")
            yield Button("Custom", id="mirror-custom")
        yield Input(placeholder="Base URL", id="base_url")
        yield Input(placeholder="API Key", password=True, id="api_key")
        yield Input(placeholder="Model Name", id="model")
        yield Input(placeholder="Model File Path (optional)", id="model_file")
        yield Button("Save Config", id="save_config", variant="primary")

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """统一处理配置面板按钮点击事件。

        对于 `mirror-*` 按钮，发送镜像切换事件；
        对于保存按钮，收集当前输入框值并发送配置保存事件。
        """
        if not event.button.id:
            return

        button_id = event.button.id
        if button_id.startswith("mirror-"):
            mirror = button_id.replace("mirror-", "")
            self.post_message(MirrorSelected(mirror))
            return

        if button_id == "save_config":
            self.post_message(
                ConfigSaved(
                    base_url=self.query_one("#base_url", Input).value.strip(),
                    api_key=self.query_one("#api_key", Input).value.strip(),
                    model=self.query_one("#model", Input).value.strip(),
                    model_file=self.query_one("#model_file", Input).value.strip(),
                )
            )

    def load_config(self, model_config: ModelConfig) -> None:
        """将模型配置对象回填到输入控件。

        Args:
            model_config: 当前生效的模型配置。

        常用于应用启动时的状态恢复，或镜像切换后的界面同步。
        """
        self.query_one("#base_url", Input).value = model_config.base_url
        self.query_one("#api_key", Input).value = model_config.api_key
        self.query_one("#model", Input).value = model_config.model
        self.query_one("#model_file", Input).value = model_config.model_file
