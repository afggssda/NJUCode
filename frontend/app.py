from __future__ import annotations

import os
import re
import asyncio
import json
from pathlib import Path
import sys
from threading import Event

from dotenv import load_dotenv
from textual import work, on
from textual.app import App
from textual.containers import Horizontal
from textual.widgets import DirectoryTree, Footer, Header, Label, TabPane, TabbedContent
from textual.widgets import TextArea

from .services.openai_client import OpenAICompatibleClient, OpenAIRequest
from .services.code_analysis import CodeAnalyzer
from .services.runtime_tools import run_hello_world
from .services.context_compressor import ContextCompressor
from .state import AppState
from .ui.widgets.chat_panel import ChatPanel, MessageSubmitted, StreamInterruptRequested
from .ui.widgets.code_viewer_panel import CodeViewerPanel, FileContextAdded
from .ui.widgets.config_panel import ConfigPanel, ConfigSaved, MirrorSelected
from .ui.widgets.file_tree_panel import FileTreePanel, WorkspaceChanged
from .ui.widgets.session_panel import (
    SessionCreateRequested,
    SessionDeleteRequested,
    SessionPanel,
    SessionRenameRequested,
    SessionSelected,
    SessionCompressRequested,
    SessionExportRequested,
    SessionImportRequested,
)
from .ui.widgets.splitter import SplitterDragEnded, SplitterDragged, VerticalSplitter
from .ui.widgets.tools_panel import HelloWorldRequested, ToolToggled, ToolsPanel
from .ui.widgets.tools_panel import AnalysisCommandRequested, SkillExecutionRequested
from .ui.widgets.skills_panel import SkillsPanel, SkillToggled, AuditLogRequested
from .ui.widgets.mcp_panel import MCPPanel, MCPServerConnectRequested, MCPToolToggled, MCPServerAddRequested
from .ui.widgets.patch_panel import (
    PatchPanel,
    PatchConfirmRequested,
    PatchRollbackRequested,
    PatchCancelRequested,
    PatchRefreshRequested,
    _PatchPreviewRequested,
)
from .services.code_extractor import extract_code_blocks


class NjuCodeApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "NJU Code (Textual Frontend)"
    SUB_TITLE = "Claude Code-like MVP"

    BINDINGS = [
        ("ctrl+n", "new_chat", "New Chat"),
        ("ctrl+h", "toggle_chat", "Show/Hide Chat"),
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
        self.analyzer = CodeAnalyzer(workspace_root)
        self.compressor = ContextCompressor(self.client, self.state.model_config)
        self.stream_cancel_event: Event | None = None
        self.stream_session_id: str | None = None
        self.stream_active = False
        self.mcp_loop: asyncio.AbstractEventLoop | None = None
        self.mcp_loop_ready = Event()
        self.left_ratio = self.state.left_ratio
        self.right_ratio = self.state.right_ratio
        self.default_chat_ratio = 0.34
        self.min_center_ratio = 0.20
        self.left_show_ratio = 0.12
        self.left_hide_ratio = 0.10
        self.right_show_ratio = 0.14
        self.right_hide_ratio = 0.12
        self.left_visible = self.left_ratio >= self.left_show_ratio
        self.right_visible = self.right_ratio >= self.right_show_ratio

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
            with TabbedContent(initial="code", id="center_tabs"):
                with TabPane("Code", id="code"):
                    yield CodeViewerPanel(id="code_view")
                with TabPane("Tools", id="tools"):
                    yield ToolsPanel(id="tools_panel")
                with TabPane("Skills", id="skills"):
                    yield SkillsPanel(id="skills_panel")
                with TabPane("MCP", id="mcp"):
                    yield MCPPanel(id="mcp_panel")
                with TabPane("Patch", id="patch"):
                    yield PatchPanel(id="patch_panel")
                with TabPane("Model", id="model"):
                    yield ConfigPanel(id="config_panel")
            yield VerticalSplitter(splitter_id="right", id="splitter_right")
            with TabbedContent(initial="chat", id="right_tabs"):
                with TabPane("Chat", id="chat"):
                    yield ChatPanel(id="chat_panel")
        with Horizontal(id="status_row"):
            yield Label("", id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        """界面挂载完成后的启动钩子。

        挂载后立即恢复持久化状态并刷新 UI，
        同时执行语法高亮可用性诊断与分栏宽度应用。
        该方法确保用户打开程序时看到的是可直接交互的稳定界面。
        """
        self.state.load()
        self.left_ratio = self.state.left_ratio
        self.right_ratio = self.state.right_ratio
        self.left_visible = self.left_ratio >= self.left_show_ratio
        self.right_visible = self.right_ratio >= self.right_show_ratio
        max_side_total = 1.0 - self.min_center_ratio
        side_total = self.left_ratio + self.right_ratio
        if side_total > max_side_total and side_total > 0:
            scale = max_side_total / side_total
            self.left_ratio *= scale
            self.right_ratio *= scale
        # Initialize skills system after analyzer is ready
        self.state.init_skills(self.analyzer)

        # Initialize MCP system
        self.state.init_mcp()

        # Initialize Patch/Rollback engine (WBS-4)
        self.state.init_patch_engine()

        self.refresh_ui()
        self._apply_pane_widths()
        self._update_status_bar()
        if (
            self.state.mcp_manager
            and any(
                server.enabled and server.auto_connect
                for server in self.state.mcp_manager.servers.values()
            )
        ):
            self._async_init_mcp_servers()
        # Populate patch panel with initial state
        self._refresh_patch_panel()

    def _detect_git_branch(self) -> str:
        """尝试读取当前工作区 git 分支名。"""
        head_file = self.state.workspace_root / ".git" / "HEAD"
        if not head_file.exists():
            return "no-git"
        try:
            head = head_file.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError:
            return "no-git"
        prefix = "ref: refs/heads/"
        if head.startswith(prefix):
            return head.replace(prefix, "", 1)
        return head[:7] if head else "detached"

    def _update_status_bar(self) -> None:
        """更新底部状态栏信息。"""
        branch = self._detect_git_branch()
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        model_name = self.state.model_config.model or "n/a"
        mirror = self.state.model_config.mirror or "custom"
        status = (
            f"Branch: {branch} | Python: {python_version} | Mirror: {mirror} | Model: {model_name}"
        )
        self.query_one("#status_bar", Label).update(status)

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
        if self.left_visible and self.left_ratio < self.left_hide_ratio:
            self.left_visible = False
        elif not self.left_visible and self.left_ratio >= self.left_show_ratio:
            self.left_visible = True

        if self.right_visible and self.right_ratio < self.right_hide_ratio:
            self.right_visible = False
        elif not self.right_visible and self.right_ratio >= self.right_show_ratio:
            self.right_visible = True

        left_hidden = not self.left_visible
        right_hidden = not self.right_visible

        effective_left = 0.0 if left_hidden else self.left_ratio
        effective_right = 0.0 if right_hidden else self.right_ratio

        center_ratio = 1.0 - effective_left - effective_right
        if center_ratio < self.min_center_ratio:
            center_ratio = self.min_center_ratio

        left_tabs = self.query_one("#left_tabs", TabbedContent)
        center_tabs = self.query_one("#center_tabs", TabbedContent)
        right_tabs = self.query_one("#right_tabs", TabbedContent)
        splitter_left = self.query_one("#splitter_left", VerticalSplitter)
        splitter_right = self.query_one("#splitter_right", VerticalSplitter)

        left_tabs.display = not left_hidden
        right_tabs.display = not right_hidden
        splitter_left.display = True
        splitter_right.display = True

        if left_hidden:
            splitter_left.styles.width = 2
            splitter_left.styles.min_width = 2
            splitter_left.update("<")
        else:
            splitter_left.styles.width = 1
            splitter_left.styles.min_width = 1
            splitter_left.update("")

        if right_hidden:
            splitter_right.styles.width = 2
            splitter_right.styles.min_width = 2
            splitter_right.update(">")
        else:
            splitter_right.styles.width = 1
            splitter_right.styles.min_width = 1
            splitter_right.update("")

        left_tabs.styles.width = f"{effective_left * 100:.2f}%"
        center_tabs.styles.width = f"{center_ratio * 100:.2f}%"
        right_tabs.styles.width = f"{effective_right * 100:.2f}%"

    def _toggle_chat_panel(self) -> None:
        """切换右侧聊天栏显示状态。"""
        if self.right_visible:
            self.right_ratio = 0.0
            self.right_visible = False
        else:
            # Expand chat to a stable default width while preserving center minimum width.
            max_right = 1.0 - self.left_ratio - self.min_center_ratio
            if max_right < self.right_show_ratio:
                self.left_ratio = max(0.0, 1.0 - self.min_center_ratio - self.default_chat_ratio)
                max_right = 1.0 - self.left_ratio - self.min_center_ratio

            target = min(self.default_chat_ratio, max_right)
            self.right_ratio = max(self.right_show_ratio, target)
            self.right_visible = True

        self._apply_pane_widths()
        self.state.left_ratio = self.left_ratio
        self.state.right_ratio = self.right_ratio
        self.state.save()

    def action_toggle_chat(self) -> None:
        """快捷键动作：显示或隐藏右侧聊天栏。"""
        self._toggle_chat_panel()

    def on_splitter_dragged(self, message: SplitterDragged) -> None:
        """处理分割条拖拽事件并更新布局比例。

        通过屏幕坐标推导新的左右栏目标宽度，
        同时结合最小宽度约束，防止任何一栏被压缩到不可用。
        计算完成后会立即触发 `_apply_pane_widths` 刷新界面。
        """
        total_width = max(self.size.width, 80)
        min_center = self.min_center_ratio

        x = self._clamp(message.screen_x / total_width, 0.0, 1.0)
        if message.splitter_id == "left":
            max_left = 1.0 - self.right_ratio - min_center
            if not self.left_visible and x > 0:
                self.left_visible = True
                self.left_ratio = self._clamp(max(x, self.left_show_ratio), 0.0, max_left)
            else:
                self.left_ratio = self._clamp(x, 0.0, max_left)
        elif message.splitter_id == "right":
            proposed_right = 1.0 - x
            max_right = 1.0 - self.left_ratio - min_center
            if not self.right_visible and proposed_right > 0:
                self.right_visible = True
                self.right_ratio = self._clamp(
                    max(proposed_right, self.right_show_ratio), 0.0, max_right
                )
            else:
                self.right_ratio = self._clamp(proposed_right, 0.0, max_right)

        self._apply_pane_widths()

    def on_splitter_drag_ended(self, _: SplitterDragEnded) -> None:
        """拖拽结束后保存最新分栏比例。"""
        self.state.left_ratio = self.left_ratio
        self.state.right_ratio = self.right_ratio
        self.state.save()

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

    def _refresh_ui_legacy(self) -> None:
        """将全局状态同步到各 UI 面板。

        该方法会统一刷新会话列表、聊天内容、工具开关、技能和 MCP 配置。
        若当前没有流式输出任务，还会将聊天状态恢复为 Idle。
        这是应用内部最核心的"状态 -> 视图"同步入口。
        """
        session_panel = self.query_one("#session_panel", SessionPanel)
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        tools_panel = self.query_one("#tools_panel", ToolsPanel)
        config_panel = self.query_one("#config_panel", ConfigPanel)
        skills_panel = self.query_one("#skills_panel", SkillsPanel)
        mcp_panel = self.query_one("#mcp_panel", MCPPanel)

        session_panel.refresh_sessions(self.state.sessions, self.state.active_session_id)
        chat_panel.render_messages(
            self.state.active_session.messages,
            self.state.active_session_id,
            session=self.state.active_session,
        )
        if not self.stream_active:
            chat_panel.set_busy(False, "Idle")
        tools_panel.refresh_tools(list(self.state.tools.values()))
        config_panel.load_config(self.state.model_config)
        skills_panel.refresh_skills(list(self.state.skills.values()))
        if self.state.mcp_manager:
            mcp_panel.refresh_servers(list(self.state.mcp_manager.servers.values()))
            mcp_panel.refresh_tools(self.state.mcp_manager.list_tools())
        self._update_status_bar()

    def _refresh_active_chat_view(self) -> None:
        """仅刷新当前会话相关视图，减少切换会话时的闪烁。"""
        session_panel = self.query_one("#session_panel", SessionPanel)
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        session_panel.refresh_sessions(self.state.sessions, self.state.active_session_id)
        chat_panel.render_messages(
            self.state.active_session.messages,
            self.state.active_session_id,
            session=self.state.active_session,
        )
        if not self.stream_active:
            chat_panel.set_busy(False, "Idle")

    def _workspace_files(self) -> list[Path]:
        """Return workspace files while skipping common generated directories."""
        files: list[Path] = []
        excluded = {".git", "venv", ".venv", "node_modules", "__pycache__"}
        for path in self.state.workspace_root.rglob("*"):
            if not path.is_file():
                continue
            if set(path.parts) & excluded:
                continue
            files.append(path)
        return files

    def _safe_read_file(self, file_path: Path) -> str:
        try:
            return file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _relative_workspace_path(self, file_path: Path) -> str:
        try:
            return str(file_path.relative_to(self.state.workspace_root)).replace("\\", "/")
        except ValueError:
            return str(file_path).replace("\\", "/")

    def _resolve_file_reference(self, raw_path: str, workspace_files: list[Path] | None = None) -> str | None:
        candidate = raw_path.strip().strip("\"'`")
        if not candidate:
            return None

        normalized = candidate.replace("\\", "/").lstrip("./")
        direct_path = self.state.workspace_root / normalized
        if direct_path.exists() and direct_path.is_file():
            return self._relative_workspace_path(direct_path)

        files = workspace_files if workspace_files is not None else self._workspace_files()
        lowered = normalized.lower()
        basename = Path(lowered).name

        exact_matches = [
            self._relative_workspace_path(path)
            for path in files
            if self._relative_workspace_path(path).lower() == lowered
        ]
        if exact_matches:
            return sorted(exact_matches, key=len)[0]

        suffix_matches = [
            self._relative_workspace_path(path)
            for path in files
            if self._relative_workspace_path(path).lower().endswith(f"/{lowered}")
            or Path(self._relative_workspace_path(path).lower()).name == basename
        ]
        if suffix_matches:
            return sorted(set(suffix_matches), key=len)[0]
        return None

    def _extract_filename_from_prose(self, text: str, workspace_files: list[Path]) -> str | None:
        """Scan prose text for a filename mention and resolve it to a workspace path.

        Looks for patterns like `demo/1.py`, **demo/1.py**, or bare tokens that
        look like paths, returning the first one that resolves to a real file.
        Checks path-like tokens first (contain / or backslash) before bare filenames so
        that demo/1.py beats a same-named file in a different directory.
        """
        # Extract tokens that look like paths (contain a separator or have an extension)
        # Use the last occurrence of each token — the one closest to the code block
        path_tokens = re.findall(r"[`*\"']?([\w./\\-]+\.[A-Za-z0-9]{1,8})[`*\"']?", text)
        # Deduplicate preserving last occurrence order, then prioritise tokens with a
        # directory separator (unambiguous) before bare filenames
        seen_tokens: set[str] = set()
        unique_last: list[str] = []
        for t in reversed(path_tokens):
            if t not in seen_tokens:
                seen_tokens.add(t)
                unique_last.append(t)
        ordered = sorted(unique_last, key=lambda t: (0 if ("/" in t or "\\" in t) else 1))
        for token in ordered:
            resolved = self._resolve_file_reference(token, workspace_files)
            if resolved:
                return resolved
        return None

    def _extract_new_file_from_prose(self, text: str) -> str | None:
        """Extract a new file path from prose when the file doesn't exist yet.

        Looks for patterns like "创建 `path/to/file.py`" or "新建文件 path/to/file.py"
        that indicate the LLM is suggesting a new file.
        """
        path_tokens = re.findall(r"[`*\"']?([\w./\\-]+\.[A-Za-z0-9]{1,8})[`*\"']?", text)
        for token in reversed(path_tokens):
            normalized = token.replace("\\", "/").lstrip("./")
            if "/" in normalized and not normalized.startswith(".."):
                return normalized
        return None

    _NON_PATCHABLE_LANGS = frozenset({
        "bash", "shell", "sh", "zsh", "console", "terminal",
        "text", "plaintext", "output", "log", "diff",
    })

    _SHELL_CMD_RE = re.compile(
        r"^\s*(?:\$|#|>|>>>)\s"
        r"|^\s*(?:pip|npm|yarn|pnpm|cargo|go|git|cd|ls|cat|mkdir|rm|cp|mv|curl|wget|docker|brew|apt|sudo|echo|export)\s",
    )

    _OUTPUT_HINT_RE = re.compile(
        r"(?:输出|运行结果|执行结果|期望输出|预期输出|结果如下|结果为|打印|显示"
        r"|output|expected output|result|prints?|produces?|returns?|shows?)"
        r"\s*[:：]?\s*$",
        re.IGNORECASE,
    )

    def _is_patchable_block(self, block) -> bool:
        """Return False for blocks that are clearly not file content (commands, output, etc.)."""
        lang = (block.language or "").lower()
        if lang in self._NON_PATCHABLE_LANGS:
            return False
        code = block.code.strip()
        if not code:
            return False
        lines = code.splitlines()
        if len(lines) <= 2 and self._SHELL_CMD_RE.match(lines[0]):
            return False
        return True

    def _is_output_block(self, preceding_text: str, block) -> bool:
        """Return True if the block appears to be example output rather than code to apply."""
        lang = (block.language or "").lower()
        if lang in ("output", "text", "plaintext", "log", "console"):
            return True
        last_line = preceding_text.rstrip().rsplit("\n", 1)[-1] if preceding_text.strip() else ""
        if self._OUTPUT_HINT_RE.search(last_line):
            return True
        return False

    def _extract_file_candidates(self, content: str, workspace_files: list[Path]) -> list[str]:
        candidates: list[str] = []
        seen: set[str] = set()

        for match in re.finditer(r"@([\w\./\\-]+)", content):
            resolved = self._resolve_file_reference(match.group(1), workspace_files)
            if resolved and resolved not in seen:
                seen.add(resolved)
                candidates.append(resolved)

        for raw_token in re.findall(r"\b[\w./\\-]+\.[A-Za-z0-9]{1,8}\b", content):
            resolved = self._resolve_file_reference(raw_token, workspace_files)
            if resolved and resolved not in seen:
                seen.add(resolved)
                candidates.append(resolved)

        return candidates[:6]

    def _extract_symbol_candidates(self, content: str) -> list[str]:
        stopwords = {
            "main",
            "python",
            "file",
            "code",
            "function",
            "class",
            "method",
            "content",
            "what",
            "this",
            "that",
        }
        candidates: list[str] = []
        seen: set[str] = set()
        patterns = [
            r"`([A-Za-z_][A-Za-z0-9_]*)`",
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)",
            r"(?:\u51fd\u6570|\u65b9\u6cd5|\u7c7b|class|method|function|def)\s*`?([A-Za-z_][A-Za-z0-9_]*)`?",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, content, flags=re.IGNORECASE):
                candidate = match.group(1)
                lowered = candidate.lower()
                if lowered in stopwords or len(candidate) < 3:
                    continue
                if candidate not in seen:
                    seen.add(candidate)
                    candidates.append(candidate)

        for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", content):
            if token in seen or token.lower() in stopwords:
                continue
            if "_" in token or any(ch.isupper() for ch in token[1:]):
                seen.add(token)
                candidates.append(token)

        return candidates[:5]

    def _rank_symbol_hits(
        self,
        symbol: str,
        hits: list[dict[str, object]],
        content: str,
        referenced_paths: list[str],
    ) -> list[dict[str, object]]:
        scored_hits = self._score_symbol_hits(symbol, hits, content, referenced_paths)
        scored_hits.sort(
            key=lambda item: (
                -item[0],
                str(item[1].get("path", "")),
                int(item[1].get("line", 0)),
            )
        )
        return [hit for _, hit in scored_hits]

    def _score_symbol_hits(
        self,
        symbol: str,
        hits: list[dict[str, object]],
        content: str,
        referenced_paths: list[str],
    ) -> list[tuple[float, dict[str, object]]]:
        query_lower = content.lower()
        query_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", content)
            if len(token) >= 2
        }
        referenced = {path.lower() for path in referenced_paths}

        scored_hits: list[tuple[float, dict[str, object]]] = []
        for hit in hits:
            path = str(hit.get("path", ""))
            path_lower = path.lower()
            basename = Path(path_lower).name
            path_parts = {
                part
                for part in re.split(r"[/._\\-]+", path_lower)
                if part
            }
            kind = str(hit.get("kind", "")).lower()

            score = 0.0
            if path_lower in referenced:
                score += 6.0
            if any(path_lower.endswith(ref) or ref.endswith(path_lower) for ref in referenced):
                score += 3.0

            overlap = len(query_tokens & path_parts)
            score += min(overlap, 4) * 1.25

            if basename in query_lower:
                score += 2.0
            if symbol.lower() in basename:
                score += 1.5
            if str(hit.get("name", "")) == symbol:
                score += 1.0

            if "class" in query_lower or "\u7c7b" in query_lower:
                score += 1.0 if kind == "class" else -0.5
            if any(word in query_lower for word in ("function", "method", "def", "\u51fd\u6570", "\u65b9\u6cd5")):
                score += 1.0 if "def" in kind else -0.5

            line = int(hit.get("line", 999999))
            score += max(0.0, 1.0 - min(line, 400) / 400.0)
            score += max(0.0, 1.0 - min(len(path), 120) / 120.0)

            scored_hits.append((score, hit))
        return scored_hits

    def _select_symbol_hits(
        self,
        symbol: str,
        hits: list[dict[str, object]],
        content: str,
        referenced_paths: list[str],
    ) -> list[dict[str, object]]:
        scored_hits = self._score_symbol_hits(symbol, hits, content, referenced_paths)
        scored_hits.sort(
            key=lambda item: (
                -item[0],
                str(item[1].get("path", "")),
                int(item[1].get("line", 0)),
            )
        )
        if not scored_hits:
            return []
        ranked_hits = [hit for _, hit in scored_hits]
        if len(scored_hits) == 1:
            return ranked_hits[:1]
        top_score = scored_hits[0][0]
        second_score = scored_hits[1][0]
        return ranked_hits[:1] if top_score - second_score >= 2.0 else ranked_hits[:2]

    def _build_auto_contexts(self, content: str) -> list[tuple[str, str]]:
        workspace_files = self._workspace_files()
        file_contexts: list[tuple[str, str]] = []
        attached_paths: set[str] = set()
        notes: list[str] = []
        file_candidates = self._extract_file_candidates(content, workspace_files)

        def attach_file(rel_path: str, reason: str) -> None:
            if rel_path in attached_paths:
                return
            full_path = self.state.workspace_root / rel_path
            text = self._safe_read_file(full_path)
            if not text:
                return
            attached_paths.add(rel_path)
            file_contexts.append((rel_path, text))
            notes.append(f"{reason}: {rel_path}")

        for rel_path in file_candidates:
            attach_file(rel_path, "file_match")
            summary = self.analyzer.summarize_file(rel_path)
            file_contexts.append((f"[AUTO-SUMMARY] {rel_path}", self.analyzer.to_text(summary)))

        for symbol in self._extract_symbol_candidates(content):
            result = self.analyzer.symbol_search(symbol)
            hits = self._select_symbol_hits(symbol, result.get("hits", []), content, file_candidates)
            if not hits:
                continue
            notes.append(
                "symbol_match: "
                + ", ".join(f"{hit['name']} @ {hit['path']}:{hit['line']}" for hit in hits)
            )
            for hit in hits:
                snippet = (
                    f"Detected symbol `{hit['name']}`.\n"
                    f"Kind: {hit['kind']}\n"
                    f"File: {hit['path']}:{hit['line']}\n"
                    f"Local context:\n{hit['context']}"
                )
                file_contexts.append(
                    (f"[AUTO-SYMBOL] {hit['name']} @ {hit['path']}:{hit['line']}", snippet)
                )
                attach_file(hit["path"], "symbol_source")

        if notes:
            file_contexts.insert(
                0,
                (
                    "[AUTO-CONTEXT]",
                    "Automatically attached local context for this user message:\n- "
                    + "\n- ".join(notes),
                ),
            )

        skill_context = self.state.build_agent_skill_context(content)
        if skill_context:
            file_contexts.insert(0, ("[AGENT-SKILLS]", skill_context))

        deduped: list[tuple[str, str]] = []
        seen_labels: set[str] = set()
        for label, text in file_contexts:
            if label in seen_labels:
                continue
            seen_labels.add(label)
            deduped.append((label, text))
        return deduped

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
        self._refresh_active_chat_view()

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

    def on_session_compress_requested(self, message: SessionCompressRequested) -> None:
        """响应手动压缩请求：压缩指定会话的历史消息并刷新界面。"""
        summary = self.state.compress_session(message.session_id, self.compressor)
        if summary is None:
            self.notify("消息数量不足，无需压缩。")
        else:
            self.notify("历史消息已压缩，摘要已生成。")
        self.state.save()
        self.refresh_ui()

    def on_session_export_requested(self, message: SessionExportRequested) -> None:
        """响应导出请求：将会话导出到 .nju_code/exports/ 目录。"""
        export_dir = self.state.workspace_root / ".nju_code" / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"session_{message.session_id[:8]}.json"
        export_path = export_dir / filename
        try:
            self.state.export_session(message.session_id, export_path)
            self.notify(f"会话已导出至: {export_path}")
        except ValueError as exc:
            self.notify(f"导出失败: {exc}", severity="error")

    def on_session_import_requested(self, _: SessionImportRequested) -> None:
        """响应导入请求：从 .nju_code/exports/ 目录导入最新一个会话文件。"""
        export_dir = self.state.workspace_root / ".nju_code" / "exports"
        json_files = sorted(
            export_dir.glob("session_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not json_files:
            self.notify(
                "未找到可导入的会话文件（.nju_code/exports/session_*.json）",
                severity="warning",
            )
            return
        try:
            session = self.state.import_session(json_files[0])
            self.state.active_session_id = session.session_id
            self.state.save()
            self.refresh_ui()
            self.notify(f"已导入会话: {session.title}")
        except ValueError as exc:
            self.notify(f"导入失败: {exc}", severity="error")

    # ------------------------------------------------------------------
    # Patch/Rollback event handlers (WBS-4)
    # ------------------------------------------------------------------

    def _refresh_patch_panel(self) -> None:
        """Push current patch state into PatchPanel."""
        try:
            panel = self.query_one("#patch_panel", PatchPanel)
        except Exception:
            return

        pending = self.state.get_pending_patches()
        pending_items = [
            {
                "task_id": t.task_id,
                "summary": t.summary_line,
                "description": t.description,
                "files_count": len(t.operations),
                # single-operation tasks: expose op type so panel can split lists
                "operation_type": t.operations[0].operation_type if len(t.operations) == 1 else "modify",
            }
            for t in pending
        ]
        panel.load_pending(pending_items)

        history = self.state.get_patch_history(limit=20)
        history_items = [
            {
                "task_id": t.task_id,
                "summary": t.summary_line,
                "reversible": t.is_reversible,
                "diff": "\n".join(op.diff for op in t.operations if op.diff),
            }
            for t in history
            if t not in pending
        ]
        panel.load_history(history_items)

        # Auto-show diff only when no task is selected yet; preserve selection on refresh
        if pending and not panel._selected_task_id:
            diff_text = self.state.preview_patch(pending[0].task_id)
            panel.show_diff(diff_text)

    def on_patch_refresh_requested(self, _: PatchRefreshRequested) -> None:
        """Reload patch panel data from state."""
        self._refresh_patch_panel()

    def on_patch_confirm_requested(self, message: PatchConfirmRequested) -> None:
        """User confirmed a patch — apply it and refresh."""
        result = self.state.apply_patch(message.task_id)
        if result is None:
            self.notify("Patch engine not initialized.", severity="error")
            return
        if result.success:
            files = ", ".join(result.files_modified[:3])
            extra = f" (+{len(result.files_modified) - 3} more)" if len(result.files_modified) > 3 else ""
            self.notify(f"Patch applied: {files}{extra}")
            self.state.save()
        else:
            self.notify(f"Patch failed: {result.error_message}", severity="error")
        self._refresh_patch_panel()

    def on__patch_preview_requested(self, message: _PatchPreviewRequested) -> None:
        """User selected a pending task — show its diff without touching state."""
        diff_text = self.state.preview_patch(message.task_id)
        try:
            panel = self.query_one("#patch_panel", PatchPanel)
            panel.show_diff(diff_text or "(no diff available)")
        except Exception:
            pass

    def on_patch_rollback_requested(self, message: PatchRollbackRequested) -> None:
        """User requested rollback — restore files from backup.

        If the rollback involves deleting files (undoing a 'create' operation),
        require explicit user confirmation before proceeding.
        """
        if not self.state._patch_history_store:
            self.notify("Patch engine not initialized.", severity="error")
            return

        task = self.state._patch_history_store.get_task(message.task_id)
        if not task:
            self.notify("Patch task not found.", severity="error")
            return

        # Check if rollback will delete files (undo create operations)
        create_ops = [op for op in task.operations if op.operation_type == "create"]
        if create_ops and not message.confirmed:
            files_to_delete = ", ".join(op.file_path for op in create_ops[:5])
            extra = f" (+{len(create_ops) - 5} more)" if len(create_ops) > 5 else ""
            self.notify(
                f"Rollback will DELETE: {files_to_delete}{extra}. Click Rollback again to confirm.",
                severity="warning",
            )
            try:
                panel = self.query_one("#patch_panel", PatchPanel)
                panel.set_status(
                    f"Confirm: rollback will delete {len(create_ops)} file(s). Click Rollback again.",
                    error=True,
                )
                panel._pending_rollback_confirm = message.task_id
                panel._set_rollback_button(True)
            except Exception:
                pass
            return

        # Clear the confirmation message before executing
        try:
            panel = self.query_one("#patch_panel", PatchPanel)
            panel.set_status("")
            panel._pending_rollback_confirm = None
        except Exception:
            pass

        result = self.state.rollback_patch(message.task_id)
        if result is None:
            self.notify("Patch engine not initialized.", severity="error")
            return
        if result.success:
            files = ", ".join(result.files_restored[:3])
            extra = f" (+{len(result.files_restored) - 3} more)" if len(result.files_restored) > 3 else ""
            self.notify(f"Rollback complete: {files}{extra}")
            self.state.save()
        else:
            self.notify(f"Rollback failed: {result.error_message}", severity="error")
        self._refresh_patch_panel()

    def on_patch_cancel_requested(self, message: PatchCancelRequested) -> None:
        """User cancelled a pending patch task."""
        ok, reason = self.state.cancel_patch(message.task_id)
        if ok:
            self.notify("Patch cancelled.")
        else:
            self.notify(f"Cannot cancel: {reason}", severity="warning")
        self._refresh_patch_panel()

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

        # Local analysis commands are executed in-process and do not call the model API.
        if message.content.strip().startswith("/"):
            self._run_analysis_command_and_render(message.content.strip())
            return

        self.state.append_message("assistant", "")
        self.state.save()
        self.query_one("#chat_panel", ChatPanel).render_messages(
            self.state.active_session.messages,
            self.state.active_session_id,
            session=self.state.active_session,
        )

        self.stream_active = True
        self.stream_session_id = session_id
        self.stream_cancel_event = Event()
        self.query_one("#chat_panel", ChatPanel).set_busy(True, "正在等待模型响应...")

        file_contexts = self._build_auto_contexts(message.content)

        # 用 build_context_messages 构建上下文（含摘要 + 近期消息），排除最后一条占位 assistant
        history_dicts = self.state.build_context_messages(session_id)
        if history_dicts and history_dicts[-1].get("role") == "assistant" and not history_dicts[-1].get("content"):
            history_dicts = history_dicts[:-1]
        request_messages = history_dicts

        request = OpenAIRequest(
            base_url=self.state.model_config.base_url,
            api_key=self.state.model_config.api_key,
            model=self.state.model_config.model,
            messages=request_messages,
            model_file=self.state.model_config.model_file,
            file_contexts=file_contexts,
        )
        self._stream_assistant_reply(request, session_id, self.stream_cancel_event)

    def on_stream_interrupt_requested(self, _: StreamInterruptRequested) -> None:
        """响应聊天面板的中断请求事件。"""
        self.action_interrupt_stream()

    @on(WorkspaceChanged)
    def on_workspace_changed(self, event: WorkspaceChanged) -> None:
        """处理工作区变更事件。"""
        self.state.workspace_root = event.new_path
        self.analyzer.set_workspace_root(event.new_path)
        self.state.save()

    @on(FileContextAdded)
    def on_file_context_added(self, event: FileContextAdded) -> None:
        """从代码视图中把文件快捷加入聊天对话框的上下文。"""
        # 将绝对路径转为相对于工作区的相对路径（如果可能）
        try:
            rel_path = Path(event.file_path).relative_to(self.state.workspace_root)
        except ValueError:
            rel_path = event.file_path
        
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        chat_panel.append_to_input(f"@{rel_path} ")

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
        self.query_one("#center_tabs", TabbedContent).active = "code"

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
        self.query_one("#chat_panel", ChatPanel).render_messages(
            self.state.active_session.messages,
            self.state.active_session_id,
            session=self.state.active_session,
        )
        self.notify(result)

    @on(AnalysisCommandRequested)
    def on_analysis_command_requested(self, message: AnalysisCommandRequested) -> None:
        """处理 Tools 面板发起的本地分析命令。"""
        if self.stream_active:
            self.notify("模型正在输出中，请先中断或等待完成。")
            return
        self.state.append_message("user", message.command)
        self._run_analysis_command_and_render(message.command)

    @on(SkillExecutionRequested)
    def on_skill_execution_requested(self, message: SkillExecutionRequested) -> None:
        """处理直接技能执行请求。"""
        if self.stream_active:
            self.notify("模型正在输出中，请先中断或等待完成。")
            return

        result = self.state.execute_skill(message.skill_id, message.params)

        # Display result in chat
        self.state.append_message("user", f"执行技能: {message.skill_id}")
        if result.get("type") == "error":
            self.state.append_message("assistant", f"[错误] {result.get('error')}")
        else:
            text_view = self.analyzer.to_text(result)
            self.state.append_message("assistant", text_view)

        self.state.save()
        self.query_one("#chat_panel", ChatPanel).render_messages(
            self.state.active_session.messages,
            self.state.active_session_id,
            session=self.state.active_session,
        )

    @on(SkillToggled)
    def on_skill_toggled(self, message: SkillToggled) -> None:
        """处理技能开关变化。"""
        self.state.update_skill(message.skill_id, message.enabled)
        self.state.save()
        # Refresh skills panel
        skills_panel = self.query_one("#skills_panel", SkillsPanel)
        skills_panel.refresh_skills(list(self.state.skills.values()))
        self.notify(f"Skill {message.skill_id} {'enabled' if message.enabled else 'disabled'}")

    @on(AuditLogRequested)
    def on_audit_log_requested(self) -> None:
        """处理审计日志查看请求。"""
        if self.state._audit_logger:
            stats = self.state._audit_logger.get_statistics()
            self.notify(f"Audit Log: {stats['total_executions']} executions, {stats['success_rate']:.1f}% success")

    def refresh_ui(self) -> None:
        """将全局状态同步到各 UI 面板。

        该方法会统一刷新会话列表、聊天内容、工具开关和模型配置。
        若当前没有流式输出任务，还会将聊天状态恢复为 Idle。
        这是应用内部最核心的"状态 -> 视图"同步入口。
        """
        session_panel = self.query_one("#session_panel", SessionPanel)
        chat_panel = self.query_one("#chat_panel", ChatPanel)
        tools_panel = self.query_one("#tools_panel", ToolsPanel)
        config_panel = self.query_one("#config_panel", ConfigPanel)
        skills_panel = self.query_one("#skills_panel", SkillsPanel)
        mcp_panel = self.query_one("#mcp_panel", MCPPanel)

        session_panel.refresh_sessions(self.state.sessions, self.state.active_session_id)
        chat_panel.render_messages(
            self.state.active_session.messages,
            self.state.active_session_id,
            session=self.state.active_session,
        )
        if not self.stream_active:
            chat_panel.set_busy(False, "Idle")
        tools_panel.refresh_tools(list(self.state.tools.values()))
        config_panel.load_config(self.state.model_config)
        skills_panel.refresh_skills(list(self.state.skills.values()))
        if self.state.mcp_manager:
            mcp_panel.refresh_servers(list(self.state.mcp_manager.servers.values()))
            mcp_panel.refresh_tools(self.state.mcp_manager.list_tools())
        self._update_status_bar()

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
        self._update_status_bar()
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
                self.query_one("#chat_panel", ChatPanel).update_last_message(session.messages[-1])
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
            chat_panel.render_messages(target_session.messages, session_id, session=target_session)
            chat_panel.set_busy(False, "Idle")
        # 流结束后检查是否需要自动压缩
        did_compress = self.state.auto_compress_if_needed(self.compressor)
        if did_compress:
            self.notify("上下文过长，已自动压缩历史消息。")

        # Extract code blocks from the completed assistant reply and queue as patches
        if target_session and target_session.messages and not cancelled and not error_message:
            last_msg = target_session.messages[-1]
            if last_msg.role == "assistant" and last_msg.content.strip():
                self._extract_and_queue_patches(last_msg.content, session_id)

        self.state.save()

    _LANG_TO_EXT: dict[str, str] = {
        "python": ".py", "javascript": ".js", "typescript": ".ts",
        "jsx": ".jsx", "tsx": ".tsx", "json": ".json",
        "yaml": ".yaml", "yml": ".yaml", "bash": ".sh", "shell": ".sh",
        "html": ".html", "css": ".css", "sql": ".sql", "go": ".go",
        "rust": ".rs", "java": ".java", "cpp": ".cpp", "c": ".c",
        "toml": ".toml",
    }

    def _extract_and_queue_patches(self, response_text: str, session_id: str) -> None:
        """Parse code blocks from an LLM reply and create pending PatchTasks.

        Blocks with a filename hint are matched directly.  Blocks without a
        filename hint are matched against files recently mentioned in the
        conversation, filtered by language/extension.
        """
        blocks = extract_code_blocks(response_text)
        if not blocks:
            return

        # Build ordered list of context files from recent messages (newest last = highest priority)
        target_session = next((s for s in self.state.sessions if s.session_id == session_id), None)
        context_files: list[str] = []
        workspace_files = self._workspace_files()
        if target_session:
            for msg in target_session.messages[-8:]:
                for c in self._extract_file_candidates(msg.content, workspace_files):
                    if c not in context_files:
                        context_files.append(c)

        file_changes: dict[str, tuple[str, str]] = {}
        skipped = 0

        for block in blocks:
            preceding = response_text[max(0, block.start_pos - 300):block.start_pos]

            # Skip blocks that are example output, not code modifications
            if self._is_output_block(preceding, block):
                skipped += 1
                continue

            resolved: str | None = None

            if block.filename:
                resolved = self._resolve_file_reference(block.filename, workspace_files)
                # If the file doesn't exist yet, treat the hint as a new file path
                if not resolved:
                    normalized = block.filename.strip().strip("\"'`").replace("\\", "/").lstrip("./")
                    if normalized and ("/" in normalized or "." in normalized):
                        resolved = normalized

            # Scan the text immediately before this block for a filename mention
            # (LLMs typically write "修改 `demo/1.py`:" before the code fence)
            if not resolved:
                resolved = self._extract_filename_from_prose(preceding, workspace_files)

            # Also check prose for new file creation hints (file doesn't exist yet)
            if not resolved:
                resolved = self._extract_new_file_from_prose(preceding)

            if not resolved and context_files:
                if self._is_patchable_block(block):
                    lang = (block.language or "").lower()
                    ext = self._LANG_TO_EXT.get(lang, "")
                    if ext:
                        # Prefer most recently mentioned file with matching extension
                        matches = [f for f in reversed(context_files) if f.endswith(ext)]
                        resolved = matches[0] if matches else None
                    if not resolved and len(context_files) == 1:
                        # Only one file in context — safe to assume it's the target
                        resolved = context_files[0]

            if not resolved:
                skipped += 1
                continue

            abs_path = self.state.workspace_root / resolved
            try:
                old_content = abs_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                old_content = ""

            new_content = block.code
            if old_content == new_content:
                continue

            # Last block for a given file wins
            file_changes[resolved] = (old_content, new_content)

        if file_changes:
            created_files: list[str] = []
            for file_path, (old_content, new_content) in file_changes.items():
                task = self.state.create_patch(
                    file_changes={file_path: (old_content, new_content)},
                    description=f"AI suggestion for {file_path}",
                    is_ai_generated=True,
                )
                if task:
                    created_files.append(file_path)
            if created_files:
                files_str = ", ".join(created_files[:3])
                extra = f" (+{len(created_files) - 3} more)" if len(created_files) > 3 else ""
                self.notify(f"AI patch queued: {files_str}{extra} — review in Patch tab")
                self._refresh_patch_panel()
                self.query_one("#center_tabs", TabbedContent).active = "patch"

        if skipped and not file_changes:
            self.notify(
                f"{skipped} code block(s) skipped — add a filename hint to the code fence header (e.g. ```python frontend/app.py)",
                severity="information",
            )

    def _apply_last_reply_to_file(self, target_file: str) -> None:
        """Create a PatchTask from the last assistant message's code blocks.

        Called by the /patch <file> command.  Finds the most recent assistant
        message, extracts all code blocks (regardless of filename hint), merges
        their code, and queues a patch against *target_file*.
        """
        resolved = self._resolve_file_reference(target_file)
        if not resolved:
            self.state.append_message(
                "assistant",
                f"[系统提示] 找不到文件: {target_file}",
            )
            self.state.save()
            self.query_one("#chat_panel", ChatPanel).render_messages(
                self.state.active_session.messages, self.state.active_session_id
            )
            return

        # Find the last assistant message
        last_assistant_content = ""
        for msg in reversed(self.state.active_session.messages):
            if msg.role == "assistant" and msg.content.strip():
                last_assistant_content = msg.content
                break

        if not last_assistant_content:
            self.state.append_message("assistant", "[系统提示] 没有找到上一条 AI 回复。")
            self.state.save()
            self.query_one("#chat_panel", ChatPanel).render_messages(
                self.state.active_session.messages, self.state.active_session_id
            )
            return

        blocks = extract_code_blocks(last_assistant_content)
        if not blocks:
            self.state.append_message("assistant", "[系统提示] 上一条 AI 回复中没有找到代码块。")
            self.state.save()
            self.query_one("#chat_panel", ChatPanel).render_messages(
                self.state.active_session.messages, self.state.active_session_id
            )
            return

        # Use the first code block that has content; if multiple, pick the
        # largest one (most likely to be the full file replacement).
        best_block = max(blocks, key=lambda b: len(b.code))
        new_content = best_block.code

        abs_path = self.state.workspace_root / resolved
        try:
            old_content = abs_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            old_content = ""

        if old_content == new_content:
            self.state.append_message(
                "assistant",
                f"[系统提示] 代码与 {resolved} 当前内容完全相同，无需创建补丁。",
            )
            self.state.save()
            self.query_one("#chat_panel", ChatPanel).render_messages(
                self.state.active_session.messages, self.state.active_session_id
            )
            return

        task = self.state.create_patch(
            file_changes={resolved: (old_content, new_content)},
            description=f"AI suggestion for {resolved}",
            is_ai_generated=True,
        )
        if task:
            self.state.append_message(
                "assistant",
                f"[系统提示] 已为 {resolved} 创建补丁，请在 Patch 标签页中确认应用。",
            )
            self._refresh_patch_panel()
            self.query_one("#center_tabs", TabbedContent).active = "patch"
        else:
            self.state.append_message("assistant", "[系统提示] 创建补丁失败，Patch 引擎未初始化。")

        self.state.save()
        self.query_one("#chat_panel", ChatPanel).render_messages(
            self.state.active_session.messages, self.state.active_session_id
        )

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

        # 若中断，保存当前输入内容以便恢复
        if cancelled:
            try:
                input_content = self.query_one("#chat_input", Input).value
                if input_content.strip():
                    self.state.mark_interrupted(session_id, input_content)
            except Exception:
                pass

        self.call_from_thread(self._finish_stream, session_id, cancelled, error_message)

    def _run_analysis_command_and_render(self, command: str) -> None:
        """执行本地分析命令并将结果回写到聊天视图。

        Now uses Skills system for command execution.
        """
        if command == "/mcp" or command.startswith("/mcp "):
            parts = command.split(maxsplit=2)
            if len(parts) < 2:
                self.state.append_message(
                    "assistant",
                    "Usage: /mcp <mcp.server.tool> [json-params]",
                )
            else:
                params: dict[str, object] = {}
                if len(parts) == 3 and parts[2].strip():
                    try:
                        parsed = json.loads(parts[2])
                        if not isinstance(parsed, dict):
                            raise ValueError("params must be a JSON object")
                        params = parsed
                    except Exception as error:
                        self.state.append_message("assistant", f"[MCP error] {error}")
                        self.state.save()
                        self.query_one("#chat_panel", ChatPanel).render_messages(
                            self.state.active_session.messages, self.state.active_session_id
                        )
                        return
                payload = self.state.execute_mcp_tool(parts[1], params)
                self.state.append_message("assistant", self.analyzer.to_text(payload))
            self.state.save()
            self.query_one("#chat_panel", ChatPanel).render_messages(
                self.state.active_session.messages, self.state.active_session_id
            )
            return

        # /patch <file> — apply last AI reply's code blocks to the given file
        if command.startswith("/patch "):
            target = command[len("/patch "):].strip()
            if target:
                self._apply_last_reply_to_file(target)
            else:
                self.state.append_message(
                    "assistant",
                    "[系统提示] 用法: /patch <文件路径>  例如: /patch frontend/app.py",
                )
                self.state.save()
                self.query_one("#chat_panel", ChatPanel).render_messages(
                    self.state.active_session.messages, self.state.active_session_id
                )
            return

        # Execute via skills system
        payload = self.state.execute_skill_command(command)

        if payload.get("type") == "help":
            commands = payload.get("commands", [])
            text = "[分析命令帮助]\n" + "\n".join(f"- {cmd}" for cmd in commands)
            self.state.append_message("assistant", text)
        elif payload.get("type") == "error":
            self.state.append_message(
                "assistant",
                f"[系统提示] 分析命令失败: {payload.get('error')}",
            )
        else:
            # Use analyzer's text formatter for output
            text_view = self.analyzer.to_text(payload)
            self.state.append_message("assistant", text_view)

        self.state.save()
        self.query_one("#chat_panel", ChatPanel).render_messages(
            self.state.active_session.messages, self.state.active_session_id
        )

    @work(thread=True)
    def _async_init_mcp_servers(self) -> None:
        """Initialize MCP server connections in background thread.

        Uses asyncio event loop to connect enabled servers.
        Reports results via notifications.
        """
        loop = asyncio.new_event_loop()
        self.mcp_loop = loop
        self.mcp_loop_ready.set()
        asyncio.set_event_loop(loop)

        try:
            if self.state.mcp_manager:
                self.state.mcp_manager.loop = loop
                results = loop.run_until_complete(
                    self.state.mcp_manager.connect_all_enabled()
                )

                connected = [s for s, ok in results.items() if ok]
                failed = [s for s, ok in results.items() if not ok]

                if connected:
                    self.call_from_thread(
                        self.notify, f"MCP servers connected: {', '.join(connected)}"
                    )
                if failed:
                    self.call_from_thread(
                        self.notify, f"MCP connection failed: {', '.join(failed)}", severity="warning"
                    )

                # Refresh MCP panel
                self.call_from_thread(self._refresh_mcp_panel)

            loop.run_forever()
        finally:
            if self.state.mcp_manager:
                self.state.mcp_manager.loop = None
            self.mcp_loop = None
            loop.close()

    def _refresh_mcp_panel(self) -> None:
        """Refresh MCP panel UI after connection changes."""
        mcp_panel = self.query_one("#mcp_panel", MCPPanel)
        if self.state.mcp_manager:
            mcp_panel.refresh_servers(list(self.state.mcp_manager.servers.values()))
            mcp_panel.refresh_tools(self.state.mcp_manager.list_tools())

    @on(MCPServerConnectRequested)
    def on_mcp_server_connect_requested(self, message: MCPServerConnectRequested) -> None:
        """Handle MCP server connect/disconnect request."""
        async def _handle():
            if self.state.mcp_manager:
                if message.connect:
                    success = await self.state.mcp_manager.connect_server(message.server_id)
                    self.call_from_thread(
                        self.notify,
                        f"MCP server {message.server_id} {'connected' if success else 'failed to connect'}"
                    )
                else:
                    await self.state.mcp_manager.disconnect_server(message.server_id)
                    self.call_from_thread(
                        self.notify, f"MCP server {message.server_id} disconnected"
                    )
                self.call_from_thread(self._refresh_mcp_panel)

        if not (self.mcp_loop and self.mcp_loop.is_running()):
            self.mcp_loop_ready.clear()
            self._async_init_mcp_servers()
            self.mcp_loop_ready.wait(2.0)

        if self.mcp_loop and self.mcp_loop.is_running():
            asyncio.run_coroutine_threadsafe(_handle(), self.mcp_loop)
        else:
            self.notify("MCP event loop is not running", severity="warning")

    @on(MCPToolToggled)
    def on_mcp_tool_toggled(self, message: MCPToolToggled) -> None:
        """Handle MCP tool enable/disable toggle."""
        self.state.update_mcp_tool(message.skill_id, message.enabled)
        self.state.save()
        self._refresh_mcp_panel()
        self.notify(f"MCP tool {message.skill_id} {'enabled' if message.enabled else 'disabled'}")

    @on(MCPServerAddRequested)
    def on_mcp_server_add_requested(self) -> None:
        """Handle add MCP server request - shows guidance."""
        self.notify("Add MCP server config in .nju_code/settings.json under 'mcp.servers' section")

    def on_app_shutdown(self) -> None:
        """Clean shutdown - disconnect MCP servers and save state."""
        if self.state.mcp_manager and self.mcp_loop and self.mcp_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self.state.mcp_manager.disconnect_all(), self.mcp_loop
            )
            try:
                future.result(timeout=10)
            except Exception:
                pass
            self.mcp_loop.call_soon_threadsafe(self.mcp_loop.stop)

        self.state.save()
