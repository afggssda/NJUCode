"""Project task and TODO scanner for NJUCode.

The scanner is intentionally read-only.  It builds a lightweight task index
from common code markers and Markdown checkboxes so users can quickly find
unfinished work without opening every file by hand.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Scan configuration
# ---------------------------------------------------------------------------

# Keep this list focused on files that usually contain human-authored tasks.
# Binary files and generated assets are intentionally skipped because task
# markers there are usually either unreadable or accidental text fragments.
TEXT_FILE_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".sql",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".css",
    ".html",
}


# Generated, vendored, and temporary directories can create noisy false tasks.
# The scanner is a project-maintenance aid, so local runtime state should not
# become part of the user's daily task list.
EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".nju_code",
    ".test_tmp",
    "venv",
    "__pycache__",
    "node_modules",
}


# ---------------------------------------------------------------------------
# Marker patterns
# ---------------------------------------------------------------------------

# Require an explicit colon after the marker.  This prevents ordinary prose such
# as "fixme examples" or "bug triage" from becoming false task items.
TASK_MARKER_RE = re.compile(
    r"^(?P<tag>TODO|FIXME|BUG|HACK|NOTE)"
    r"(?:\((?P<owner>[A-Za-z0-9_.-]+)\))?"
    r"\s*:\s*(?P<text>\S.*)$",
    re.IGNORECASE,
)

# Markdown checkboxes are the only source that can naturally be "done"; code
# comments are considered open until the developer removes or rewrites them.
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.+?)\s*$")

# Markdown supports both bare task lines and bullet task lines, but still uses
# the same strict marker format as code comments.
MARKDOWN_TASK_RE = re.compile(
    r"^\s{0,3}(?:[-*+]\s+)?(?P<body>TODO(?:\([A-Za-z0-9_.-]+\))?\s*:\s*\S.*|"
    r"FIXME(?:\([A-Za-z0-9_.-]+\))?\s*:\s*\S.*|"
    r"BUG(?:\([A-Za-z0-9_.-]+\))?\s*:\s*\S.*|"
    r"HACK(?:\([A-Za-z0-9_.-]+\))?\s*:\s*\S.*|"
    r"NOTE(?:\([A-Za-z0-9_.-]+\))?\s*:\s*\S.*)$",
    re.IGNORECASE,
)

# Non-Python text-like files do not have a standard tokenizer, so we accept only
# common comment prefixes before looking for a strict task marker.
COMMENT_PREFIX_RE = re.compile(r"^\s*(?:#|//|--|/\*+|\*|<!--)\s*(?P<body>.*?)(?:\*/|-->)?\s*$")


@dataclass
class TaskItem:
    """One task-like marker found in a workspace file."""

    # All paths are workspace-relative so payloads are stable across machines.
    path: str
    line: int
    # tag is normalized to uppercase: TODO, FIXME, BUG, HACK, NOTE, CHECKBOX.
    tag: str
    text: str
    # status is currently "open" or "done"; only Markdown checkboxes can be done.
    status: str
    # priority is derived from tag, not from user input, for predictable sorting.
    priority: str
    # source records the parser path that produced the item for debugging.
    source: str
    owner: str = ""


class ProjectTaskIndex:
    """Scan a workspace for code markers and Markdown checklist items."""

    def __init__(self, workspace_root: Path, max_file_bytes: int = 1_000_000) -> None:
        self.workspace_root = workspace_root
        # Large files often contain minified bundles or exported logs.  Scanning
        # them slows the UI and tends to produce low-signal matches.
        self.max_file_bytes = max_file_bytes

    def _relative(self, path: Path) -> str:
        # Prefer portable slash-separated relative paths in command output.
        try:
            return path.relative_to(self.workspace_root).as_posix()
        except ValueError:
            return path.as_posix()

    def _is_test_file(self, path: Path) -> bool:
        # Test fixtures often contain intentional TODO/FIXME/BUG examples.  They
        # are hidden by default so the main task list reflects product code.
        try:
            rel_parts = path.relative_to(self.workspace_root).parts
        except ValueError:
            rel_parts = path.parts
        return (
            "tests" in rel_parts
            or path.name.startswith("test_")
            or path.name.endswith("_test.py")
        )

    def _iter_candidate_files(self, include_tests: bool = False) -> Iterable[Path]:
        # Filtering happens before file reads, keeping the scanner cheap enough
        # for interactive use from the chat command and Tools panel.
        for path in self.workspace_root.rglob("*"):
            if path.is_dir():
                continue
            try:
                rel_parts = set(path.relative_to(self.workspace_root).parts)
            except ValueError:
                rel_parts = set(path.parts)
            if rel_parts & EXCLUDED_DIRS:
                continue
            # Tests can be included explicitly when a user wants to audit
            # fixtures or test-maintenance tasks.
            if not include_tests and self._is_test_file(path):
                continue
            if path.suffix.lower() not in TEXT_FILE_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > self.max_file_bytes:
                    continue
            except OSError:
                continue
            yield path

    def _read_lines(self, path: Path) -> list[str]:
        # UTF-8 with ignore fallback keeps mixed project files from crashing the
        # whole scan; unreadable files simply contribute no task items.
        try:
            return path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return []

    def _priority_for_tag(self, tag: str) -> str:
        # Risk-like markers float to the top of the output.  NOTE stays low
        # because it is commonly informational rather than urgent.
        tag = tag.upper()
        if tag in {"BUG", "FIXME"}:
            return "high"
        if tag in {"TODO", "HACK", "CHECKBOX"}:
            return "medium"
        return "low"

    def _task_from_marker(
        self,
        rel_path: str,
        line_no: int,
        body: str,
        source: str,
    ) -> TaskItem | None:
        # This helper is the single strict gate for non-checkbox markers.  Every
        # parser path extracts a candidate body, then validates it here.
        marker = TASK_MARKER_RE.match(body.strip())
        if not marker:
            return None
        tag = marker.group("tag").upper()
        owner = (marker.group("owner") or "").strip()
        return TaskItem(
            path=rel_path,
            line=line_no,
            tag=tag,
            text=marker.group("text").strip(),
            status="open",
            priority=self._priority_for_tag(tag),
            source=source,
            owner=owner,
        )

    def _scan_markdown_line(self, rel_path: str, line_no: int, line: str) -> list[TaskItem]:
        items: list[TaskItem] = []

        # Markdown checkboxes carry explicit completion state, unlike code
        # comments which are treated as open until the marker is removed.
        checkbox = CHECKBOX_RE.match(line)
        if checkbox:
            status = "done" if checkbox.group("mark").lower() == "x" else "open"
            items.append(
                TaskItem(
                    path=rel_path,
                    line=line_no,
                    tag="CHECKBOX",
                    text=checkbox.group("text").strip(),
                    status=status,
                    priority=self._priority_for_tag("CHECKBOX"),
                    source="markdown_checkbox",
                )
            )
            return items

        markdown_task = MARKDOWN_TASK_RE.match(line)
        if markdown_task:
            # Markdown marker lines reuse _task_from_marker so owner parsing and
            # priority assignment stay identical to code comments.
            item = self._task_from_marker(
                rel_path,
                line_no,
                markdown_task.group("body"),
                "markdown_marker",
            )
            if item:
                items.append(item)
        return items

    def _scan_comment_line(self, rel_path: str, line_no: int, line: str) -> list[TaskItem]:
        # Generic text files only count markers that appear after a recognizable
        # comment prefix.  This avoids scanning config values and documentation
        # examples as if they were source comments.
        comment = COMMENT_PREFIX_RE.match(line)
        if not comment:
            return []
        item = self._task_from_marker(rel_path, line_no, comment.group("body"), "code_comment")
        return [item] if item else []

    def _scan_python_comments(self, path: Path, rel_path: str) -> list[TaskItem]:
        # Python gets a real tokenizer because comments, strings, and docstrings
        # can look similar in plain text.  tokenize lets us ignore everything
        # except actual COMMENT tokens.
        text = "\n".join(self._read_lines(path))
        items: list[TaskItem] = []
        try:
            tokens = tokenize.generate_tokens(io.StringIO(text).readline)
            for token in tokens:
                if token.type != tokenize.COMMENT:
                    continue
                body = token.string.lstrip("#").strip()
                item = self._task_from_marker(rel_path, token.start[0], body, "python_comment")
                if item:
                    items.append(item)
        except tokenize.TokenError:
            # Syntax-incomplete files should not break the UI command.  The
            # project doctor has separate syntax checks for reporting failures.
            return []
        return items

    def _scan_file(self, path: Path, rel_path: str) -> list[TaskItem]:
        # Dispatch by file type so Markdown checkboxes and Python comments can
        # use more precise parsing than generic text files.
        if path.suffix.lower() == ".py":
            return self._scan_python_comments(path, rel_path)

        items: list[TaskItem] = []
        for line_no, line in enumerate(self._read_lines(path), start=1):
            if path.suffix.lower() == ".md":
                items.extend(self._scan_markdown_line(rel_path, line_no, line))
            else:
                items.extend(self._scan_comment_line(rel_path, line_no, line))
        return items

    def _apply_filters(
        self,
        items: list[TaskItem],
        tag: str = "",
        include_done: bool = False,
        owner: str = "",
        path_filter: str = "",
    ) -> list[TaskItem]:
        # Filtering is applied after scanning so summary counts always describe
        # the exact list the user asked to see.
        tag = tag.upper().strip()
        owner = owner.strip().lower()
        path_filter = path_filter.strip().replace("\\", "/")
        filtered: list[TaskItem] = []
        for item in items:
            if path_filter and path_filter not in item.path:
                continue
            if tag and item.tag != tag:
                continue
            if owner and item.owner.lower() != owner:
                continue
            if not include_done and item.status == "done":
                continue
            filtered.append(item)
        return filtered

    def _summarize(self, items: list[TaskItem], scanned_files: int) -> dict[str, Any]:
        # Keep summary fields redundant with items on purpose: UI clients can
        # show counts without recomputing them from the full result list.
        by_tag = Counter(item.tag for item in items)
        by_status = Counter(item.status for item in items)
        by_priority = Counter(item.priority for item in items)
        return {
            "total": len(items),
            "open": by_status.get("open", 0),
            "done": by_status.get("done", 0),
            "scanned_files": scanned_files,
            "by_tag": dict(sorted(by_tag.items())),
            "by_status": dict(sorted(by_status.items())),
            "by_priority": dict(sorted(by_priority.items())),
        }

    def scan(
        self,
        tag: str = "",
        include_done: bool = False,
        owner: str = "",
        limit: int = 50,
        include_tests: bool = False,
        path_filter: str = "",
    ) -> dict[str, Any]:
        """Return a structured task index payload for analyzer/skill output."""

        # First collect all strict task markers from candidate files.  The
        # service remains read-only; it never writes cache or index files.
        all_items: list[TaskItem] = []
        scanned_files = 0
        for path in self._iter_candidate_files(include_tests=include_tests):
            scanned_files += 1
            rel_path = self._relative(path)
            all_items.extend(self._scan_file(path, rel_path))

        priority_rank = {"high": 0, "medium": 1, "low": 2}
        filtered = self._apply_filters(
            all_items,
            tag=tag,
            include_done=include_done,
            owner=owner,
            path_filter=path_filter,
        )
        # Stable sorting makes repeated command runs easy to compare in chat.
        filtered.sort(key=lambda item: (priority_rank.get(item.priority, 9), item.path, item.line))
        # Bound result size so accidental large task lists do not flood the TUI.
        limit = max(1, min(200, int(limit or 50)))

        return {
            "type": "task_index",
            "workspace": str(self.workspace_root),
            "summary": self._summarize(filtered, scanned_files),
            "filters": {
                "tag": tag.upper().strip(),
                "include_done": include_done,
                "include_tests": include_tests,
                "owner": owner.strip(),
                "path": path_filter.strip().replace("\\", "/"),
                "limit": limit,
            },
            "items": [asdict(item) for item in filtered[:limit]],
            "total_before_limit": len(filtered),
        }
