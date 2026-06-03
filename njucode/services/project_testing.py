"""Project-wide diagnostics and executable self-tests for NJUCode.

The service in this module is intentionally independent from the Textual UI.
It can be called from a builtin skill, from a future CLI command, or from the
standalone ``test_all_features.py`` regression file.  The goal is not to
replace unit tests; it is a compact project doctor that checks whether the
major feature areas can still be discovered and exercised after refactors.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional
from uuid import uuid4


CheckCallable = Callable[[], "ProjectCheckResult"]


# Directories that should never affect diagnostic results.  They are either
# generated, vendored, or created by this test runner itself.
EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".test_tmp",
    "venv",
    "__pycache__",
    "node_modules",
}


# Files and directories that define the expected NJUCode project skeleton.
# The layout check uses this list as a quick smoke test before deeper imports.
REQUIRED_TOP_LEVEL = [
    "main.py",
    "requirements.txt",
    "README.md",
    "njucode",
    "njucode/app.py",
    "njucode/models.py",
    "njucode/state.py",
    "njucode/services",
    "njucode/services/code_analysis.py",
    "njucode/services/openai_client.py",
    "njucode/services/context_compressor.py",
    "njucode/services/patch_engine.py",
    "njucode/services/code_extractor.py",
    "njucode/skills",
    "njucode/skills/models.py",
    "njucode/skills/registry.py",
    "njucode/skills/executor.py",
    "njucode/skills/builtin/__init__.py",
    "njucode/mcp",
    "njucode/mcp/manager.py",
    "njucode/mcp/client.py",
    "njucode/ui/widgets",
]


# Textual widgets that make up the user-facing panels.  Keeping this list here
# makes UI regressions visible even when the TUI is not launched in tests.
REQUIRED_WIDGET_FILES = [
    "chat_panel.py",
    "code_viewer_panel.py",
    "config_panel.py",
    "file_tree_panel.py",
    "mcp_panel.py",
    "patch_panel.py",
    "session_panel.py",
    "skills_panel.py",
    "splitter.py",
    "tools_panel.py",
]


# Some PyPI distribution names do not map one-to-one to import names.  The
# import check consults this table before falling back to hyphen-to-underscore.
REQUIREMENT_IMPORT_MAP = {
    "python-dotenv": "dotenv",
    "tree-sitter": "tree_sitter",
    "tree-sitter-languages": "tree_sitter_languages",
    "tree-sitter-python": "tree_sitter_python",
    "tree-sitter-javascript": "tree_sitter_javascript",
    "tree-sitter-typescript": "tree_sitter_typescript",
    "tree-sitter-c": "tree_sitter_c",
    "tree-sitter-cpp": "tree_sitter_cpp",
    "tree-sitter-java": "tree_sitter_java",
    "tree-sitter-go": "tree_sitter_go",
    "tree-sitter-rust": "tree_sitter_rust",
    "tree-sitter-json": "tree_sitter_json",
    "tree-sitter-yaml": "tree_sitter_yaml",
    "tree-sitter-markdown": "tree_sitter_markdown",
    "tree-sitter-bash": "tree_sitter_bash",
    "tree-sitter-html": "tree_sitter_html",
    "tree-sitter-css": "tree_sitter_css",
    "tree-sitter-sql": "tree_sitter_sql",
    "tree-sitter-toml": "tree_sitter_toml",
}


# ---------------------------------------------------------------------------
# Report data structures
# ---------------------------------------------------------------------------

@dataclass
class ProjectIssue:
    """A single finding produced by a diagnostic check."""

    # severity is intentionally a string instead of an Enum so reports remain
    # easy to serialize and inspect from chat output, JSON, and Markdown.
    severity: str
    message: str
    path: str = ""
    hint: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class ProjectCheckResult:
    """Result for one named diagnostic check."""

    # status uses the compact vocabulary pass/warn/fail/skip.  The UI and tests
    # both rely on those exact values when summarizing the report.
    name: str
    status: str
    summary: str
    elapsed_ms: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    issues: list[ProjectIssue] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    @property
    def warned(self) -> bool:
        return self.status == "warn"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = [issue.to_dict() for issue in self.issues]
        return payload


@dataclass
class ProjectTestReport:
    """Aggregate result for the full project doctor run."""

    # The report object is kept small and pure-data so it can be returned from
    # a local command, emitted to chat, or exported as JSON without extra hooks.
    workspace: str
    generated_at: str
    results: list[ProjectCheckResult]

    @property
    def passed(self) -> bool:
        return self.fail_count == 0

    @property
    def fail_count(self) -> int:
        return sum(1 for result in self.results if result.status == "fail")

    @property
    def warn_count(self) -> int:
        return sum(1 for result in self.results if result.status == "warn")

    @property
    def pass_count(self) -> int:
        return sum(1 for result in self.results if result.status == "pass")

    @property
    def skip_count(self) -> int:
        return sum(1 for result in self.results if result.status == "skip")

    def to_dict(self) -> dict[str, Any]:
        # Keep a stable "type" field because CodeAnalyzer.to_text dispatches on
        # payload type just like it does for scan/search/impact results.
        return {
            "type": "project_test_report",
            "workspace": self.workspace,
            "generated_at": self.generated_at,
            "passed": self.passed,
            "summary": {
                "pass": self.pass_count,
                "warn": self.warn_count,
                "fail": self.fail_count,
                "skip": self.skip_count,
                "total": len(self.results),
            },
            "results": [result.to_dict() for result in self.results],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def to_markdown(self) -> str:
        # Markdown is mostly for saved reports or release notes; the chat view
        # usually uses report_to_text() for a denser layout.
        status = "FAIL" if self.fail_count else ("WARN" if self.warn_count else "PASS")
        lines = [
            f"# NJUCode Project Doctor: {status}",
            "",
            f"- Workspace: `{self.workspace}`",
            f"- Generated: `{self.generated_at}`",
            f"- Passed: `{self.pass_count}`",
            f"- Warnings: `{self.warn_count}`",
            f"- Failures: `{self.fail_count}`",
            f"- Skipped: `{self.skip_count}`",
            "",
            "| Check | Status | Summary |",
            "| --- | --- | --- |",
        ]
        for result in self.results:
            lines.append(
                f"| {result.name} | {result.status.upper()} | {result.summary.replace('|', '/')} |"
            )
        issue_lines: list[str] = []
        for result in self.results:
            for issue in result.issues:
                location = f" `{issue.path}`" if issue.path else ""
                hint = f" Hint: {issue.hint}" if issue.hint else ""
                issue_lines.append(
                    f"- **{result.name} / {issue.severity.upper()}**{location}: {issue.message}{hint}"
                )
        if issue_lines:
            lines.extend(["", "## Issues", *issue_lines])
        return "\n".join(lines)


class ProjectTestRunner:
    """Run local checks that cover NJUCode's major capability areas."""

    def __init__(self, workspace_root: Path, include_slow: bool = False) -> None:
        self.workspace_root = workspace_root.resolve()
        self.include_slow = include_slow
        # The order is intentional: cheap structural checks run before behavior
        # checks, so failures point to missing prerequisites first.
        # Each tuple contains the public check name and the bound method that
        # returns a ProjectCheckResult.
        # New checks should be added here, then covered by test_all_features.py.
        self._checks: list[tuple[str, CheckCallable]] = [
            ("layout", self.check_project_layout),
            ("requirements", self.check_requirements),
            ("python_syntax", self.check_python_syntax),
            ("imports", self.check_required_imports),
            ("entrypoint", self.check_entrypoint),
            ("code_analysis", self.check_code_analysis),
            ("code_extractor", self.check_code_extractor),
            ("context_compressor", self.check_context_compressor),
            ("settings_store", self.check_settings_store),
            ("patch_engine", self.check_patch_engine),
            ("skills", self.check_skills),
            ("mcp", self.check_mcp_presets),
            ("ui_structure", self.check_ui_structure),
            ("readme_commands", self.check_readme_commands),
            ("security", self.check_security_basics),
            ("line_budget", self.check_line_budget),
        ]

    def run_all(self, selected: Optional[Iterable[str]] = None) -> ProjectTestReport:
        # selected lets tests and future UI controls run a smaller subset while
        # still using the same report object as the full doctor run.
        selected_set = {item.strip() for item in selected or [] if item.strip()}
        results: list[ProjectCheckResult] = []
        for name, check in self._checks:
            if selected_set and name not in selected_set:
                continue
            results.append(self._timed(name, check))
        return ProjectTestReport(
            workspace=str(self.workspace_root),
            generated_at=datetime.now().isoformat(timespec="seconds"),
            results=results,
        )

    def list_checks(self) -> list[str]:
        # Exposed mainly for tests and future command completion/help surfaces.
        return [name for name, _ in self._checks]

    def _timed(self, name: str, check: CheckCallable) -> ProjectCheckResult:
        # Every check is isolated behind this wrapper so a single unexpected
        # exception becomes a structured failure instead of aborting the report.
        start = time.perf_counter()
        try:
            result = check()
        except Exception as exc:  # pragma: no cover - defensive safety net
            result = ProjectCheckResult(
                name=name,
                status="fail",
                summary=f"unexpected exception: {exc}",
                issues=[ProjectIssue("error", repr(exc))],
            )
        result.elapsed_ms = int((time.perf_counter() - start) * 1000)
        return result

    def _path(self, rel_path: str) -> Path:
        # Centralize workspace-relative resolution to keep checks short and
        # consistent.
        return self.workspace_root / rel_path

    def _iter_project_files(self) -> Iterator[Path]:
        # Shared file iterator used by syntax, security, and line-count checks.
        for path in self.workspace_root.rglob("*"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            if path.is_file():
                yield path

    def _iter_python_files(self) -> Iterator[Path]:
        # Python checks share the same exclusion rules as all-file checks.
        for path in self._iter_project_files():
            if path.suffix == ".py":
                yield path

    def _relative(self, path: Path) -> str:
        # Normalize Windows paths to slash form so report output is stable
        # across PowerShell, Git Bash, and CI logs.
        try:
            return str(path.relative_to(self.workspace_root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def _read_text(self, rel_path: str) -> str:
        # errors="ignore" keeps diagnostics resilient when a file contains
        # unexpected bytes.
        path = self._path(rel_path)
        return path.read_text(encoding="utf-8", errors="ignore")

    class _WorkspaceTempDir:
        # Small context manager instead of tempfile.TemporaryDirectory because
        # some Windows lab sandboxes allow workspace writes but deny OS temp
        # directory writes.
        def __init__(self, root: Path) -> None:
            self.path = root / f"doctor_{uuid4().hex}"

        def __enter__(self) -> str:
            self.path.mkdir(parents=True, exist_ok=True)
            return str(self.path)

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            shutil.rmtree(self.path, ignore_errors=True)

    def _temporary_directory(self) -> "ProjectTestRunner._WorkspaceTempDir":
        # Keep diagnostics temp files inside the workspace so the same sandbox
        # permissions as the project are used.  This avoids OS temp-dir ACL
        # differences on Windows lab machines.
        temp_root = self.workspace_root / ".nju_code" / "test_tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        return self._WorkspaceTempDir(temp_root)

    def _result(
        self,
        name: str,
        status: str,
        summary: str,
        metrics: Optional[dict[str, Any]] = None,
        issues: Optional[list[ProjectIssue]] = None,
    ) -> ProjectCheckResult:
        # Helper keeps each check focused on what it validates rather than on
        # repeated dataclass construction boilerplate.
        return ProjectCheckResult(
            name=name,
            status=status,
            summary=summary,
            metrics=metrics or {},
            issues=issues or [],
        )

    def check_project_layout(self) -> ProjectCheckResult:
        # Fastest check: verify the expected project skeleton before importing
        # modules that depend on those paths.
        # This catches accidental file moves, renamed widgets, and incomplete
        # merges before deeper checks fail with confusing import errors.
        missing: list[ProjectIssue] = []
        for rel_path in REQUIRED_TOP_LEVEL:
            if not self._path(rel_path).exists():
                missing.append(
                    ProjectIssue(
                        severity="error",
                        message="required project path is missing",
                        path=rel_path,
                    )
                )
        widget_dir = self._path("njucode/ui/widgets")
        widget_missing = []
        for filename in REQUIRED_WIDGET_FILES:
            if not (widget_dir / filename).exists():
                widget_missing.append(filename)
                missing.append(
                    ProjectIssue(
                        severity="error",
                        message="required widget module is missing",
                        path=f"njucode/ui/widgets/{filename}",
                    )
                )
        status = "pass" if not missing else "fail"
        return self._result(
            "layout",
            status,
            f"{len(REQUIRED_TOP_LEVEL) - len(missing)} required paths present",
            metrics={
                "required_paths": len(REQUIRED_TOP_LEVEL),
                "missing_paths": len(missing),
                "required_widgets": len(REQUIRED_WIDGET_FILES),
                "missing_widgets": widget_missing,
            },
            issues=missing,
        )

    def check_requirements(self) -> ProjectCheckResult:
        # This is a declaration check, not an install check.  Importability is
        # covered separately by check_required_imports().
        # It also rejects suspicious direct/editable dependencies, which are
        # harder to reproduce during classroom demos.
        requirements_path = self._path("requirements.txt")
        if not requirements_path.exists():
            return self._result(
                "requirements",
                "fail",
                "requirements.txt is missing",
                issues=[ProjectIssue("error", "requirements.txt not found", "requirements.txt")],
            )

        lines = [
            line.strip()
            for line in requirements_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        issues: list[ProjectIssue] = []
        names: list[str] = []
        for line in lines:
            match = re.match(r"([A-Za-z0-9_.-]+)", line)
            if not match:
                issues.append(ProjectIssue("warning", "cannot parse requirement", "requirements.txt", line))
                continue
            names.append(match.group(1).lower())
            if line.startswith("-e ") or "://" in line:
                issues.append(
                    ProjectIssue(
                        "warning",
                        "requirement uses an editable or direct URL source",
                        "requirements.txt",
                        line,
                    )
                )
        required = {"textual", "rich", "pydantic", "python-dotenv", "openai", "mcp"}
        missing = sorted(required - set(names))
        for name in missing:
            issues.append(
                ProjectIssue("error", f"required dependency is not listed: {name}", "requirements.txt")
            )
        status = "fail" if missing else ("warn" if issues else "pass")
        return self._result(
            "requirements",
            status,
            f"{len(lines)} dependencies declared",
            metrics={"dependencies": names, "dependency_count": len(lines)},
            issues=issues,
        )

    def check_python_syntax(self) -> ProjectCheckResult:
        # Use ast.parse instead of compileall so diagnostics do not need to
        # write __pycache__ files in restricted Windows terminals.
        # This catches SyntaxError without mutating the repository.
        issues: list[ProjectIssue] = []
        file_count = 0
        total_lines = 0
        for path in self._iter_python_files():
            file_count += 1
            rel = self._relative(path)
            text = path.read_text(encoding="utf-8", errors="ignore")
            total_lines += text.count("\n") + 1
            try:
                ast.parse(text, filename=rel)
            except SyntaxError as exc:
                issues.append(
                    ProjectIssue(
                        "error",
                        f"syntax error at line {exc.lineno}: {exc.msg}",
                        rel,
                    )
                )
        status = "pass" if not issues else "fail"
        return self._result(
            "python_syntax",
            status,
            f"parsed {file_count} Python files",
            metrics={"python_files": file_count, "python_lines": total_lines},
            issues=issues,
        )

    def check_required_imports(self) -> ProjectCheckResult:
        # A dependency can be listed correctly but still be missing from the
        # active environment; this check catches that setup problem.
        # The mapping table handles packages whose import names differ from
        # their PyPI distribution names.
        req_path = self._path("requirements.txt")
        if not req_path.exists():
            return self._result("imports", "skip", "requirements.txt missing")

        missing: list[ProjectIssue] = []
        installed: list[str] = []
        for line in req_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = re.split(r"[<>=!~\[]+", line, maxsplit=1)[0].strip().lower()
            if not name:
                continue
            module_name = REQUIREMENT_IMPORT_MAP.get(name, name.replace("-", "_"))
            if importlib.util.find_spec(module_name) is None:
                missing.append(
                    ProjectIssue(
                        "error",
                        f"module for requirement is not importable: {module_name}",
                        "requirements.txt",
                        f"install {name}",
                    )
                )
            else:
                installed.append(module_name)
        status = "pass" if not missing else "fail"
        return self._result(
            "imports",
            status,
            f"{len(installed)} requirement modules importable",
            metrics={"importable": installed, "missing": [i.message for i in missing]},
            issues=missing,
        )

    def check_entrypoint(self) -> ProjectCheckResult:
        # Import smoke test for the application entrypoint.  It deliberately
        # avoids launching Textual, which would block the terminal.
        # If this check fails, the app is unlikely to start with python main.py.
        issues: list[ProjectIssue] = []
        try:
            import main  # type: ignore
            from ..app import NjuCodeApp

            title = getattr(NjuCodeApp, "TITLE", "")
            if not title:
                issues.append(ProjectIssue("warning", "NjuCodeApp.TITLE is empty", "njucode/app.py"))
            if not hasattr(main, "NjuCodeApp"):
                issues.append(ProjectIssue("warning", "main.py does not expose NjuCodeApp", "main.py"))
        except Exception as exc:
            issues.append(ProjectIssue("error", f"entrypoint import failed: {exc}", "main.py"))
        status = "fail" if any(i.severity == "error" for i in issues) else ("warn" if issues else "pass")
        return self._result(
            "entrypoint",
            status,
            "main.py and NjuCodeApp import smoke checked",
            issues=issues,
        )

    def check_code_analysis(self) -> ProjectCheckResult:
        # Exercise the local analysis engine as a user would: scan first, then
        # search, inspect symbols, summarize files, trace deps, recall, impact.
        # These checks operate on the real repository so they catch integration
        # drift between analyzer logic and current project files.
        from .code_analysis import CodeAnalyzer

        analyzer = CodeAnalyzer(self.workspace_root)
        issues: list[ProjectIssue] = []
        metrics: dict[str, Any] = {}

        scan = analyzer.scan_project()
        # The scan result is used by the Tools panel and slash-command output.
        metrics["file_count"] = scan.get("summary", {}).get("file_count", 0)
        if scan.get("type") != "scan" or metrics["file_count"] <= 0:
            issues.append(ProjectIssue("error", "scan_project did not return project files"))

        search = analyzer.search_text("NjuCodeApp")
        # NjuCodeApp is a stable symbol that should exist in the main TUI file.
        metrics["nju_code_app_hits"] = search.get("hit_count", 0)
        if search.get("hit_count", 0) <= 0:
            issues.append(ProjectIssue("error", "text search could not find NjuCodeApp", "njucode/app.py"))

        symbol = analyzer.symbol_search("NjuCodeApp")
        # Symbol search is Python-specific, so it complements text search.
        metrics["symbol_hits"] = symbol.get("hit_count", 0)
        if symbol.get("hit_count", 0) <= 0:
            issues.append(ProjectIssue("error", "symbol search could not find NjuCodeApp", "njucode/app.py"))

        summary = analyzer.summarize_file("njucode/app.py")
        # app.py is large enough to exercise class/function/import extraction.
        if summary.get("type") != "file_summary" or summary.get("error"):
            issues.append(ProjectIssue("error", "file summary failed for njucode/app.py", "njucode/app.py"))

        deps = analyzer.neighbors("njucode/app.py", depth=1)
        # Dependency neighbors are the data behind /deps.
        if deps.get("type") != "neighbors":
            issues.append(ProjectIssue("error", "dependency neighbor analysis failed", "njucode/app.py"))

        recall = analyzer.recall_files("patch rollback skills", top_k=5)
        # Natural-language recall is intentionally fuzzy, so this check focuses
        # on payload shape rather than exact ranking.
        metrics["recall_count"] = len(recall.get("results", []))
        if recall.get("type") != "recall":
            issues.append(ProjectIssue("error", "file recall returned unexpected payload"))

        impact = analyzer.impact_analysis("NjuCodeApp", depth=1)
        # Impact analysis exercises the file/symbol resolution path.
        if impact.get("type") != "impact":
            issues.append(ProjectIssue("error", "impact analysis returned unexpected payload"))

        status = "pass" if not issues else "fail"
        return self._result(
            "code_analysis",
            status,
            "scan/search/symbol/summary/deps/recall/impact exercised",
            metrics=metrics,
            issues=issues,
        )

    def check_code_extractor(self) -> ProjectCheckResult:
        # The patch pipeline depends on robust fenced-code parsing, including
        # filename hints and multi-file blocks in one LLM reply.
        # The sample includes a shell block to make sure executable examples are
        # not mistaken for patchable source code.
        from .code_extractor import extract_code_blocks

        sample = (
            "Here is a change.\n"
            "```python njucode/app.py\n"
            "class Example:\n"
            "    pass\n"
            "```\n"
            "```bash\n"
            "python main.py\n"
            "```\n"
            "```python\n"
            "# njucode/a.py\n"
            "A = 1\n"
            "# njucode/b.py\n"
            "B = 2\n"
            "```\n"
        )
        blocks = extract_code_blocks(sample)
        filenames = [block.filename for block in blocks]
        issues: list[ProjectIssue] = []
        if "njucode/app.py" not in filenames:
            issues.append(ProjectIssue("error", "filename hint was not parsed"))
        if "njucode/a.py" not in filenames or "njucode/b.py" not in filenames:
            issues.append(ProjectIssue("error", "multi-file code block was not split"))
        if any(block.language in {"bash", "sh"} for block in blocks):
            issues.append(ProjectIssue("error", "shell block should have been filtered"))
        status = "pass" if not issues else "fail"
        return self._result(
            "code_extractor",
            status,
            f"extracted {len(blocks)} patchable code blocks",
            metrics={"filenames": filenames},
            issues=issues,
        )

    def check_context_compressor(self) -> ProjectCheckResult:
        # Compression is tested with a fake model client so the doctor remains
        # deterministic and does not require network/API credentials.
        # The token threshold is deliberately low so the check always exercises
        # the compression path on a tiny fixture.
        from ..models import ChatMessage, ModelConfig
        from .context_compressor import ContextCompressor

        class FakeClient:
            def chat(self, request: Any) -> str:
                return "[intent] summarize\n[key] keep files and decisions"

        compressor = ContextCompressor(
            FakeClient(),
            ModelConfig(),
            token_threshold=30,
            keep_recent=2,
            min_messages_to_compress=1,
        )
        messages = [
            ChatMessage(role="user", content="Please explain the codebase in Chinese. " * 10),
            ChatMessage(role="assistant", content="Here is a long answer. " * 20),
            ChatMessage(role="user", content="Now focus on patch rollback."),
            ChatMessage(role="assistant", content="Patch rollback uses backups."),
            ChatMessage(role="user", content="Keep the latest requirement visible."),
        ]
        issues: list[ProjectIssue] = []
        estimate = compressor.estimate_tokens(messages)
        if estimate <= 0:
            issues.append(ProjectIssue("error", "token estimate should be positive"))
        if not compressor.needs_compression(messages):
            issues.append(ProjectIssue("warning", "low test threshold should trigger compression"))
        result = compressor.compress(messages, session_title="doctor")
        if not result.summary:
            issues.append(ProjectIssue("error", "compression did not produce a summary"))
        if len(result.kept_messages) > len(messages):
            issues.append(ProjectIssue("error", "compression kept more messages than input"))
        status = "fail" if any(i.severity == "error" for i in issues) else ("warn" if issues else "pass")
        return self._result(
            "context_compressor",
            status,
            "token estimation and compression exercised",
            metrics={
                "token_estimate": estimate,
                "token_before": result.token_before,
                "token_after": result.token_after,
                "removed_count": result.removed_count,
            },
            issues=issues,
        )

    def check_settings_store(self) -> ProjectCheckResult:
        # SettingsStore is responsible for user state durability, so this check
        # verifies backup and import/export behavior in a scratch workspace.
        # Scratch workspaces prevent accidental reads or writes to the user's
        # real .nju_code/settings.json.
        from .settings_store import SettingsStore

        issues: list[ProjectIssue] = []
        with self._temporary_directory() as tmp:
            root = Path(tmp)
            store = SettingsStore(root)
            payload = {"model": {"base_url": "https://example.test", "api_key": ""}, "sessions": []}
            store.save(payload)
            loaded = store.load()
            if loaded != payload:
                issues.append(ProjectIssue("error", "settings roundtrip changed payload"))
            store.save({"model": {"base_url": "https://second.test"}, "sessions": []})
            if not store.has_backup():
                issues.append(ProjectIssue("error", "settings backup was not created"))
            session = {"session_id": "s1", "title": "Test", "messages": [{"role": "user", "content": "hi"}]}
            export_path = root / ".nju_code" / "exports" / "session_s1.json"
            store.export_session_file(session, export_path)
            imported = store.import_session_file(export_path)
            if imported["session_id"] != "s1":
                issues.append(ProjectIssue("error", "session export/import roundtrip failed"))
        status = "pass" if not issues else "fail"
        return self._result(
            "settings_store",
            status,
            "settings backup and session import/export exercised",
            issues=issues,
        )

    def check_patch_engine(self) -> ProjectCheckResult:
        # Patch validation covers both the happy path and a path traversal
        # attack attempt.  This keeps WBS-4 safety requirements visible.
        # The modify/rollback path is chosen because it does not require file
        # deletion in restrictive sandboxes.
        from .patch_engine import PatchEngine, PatchHistoryStore, PatchStatus

        class DummyAudit:
            def __init__(self) -> None:
                self.logs: list[Any] = []

            def record(self, log: Any) -> None:
                self.logs.append(log)

        issues: list[ProjectIssue] = []
        with self._temporary_directory() as tmp:
            root = Path(tmp)
            target = root / "demo.py"
            target.write_text("value = 1\n", encoding="utf-8")
            store = PatchHistoryStore(root)
            audit = DummyAudit()
            engine = PatchEngine(root, store, audit)
            task = engine.generate_patch(
                {"demo.py": ("value = 1\n", "value = 2\n")},
                description="doctor patch",
                is_ai_generated=True,
                reviewer="doctor",
            )
            preview = engine.preview_patch(task)
            if "value = 2" not in preview:
                issues.append(ProjectIssue("error", "preview did not include new content"))
            ok, reason = engine.validate_patch(task)
            if not ok:
                issues.append(ProjectIssue("error", f"valid patch rejected: {reason}"))
            applied = engine.apply_patch(task)
            if not applied.success or target.read_text(encoding="utf-8") != "value = 2\n":
                issues.append(ProjectIssue("error", "patch apply failed"))
            rolled = engine.rollback_patch(task.task_id)
            if not rolled.success or target.read_text(encoding="utf-8") != "value = 1\n":
                issues.append(ProjectIssue("error", "patch rollback failed"))
            if store.get_task(task.task_id).status != PatchStatus.ROLLED_BACK:
                issues.append(ProjectIssue("error", "rolled back task status not persisted"))
            unsafe = engine.generate_patch({"../escape.py": ("", "x = 1\n")})
            ok, _ = engine.validate_patch(unsafe)
            if ok:
                issues.append(ProjectIssue("error", "path traversal patch was accepted"))
        status = "pass" if not issues else "fail"
        return self._result(
            "patch_engine",
            status,
            "generate/preview/apply/rollback/path-safety exercised",
            issues=issues,
        )

    def check_skills(self) -> ProjectCheckResult:
        # Skills are registered dynamically at app startup.  The doctor checks
        # IDs, command aliases, and help text so the UI/command surface agrees.
        # This catches the common bug where a manifest exists but /help or the
        # command dispatcher was not updated.
        from ..skills.builtin import BUILTIN_AGENT_MANIFESTS, BUILTIN_MANIFESTS
        from ..skills.registry import SkillRegistry
        from .code_analysis import CodeAnalyzer

        issues: list[ProjectIssue] = []
        registry = SkillRegistry(self.workspace_root)
        seen_ids: set[str] = set()
        command_aliases: set[str] = set()
        for manifest in [*BUILTIN_MANIFESTS, *BUILTIN_AGENT_MANIFESTS]:
            if manifest.skill_id in seen_ids:
                issues.append(ProjectIssue("error", f"duplicate skill id: {manifest.skill_id}"))
            seen_ids.add(manifest.skill_id)
            for alias in manifest.command_aliases:
                if alias in command_aliases:
                    issues.append(ProjectIssue("error", f"duplicate command alias: {alias}"))
                command_aliases.add(alias)
            registry.register_skill(manifest)

        analyzer = CodeAnalyzer(self.workspace_root)
        registry.load()
        required_aliases = {
            "/help",
            "/scan",
            "/search",
            "/symbol",
            "/summary",
            "/deps",
            "/recall",
            "/impact",
            "/tasks",
            "/metrics",
            "/doctor",
        }
        missing_aliases = sorted(required_aliases - command_aliases)
        for alias in missing_aliases:
            issues.append(ProjectIssue("error", f"missing builtin command alias: {alias}"))
        help_payload = analyzer.run_command("/help")
        help_commands = "\n".join(help_payload.get("commands", []))
        for alias in required_aliases:
            if alias not in help_commands:
                issues.append(ProjectIssue("warning", f"help output omits alias: {alias}"))
        status = "fail" if any(i.severity == "error" for i in issues) else ("warn" if issues else "pass")
        return self._result(
            "skills",
            status,
            f"{len(seen_ids)} builtin/agent skills registered",
            metrics={"skill_count": len(seen_ids), "command_aliases": sorted(command_aliases)},
            issues=issues,
        )

    def check_mcp_presets(self) -> ProjectCheckResult:
        # MCP presets should exist even when the external npx/uvx commands are
        # not installed.  We only validate configuration, not live connections.
        # Live connections are intentionally out of scope for a local doctor run
        # because they depend on optional external command runners.
        from ..mcp.manager import MCPManager

        with self._temporary_directory() as tmp:
            manager = MCPManager(Path(tmp))
            manager.load()
            server_ids = set(manager.servers)
        required = {"filesystem", "memory", "fetch", "git"}
        missing = sorted(required - server_ids)
        issues = [
            ProjectIssue("error", f"missing MCP preset: {server_id}") for server_id in missing
        ]
        status = "pass" if not issues else "fail"
        return self._result(
            "mcp",
            status,
            f"{len(server_ids)} MCP server presets available",
            metrics={"servers": sorted(server_ids)},
            issues=issues,
        )

    def check_ui_structure(self) -> ProjectCheckResult:
        # Static UI structure check: enough to catch missing tabs/buttons
        # without launching a terminal UI session.
        # It looks for stable IDs because Textual event handlers rely on them.
        issues: list[ProjectIssue] = []
        app_text = self._read_text("njucode/app.py")
        tools_text = self._read_text("njucode/ui/widgets/tools_panel.py")
        required_tabs = ["explorer", "chats", "code", "tools", "skills", "mcp", "patch", "model", "chat"]
        for tab_id in required_tabs:
            if f'id="{tab_id}"' not in app_text and f"id='{tab_id}'" not in app_text:
                issues.append(ProjectIssue("error", f"tab id is not declared: {tab_id}", "njucode/app.py"))
        required_bindings = ["ctrl+n", "ctrl+h", "ctrl+c", "ctrl+q"]
        for binding in required_bindings:
            if binding not in app_text:
                issues.append(ProjectIssue("warning", f"expected key binding missing: {binding}", "njucode/app.py"))
        if "analysis_doctor_btn" not in tools_text:
            issues.append(ProjectIssue("error", "Doctor button missing from Tools panel", "njucode/ui/widgets/tools_panel.py"))
        if "/doctor" not in tools_text:
            issues.append(ProjectIssue("error", "Tools panel does not dispatch /doctor", "njucode/ui/widgets/tools_panel.py"))
        if "analysis_tasks_btn" not in tools_text:
            issues.append(ProjectIssue("error", "Tasks button missing from Tools panel", "njucode/ui/widgets/tools_panel.py"))
        if "/tasks" not in tools_text:
            issues.append(ProjectIssue("error", "Tools panel does not dispatch /tasks", "njucode/ui/widgets/tools_panel.py"))
        if "analysis_metrics_btn" not in tools_text:
            issues.append(ProjectIssue("error", "Metrics button missing from Tools panel", "njucode/ui/widgets/tools_panel.py"))
        if "/metrics" not in tools_text:
            issues.append(ProjectIssue("error", "Tools panel does not dispatch /metrics", "njucode/ui/widgets/tools_panel.py"))
        status = "fail" if any(i.severity == "error" for i in issues) else ("warn" if issues else "pass")
        return self._result(
            "ui_structure",
            status,
            "core tab IDs, bindings, doctor, and task controls checked",
            issues=issues,
        )

    def check_readme_commands(self) -> ProjectCheckResult:
        # Documentation drift is easy in course projects; compare README text
        # with the user-visible command set.
        # The check is a warning rather than failure because docs can lag during
        # active development, but the report still surfaces the mismatch.
        readme = self._read_text("README.md") if self._path("README.md").exists() else ""
        expected = [
            "/help",
            "/scan",
            "/search",
            "/symbol",
            "/summary",
            "/deps",
            "/recall",
            "/impact",
            "/tasks",
            "/metrics",
            "/doctor",
        ]
        issues = [
            ProjectIssue("warning", f"README does not mention command: {cmd}", "README.md")
            for cmd in expected
            if cmd not in readme
        ]
        status = "warn" if issues else "pass"
        return self._result(
            "readme_commands",
            status,
            "README command list compared with builtin diagnostics",
            metrics={"expected_commands": expected},
            issues=issues,
        )

    def check_security_basics(self) -> ProjectCheckResult:
        # This is intentionally conservative: it warns about local secret-like
        # files/tokens but never prints secret values.
        # It is not a full secret scanner; it is a lightweight guardrail for
        # common course-demo mistakes.
        issues: list[ProjectIssue] = []
        settings_path = self._path(".nju_code/settings.json")
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8", errors="ignore"))
                api_key = str(data.get("model", {}).get("api_key", ""))
                if api_key:
                    issues.append(ProjectIssue("error", "API key should not be persisted", ".nju_code/settings.json"))
            except json.JSONDecodeError:
                issues.append(ProjectIssue("warning", "settings.json is not valid JSON", ".nju_code/settings.json"))

        for env_name in [".env", "env", "secrets.json"]:
            path = self._path(env_name)
            if path.exists():
                issues.append(
                    ProjectIssue(
                        "warning",
                        "local secret/config file exists; ensure it is not committed",
                        env_name,
                    )
                )

        suspicious_patterns = [
            re.compile(r"sk-[A-Za-z0-9]{20,}"),
            re.compile(r"OPENAI_API_KEY\s*=\s*['\"]?[^'\"\s]+"),
        ]
        scanned_files = 0
        for path in self._iter_project_files():
            rel = self._relative(path)
            if path.suffix.lower() not in {".py", ".md", ".json", ".toml", ".txt", ".yml", ".yaml"}:
                continue
            scanned_files += 1
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in suspicious_patterns:
                if pattern.search(text) and rel not in {"README.md"}:
                    issues.append(ProjectIssue("warning", "possible secret-like token found", rel))
                    break

        status = "fail" if any(i.severity == "error" for i in issues) else ("warn" if issues else "pass")
        return self._result(
            "security",
            status,
            f"scanned {scanned_files} text files for persisted secrets",
            metrics={"scanned_files": scanned_files},
            issues=issues,
        )

    def check_line_budget(self) -> ProjectCheckResult:
        # Coarse project-size metric used for reporting and milestone evidence,
        # not as a quality measure by itself.
        # The largest-files list helps reviewers understand where code volume
        # is concentrated.
        total_lines = 0
        by_suffix: dict[str, int] = {}
        largest: list[tuple[int, str]] = []
        for path in self._iter_project_files():
            suffix = path.suffix or "<none>"
            try:
                line_count = path.read_text(encoding="utf-8", errors="ignore").count("\n") + 1
            except OSError:
                continue
            total_lines += line_count
            by_suffix[suffix] = by_suffix.get(suffix, 0) + line_count
            largest.append((line_count, self._relative(path)))
        largest.sort(reverse=True)
        issues: list[ProjectIssue] = []
        if total_lines < 2000:
            issues.append(ProjectIssue("warning", "project has fewer than 2000 tracked text lines"))
        status = "warn" if issues else "pass"
        return self._result(
            "line_budget",
            status,
            f"counted {total_lines} project lines",
            metrics={
                "total_lines": total_lines,
                "lines_by_suffix": dict(sorted(by_suffix.items())),
                "largest_files": [{"path": path, "lines": count} for count, path in largest[:10]],
            },
            issues=issues,
        )


def run_project_test_suite(
    workspace_root: Path,
    include_slow: bool = False,
    selected: Optional[Iterable[str]] = None,
) -> ProjectTestReport:
    """Convenience wrapper used by skills and tests."""

    # Keep one public function for command handlers and tests so they exercise
    # the same diagnostic path.
    runner = ProjectTestRunner(workspace_root=workspace_root, include_slow=include_slow)
    return runner.run_all(selected=selected)


def report_to_text(report: ProjectTestReport, verbose: bool = False) -> str:
    """Format a report for chat/TUI display."""

    # Chat output should stay compact by default; detailed issues are shown for
    # warning/failing checks, or for every check when verbose=True.
    status = "FAIL" if report.fail_count else ("WARN" if report.warn_count else "PASS")
    summary = report.to_dict()["summary"]
    lines = [
        f"[Project Doctor] {status}",
        f"Workspace: {report.workspace}",
        (
            f"Checks: {summary['pass']} pass, {summary['warn']} warn, "
            f"{summary['fail']} fail, {summary['skip']} skip"
        ),
        "",
    ]
    for result in report.results:
        marker = {
            "pass": "[PASS]",
            "warn": "[WARN]",
            "fail": "[FAIL]",
            "skip": "[SKIP]",
        }.get(result.status, "[????]")
        lines.append(f"{marker} {result.name}: {result.summary} ({result.elapsed_ms} ms)")
        if verbose or result.status in {"fail", "warn"}:
            for issue in result.issues[:10]:
                loc = f" {issue.path}" if issue.path else ""
                hint = f" | {issue.hint}" if issue.hint else ""
                lines.append(f"  - {issue.severity}:{loc} {issue.message}{hint}")
            if len(result.issues) > 10:
                lines.append(f"  - ... {len(result.issues) - 10} more issue(s)")
    return "\n".join(lines)


def save_doctor_report(workspace_root: Path, report: ProjectTestReport) -> dict[str, str]:
    """Save a Project Doctor report as Markdown and JSON under .nju_code/reports."""

    # Keep generated reports with other local project state.  The UUID suffix
    # avoids collisions when /doctor is run multiple times in one minute.
    reports_dir = workspace_root / ".nju_code" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"project_doctor_{timestamp}_{uuid4().hex[:8]}"
    markdown_path = reports_dir / f"{stem}.md"
    json_path = reports_dir / f"{stem}.json"

    markdown_path.write_text(report.to_markdown() + "\n", encoding="utf-8")
    json_path.write_text(report.to_json() + "\n", encoding="utf-8")
    return {
        "markdown_path": markdown_path.relative_to(workspace_root).as_posix(),
        "json_path": json_path.relative_to(workspace_root).as_posix(),
    }


def run_doctor_as_payload(
    workspace_root: Path,
    include_slow: bool = False,
    verbose: bool = False,
    selected: Optional[Iterable[str]] = None,
    save_report: bool = False,
) -> dict[str, Any]:
    """Return a dict payload compatible with existing analyzer/skill outputs."""

    # The existing analyzer/skill pipeline expects dict payloads.  Include both
    # machine-readable fields and preformatted text/markdown for display.
    report = run_project_test_suite(
        workspace_root=workspace_root,
        include_slow=include_slow,
        selected=selected,
    )
    payload = report.to_dict()
    payload["text"] = report_to_text(report, verbose=verbose)
    payload["markdown"] = report.to_markdown()
    if save_report:
        paths = save_doctor_report(workspace_root, report)
        payload.update(paths)
        payload["text"] = "\n".join(
            [
                payload["text"],
                "",
                "[Saved Report]",
                f"Markdown: {paths['markdown_path']}",
                f"JSON    : {paths['json_path']}",
            ]
        )
    return payload
