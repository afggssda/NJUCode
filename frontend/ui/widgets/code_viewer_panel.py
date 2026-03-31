from __future__ import annotations

from pathlib import Path

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static, TextArea
from textual.message import Message

class FileContextAdded(Message):
    """请求将当前文件加入对话上下文的事件"""
    def __init__(self, file_path: Path):
        super().__init__()
        self.file_path = file_path

class CodeViewerPanel(Vertical):
    BINDINGS = [
        ("ctrl+s", "save_file", "Save File")
    ]

    def __init__(self, **kwargs):
        """初始化代码查看/编辑面板。

        初始化阶段会设置当前文件状态、默认语言与告警标记，
        并准备扩展名到语法包提示名的映射表，
        以便在高亮不可用时给出准确安装建议。
        """
        super().__init__(**kwargs)
        self.current_file_path: Path | None = None
        self.current_language = "text"
        self._missing_language_warned = False
        self._language_pkg_hint = {
            "python": "tree-sitter-python",
            "javascript": "tree-sitter-javascript",
            "typescript": "tree-sitter-typescript",
            "tsx": "tree-sitter-typescript",
            "c": "tree-sitter-c",
            "cpp": "tree-sitter-cpp",
            "java": "tree-sitter-java",
            "go": "tree-sitter-go",
            "rust": "tree-sitter-rust",
            "json": "tree-sitter-json",
            "yaml": "tree-sitter-yaml",
            "markdown": "tree-sitter-markdown",
            "bash": "tree-sitter-bash",
            "html": "tree-sitter-html",
            "css": "tree-sitter-css",
            "sql": "tree-sitter-sql",
            "toml": "tree-sitter-toml",
        }

    def compose(self):
        """构建代码面板 UI。

        面板由标题、操作按钮行和文本编辑器三部分组成。
        编辑器默认启用行号与当前行高亮，
        并预设主题用于统一视觉风格。
        """
        yield Label("Code Viewer", classes="panel-title", id="code_title")
        with Horizontal(id="code_actions"):
            yield Button("Save", id="code_save_btn", variant="success", disabled=True)
            yield Button("Reload", id="code_reload_btn", disabled=True)
            yield Button("Ask in Chat", id="code_ask_btn", variant="primary", disabled=True)
        yield Static(
            """
█   █     ██████   █   █
██  █       ██     █   █
█ █ █       ██     █   █
█  ██    █  ██     █   █
█   █     ███       ███

从左侧 Explorer 选择一个文件开始编辑
            """.strip("\n"),
            id="code_empty_state",
        )
        yield TextArea.code_editor(
            "",
            id="code_editor",
            language=None,
            theme="monokai",
            show_line_numbers=True,
            highlight_cursor_line=True,
        )

    def on_mount(self) -> None:
        """挂载后进入空态：显示图标占位，不显示代码框。"""
        self._set_empty_state(True)

    def _set_empty_state(self, empty: bool) -> None:
        """切换空态与编辑态显示。"""
        self.query_one("#code_empty_state", Static).display = empty
        self.query_one("#code_editor", TextArea).display = not empty

    def _guess_language(self, file_path: Path) -> str:
        """根据文件扩展名推断语法高亮语言。

        Args:
            file_path: 目标文件路径。

        Returns:
            Textual 可识别的语言键；未知扩展名回退为 `text`。
        """
        ext = file_path.suffix.lower()
        mapping = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "tsx",
            ".jsx": "jsx",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".cxx": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".md": "markdown",
            ".sh": "bash",
            ".html": "html",
            ".css": "css",
            ".sql": "sql",
        }
        return mapping.get(ext, "text")

    def show_file(self, file_path: Path) -> None:
        """加载并展示指定文件内容。

        该方法会尝试使用推断语言开启高亮，
        若语言包缺失或初始化失败则自动降级为纯文本显示，
        并在必要时给出一次性提示，避免应用崩溃。
        """
        title = self.query_one("#code_title", Label)
        editor = self.query_one("#code_editor", TextArea)
        save_btn = self.query_one("#code_save_btn", Button)
        reload_btn = self.query_one("#code_reload_btn", Button)
        ask_btn = self.query_one("#code_ask_btn", Button)

        self.current_file_path = file_path
        title.update(f"Code Viewer - {file_path}")
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = file_path.read_text(errors="ignore")
        except OSError as error:
            self.app.notify(f"读取失败: {error}", severity="error")
            return

        requested_language = self._guess_language(file_path)
        available_languages = set(editor.available_languages)
        self.current_language = "text"
        editor.language = None
        editor.theme = "monokai"

        highlight_enabled = False
        if requested_language in available_languages:
            try:
                editor.language = requested_language
                editor.load_text(content)
                self.current_language = requested_language
                highlight_enabled = True
            except Exception:
                highlight_enabled = False
        elif requested_language != "text" and not self._missing_language_warned:
            pkg = self._language_pkg_hint.get(requested_language, f"tree-sitter-{requested_language}")
            self.app.notify(
                f"当前环境缺少 {requested_language} 语法包（建议安装 {pkg}），已降级为纯文本。",
                severity="warning",
            )
            self._missing_language_warned = True

        if not highlight_enabled:
            try:
                editor.language = None
                editor.load_text(content)
                self.current_language = "text"
            except Exception as error:
                self.app.notify(f"加载文件失败: {error}", severity="error")
                return
            if requested_language != "text" and not self._missing_language_warned:
                pkg = self._language_pkg_hint.get(requested_language, f"tree-sitter-{requested_language}")
                self.app.notify(
                    f"{requested_language} 高亮初始化失败（建议安装 {pkg}），已降级为纯文本。",
                    severity="warning",
                )
                self._missing_language_warned = True

            self._set_empty_state(False)
        save_btn.disabled = False
        reload_btn.disabled = False
        ask_btn.disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """处理代码面板操作按钮。

        - `Save`: 将编辑器内容写回当前文件。
        - `Reload`: 从磁盘重新加载当前文件内容。
        - `Ask in Chat`: 将文件上下文发往聊天框。

        写入失败会以通知形式提示错误原因。
        """
        if event.button.id == "code_save_btn" and self.current_file_path:
            self.action_save_file()
            return
        if event.button.id == "code_reload_btn" and self.current_file_path:
            self.show_file(self.current_file_path)
            return
        if event.button.id == "code_ask_btn" and self.current_file_path:
            self.post_message(FileContextAdded(self.current_file_path))

    def action_save_file(self) -> None:
        """保存当前文件内容。"""
        if self.current_file_path:
            editor = self.query_one("#code_editor", TextArea)
            try:
                self.current_file_path.write_text(editor.text, encoding="utf-8")
                self.app.notify(f"已保存: {self.current_file_path}")
            except OSError as error:
                self.app.notify(f"保存失败: {error}", severity="error")
