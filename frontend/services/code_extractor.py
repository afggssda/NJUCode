"""Utility to extract fenced code blocks from LLM markdown responses.

Parses blocks of the form:
    ```lang
    code
    ```
or with an embedded filename hint:
    ```python frontend/app.py
    code
    ```
    ```python:frontend/app.py
    code
    ```

Additionally handles:
- Filtering out shell command blocks (e.g. ``python main.py``)
- Splitting a single code block that contains multiple files separated by
  file-boundary comments (e.g. ``# frontend/app.py``)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class ExtractedCodeBlock:
    language: str
    filename: Optional[str]  # workspace-relative path hint, may be None
    code: str
    raw_info: str            # full info string after the opening ```
    start_pos: int = 0       # byte offset of the opening ``` in the source text


_FENCED_BLOCK_RE = re.compile(
    r"```(?P<info>[^\n]*)\n(?P<code>.*?)```",
    re.DOTALL,
)

_EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".sh": "bash",
    ".html": "html", ".css": "css", ".sql": "sql", ".go": "go",
    ".rs": "rust", ".java": "java", ".cpp": "cpp", ".c": "c",
    ".toml": "toml", ".txt": "text",
}

_SHELL_LANGUAGES = frozenset({
    "bash", "sh", "shell", "console", "terminal", "cmd",
    "powershell", "ps1", "zsh", "fish", "bat",
})

_FILE_BOUNDARY_RE = re.compile(
    r"^(?:"
    r"(?:#|//)\s*[-=]*\s*(?:File:\s*)?(?P<path1>[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]{1,8})\s*[-=]*"
    r"|"
    r"(?:#|//)\s*(?P<path2>[a-zA-Z0-9_/-]+/[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]{1,8})\s*$"
    r"|"
    r"/\*\s*[-=]*\s*(?:File:\s*)?(?P<path3>[a-zA-Z0-9_./-]+\.[a-zA-Z0-9]{1,8})\s*[-=]*\s*\*/"
    r")$",
    re.MULTILINE,
)


def extract_code_blocks(text: str) -> List[ExtractedCodeBlock]:
    """Return all fenced code blocks found in *text*.

    Each block carries an optional *filename* hint parsed from the info string.
    Blocks without a recognisable filename hint have ``filename=None``.

    Shell command blocks (language is bash/sh/console/etc.) are excluded.
    Blocks containing file-boundary comments are split into per-file blocks.
    """
    blocks: List[ExtractedCodeBlock] = []
    for match in _FENCED_BLOCK_RE.finditer(text):
        info = match.group("info").strip()
        code = match.group("code")

        language, filename = _parse_info(info)

        if language.lower() in _SHELL_LANGUAGES:
            continue

        sub_blocks = _try_split_multi_file(code, language, info, match.start())
        if sub_blocks:
            blocks.extend(sub_blocks)
        else:
            blocks.append(ExtractedCodeBlock(
                language=language,
                filename=filename,
                code=code,
                raw_info=info,
                start_pos=match.start(),
            ))
    return blocks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_info(info: str) -> Tuple[str, Optional[str]]:
    """Return (language, filename_or_None) from a code-fence info string."""
    if not info:
        return "", None

    has_colon = ":" in info
    parts = re.split(r"[:\s]+", info, maxsplit=1)
    first = parts[0].strip()
    rest = parts[1].strip() if len(parts) == 2 else ""

    if rest and _looks_like_path(rest, require_dir=not has_colon):
        return first, rest

    if _looks_like_path(first, require_dir=False):
        return _guess_language(first), first

    return first, None


def _looks_like_path(s: str, require_dir: bool = False) -> bool:
    """True if *s* looks like a file path.

    When *require_dir* is True, a bare filename (no directory separator) is
    rejected. This prevents ``python main.py`` (space-separated, no directory)
    from being treated as a file hint while still allowing ``python:main.py``
    (colon-separated explicit annotation).
    """
    if not s:
        return False
    has_sep = "/" in s or "\\" in s
    if require_dir and not has_sep:
        return False
    if has_sep:
        return True
    if re.search(r"\.[a-zA-Z0-9]{1,8}$", s):
        return True
    return False


def _try_split_multi_file(
    code: str, language: str, raw_info: str, start_pos: int,
) -> Optional[List[ExtractedCodeBlock]]:
    """If *code* contains file-boundary comments, split into per-file blocks.

    Returns None if no split is needed (0 or 1 file boundaries found).
    """
    boundaries: List[Tuple[int, str]] = []
    for m in _FILE_BOUNDARY_RE.finditer(code):
        path = m.group("path1") or m.group("path2") or m.group("path3")
        if path and "/" in path:
            boundaries.append((m.start(), path))

    if len(boundaries) < 2:
        return None

    blocks: List[ExtractedCodeBlock] = []
    for i, (offset, filepath) in enumerate(boundaries):
        line_end = code.index("\n", offset) + 1 if "\n" in code[offset:] else len(code)
        if i + 1 < len(boundaries):
            next_offset = boundaries[i + 1][0]
        else:
            next_offset = len(code)

        chunk = code[line_end:next_offset].strip("\n")
        if not chunk.strip():
            continue

        file_lang = _guess_language(filepath) or language
        blocks.append(ExtractedCodeBlock(
            language=file_lang,
            filename=filepath,
            code=chunk + "\n",
            raw_info=raw_info,
            start_pos=start_pos,
        ))

    return blocks if blocks else None


def _guess_language(filename: str) -> str:
    for ext, lang in _EXT_TO_LANG.items():
        if filename.endswith(ext):
            return lang
    return ""
