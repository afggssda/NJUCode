"""Static code metrics and maintainability hotspot analysis.

This module adds a read-only project intelligence layer on top of the existing
search/dependency features.  It uses Python AST parsing, lightweight import
resolution, cyclomatic-complexity counting, and strongly connected component
detection to highlight files that may deserve refactoring attention.
"""

from __future__ import annotations

import ast
import tokenize
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Iterable


# Runtime, cache, and generated directories are ignored so metrics describe the
# source tree the user maintains, not local tool state.
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


@dataclass
class FunctionMetric:
    # qualname preserves class nesting, e.g. "Panel.render", so the output is
    # precise enough to jump from a metric row back to code.
    path: str
    name: str
    qualname: str
    line: int
    end_line: int
    kind: str
    complexity: int
    # The split counters make the complexity score explainable in JSON output.
    branch_points: int
    loop_points: int
    exception_points: int


@dataclass
class FileMetric:
    # File-level metrics combine local complexity with graph position.
    path: str
    lines: int
    code_lines: int
    comment_lines: int
    blank_lines: int
    class_count: int
    function_count: int
    method_count: int
    total_complexity: int
    max_complexity: int
    avg_complexity: float
    fan_in: int
    fan_out: int
    # cycle_id links this file back to the cycles list when it participates in
    # an import strongly connected component.
    cycle_id: int | None
    hotspot_score: float


class ComplexityCounter(ast.NodeVisitor):
    """Count decision points inside one function body."""

    def __init__(self) -> None:
        # Complexity starts at 1 for the straight-line path through a function.
        self.branch_points = 0
        self.loop_points = 0
        self.exception_points = 0

    @property
    def total(self) -> int:
        # This is a lightweight McCabe-style metric, not a full linter clone.
        return 1 + self.branch_points + self.loop_points + self.exception_points

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Nested functions are reported separately by FunctionCollector.
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_If(self, node: ast.If) -> None:
        self.branch_points += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.branch_points += 1
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        # Pattern matching can hide many branches in compact syntax, so each
        # case contributes to the decision count.
        self.branch_points += max(1, len(node.cases))
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # a and b and c has multiple short-circuit paths inside one expression.
        self.branch_points += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        # Comprehension loops and inline filters are compact but still branch.
        self.loop_points += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.loop_points += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.loop_points += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.loop_points += 1
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        # Handlers, else, and finally blocks all add alternate control-flow paths.
        self.exception_points += len(node.handlers) + int(bool(node.orelse)) + int(bool(node.finalbody))
        self.generic_visit(node)


class FunctionCollector(ast.NodeVisitor):
    """Collect functions with class-qualified names."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.class_stack: list[str] = []
        self.functions: list[FunctionMetric] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Keep a stack instead of only the current class so nested classes still
        # produce useful qualified names.
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node)
        self.generic_visit(node)

    def _record_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        # Count complexity from the body children so the function node itself
        # does not recurse into nested functions as part of the outer score.
        counter = ComplexityCounter()
        for child in node.body:
            counter.visit(child)
        prefix = ".".join(self.class_stack)
        qualname = f"{prefix}.{node.name}" if prefix else node.name
        kind = "method" if self.class_stack else "function"
        self.functions.append(
            FunctionMetric(
                path=self.path,
                name=node.name,
                qualname=qualname,
                line=getattr(node, "lineno", 1),
                end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                kind=kind,
                complexity=counter.total,
                branch_points=counter.branch_points,
                loop_points=counter.loop_points,
                exception_points=counter.exception_points,
            )
        )


class ProjectMetricsAnalyzer:
    """Compute static complexity and dependency metrics for Python files."""

    def __init__(self, workspace_root: Path, max_file_bytes: int = 1_000_000) -> None:
        self.workspace_root = workspace_root
        # Large generated or bundled files can dominate metrics and slow the UI.
        self.max_file_bytes = max_file_bytes

    def _relative(self, path: Path) -> str:
        # Stable workspace-relative paths make reports portable across machines.
        try:
            return path.relative_to(self.workspace_root).as_posix()
        except ValueError:
            return path.as_posix()

    def _is_test_file(self, path: Path) -> bool:
        # Production maintainability is the default lens; tests can be included
        # explicitly when the user wants to audit test code too.
        try:
            parts = path.relative_to(self.workspace_root).parts
        except ValueError:
            parts = path.parts
        return "tests" in parts or path.name.startswith("test_") or path.name.endswith("_test.py")

    def _iter_python_files(self, include_tests: bool = False, path_filter: str = "") -> Iterable[Path]:
        # File filtering happens up front to avoid parsing irrelevant trees.
        path_filter = path_filter.strip().replace("\\", "/")
        for path in self.workspace_root.rglob("*.py"):
            if path.is_dir():
                continue
            rel = self._relative(path)
            if path_filter and path_filter not in rel:
                continue
            try:
                rel_parts = set(path.relative_to(self.workspace_root).parts)
            except ValueError:
                rel_parts = set(path.parts)
            if rel_parts & EXCLUDED_DIRS:
                continue
            # Keep parity with /tasks: tests are opt-in to reduce noise.
            if not include_tests and self._is_test_file(path):
                continue
            try:
                if path.stat().st_size > self.max_file_bytes:
                    continue
            except OSError:
                continue
            yield path

    def _read_text(self, path: Path) -> str:
        # Metrics should be best-effort; unreadable files simply drop out of the
        # report instead of failing the whole command.
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _module_name(self, rel_path: str) -> str:
        # Convert a path into the importable module name used for graph matching.
        module = rel_path[:-3].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        return module

    def _line_stats(self, text: str) -> tuple[int, int, int, int]:
        # tokenize gives more accurate code/comment line counts than string
        # prefix checks, especially for inline comments and multiline strings.
        lines = text.splitlines()
        blank_lines = sum(1 for line in lines if not line.strip())
        comment_lines: set[int] = set()
        code_lines: set[int] = set()
        try:
            tokens = tokenize.generate_tokens(StringIO(text).readline)
            for token in tokens:
                if token.type == tokenize.COMMENT:
                    comment_lines.add(token.start[0])
                elif token.type not in {
                    tokenize.ENCODING,
                    tokenize.ENDMARKER,
                    tokenize.INDENT,
                    tokenize.DEDENT,
                    tokenize.NEWLINE,
                    tokenize.NL,
                }:
                    code_lines.add(token.start[0])
        except tokenize.TokenError:
            # Broken/in-progress files still get a conservative nonblank count.
            code_lines = {idx for idx, line in enumerate(lines, start=1) if line.strip()}
        return len(lines), len(code_lines), len(comment_lines), blank_lines

    def _parse_files(
        self,
        include_tests: bool,
        path_filter: str = "",
    ) -> tuple[dict[str, ast.AST], dict[str, str], dict[str, str]]:
        # Return trees, raw text, and module mapping together so later phases
        # share one filesystem pass.
        trees: dict[str, ast.AST] = {}
        texts: dict[str, str] = {}
        module_to_path: dict[str, str] = {}
        for path in self._iter_python_files(include_tests=include_tests, path_filter=path_filter):
            rel = self._relative(path)
            text = self._read_text(path)
            try:
                tree = ast.parse(text, filename=rel)
            except SyntaxError:
                # Syntax errors are left to Project Doctor; metrics only report
                # files that can be analyzed structurally.
                continue
            trees[rel] = tree
            texts[rel] = text
            module_to_path[self._module_name(rel)] = rel
        return trees, texts, module_to_path

    def _resolve_relative_import(self, current_module: str, level: int, module: str | None) -> str:
        # Resolve "from .x import y" against the current package well enough for
        # internal project imports.  External packages are ignored later.
        parts = current_module.split(".")
        if current_module.endswith("__init__"):
            package_parts = parts[:-1]
        else:
            package_parts = parts[:-1]
        keep = max(0, len(package_parts) - max(0, level - 1))
        base = package_parts[:keep]
        if module:
            base.extend(module.split("."))
        return ".".join(part for part in base if part)

    def _import_candidates(self, current_module: str, node: ast.AST) -> list[str]:
        # Include both the module and imported child symbols as candidates so
        # "from pkg import submodule" can resolve to pkg.submodule.py.
        candidates: list[str] = []
        if isinstance(node, ast.Import):
            candidates.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = self._resolve_relative_import(current_module, node.level, node.module)
            if base:
                candidates.append(base)
                candidates.extend(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
        return candidates

    def _resolve_import_path(self, imported: str, module_to_path: dict[str, str]) -> str | None:
        # Try the exact module first, then progressively fall back to package
        # prefixes such as a.b.c -> a.b -> a.
        if imported in module_to_path:
            return module_to_path[imported]
        bits = imported.split(".")
        while len(bits) > 1:
            bits.pop()
            prefix = ".".join(bits)
            if prefix in module_to_path:
                return module_to_path[prefix]
        return None

    def _dependency_graph(
        self,
        trees: dict[str, ast.AST],
        module_to_path: dict[str, str],
    ) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        # forward[A] contains files imported by A; reverse[B] contains files
        # that import B.  Both directions are needed for hotspot scoring.
        forward: dict[str, set[str]] = defaultdict(set)
        reverse: dict[str, set[str]] = defaultdict(set)
        path_to_module = {path: module for module, path in module_to_path.items()}
        for rel_path, tree in trees.items():
            current_module = path_to_module.get(rel_path, self._module_name(rel_path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                for imported in self._import_candidates(current_module, node):
                    target = self._resolve_import_path(imported, module_to_path)
                    if target and target != rel_path:
                        forward[rel_path].add(target)
                        reverse[target].add(rel_path)
        return forward, reverse

    def _find_cycles(self, nodes: list[str], forward: dict[str, set[str]]) -> list[list[str]]:
        # Tarjan's algorithm finds strongly connected components in one graph
        # pass.  Components with more than one file are import cycles.
        index = 0
        stack: list[str] = []
        on_stack: set[str] = set()
        indices: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        cycles: list[list[str]] = []

        def strongconnect(node: str) -> None:
            nonlocal index
            # indices records DFS discovery order; lowlinks tracks the earliest
            # node reachable from this subtree.
            indices[node] = index
            lowlinks[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)

            for neighbor in forward.get(node, set()):
                if neighbor not in indices:
                    strongconnect(neighbor)
                    lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
                elif neighbor in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[neighbor])

            if lowlinks[node] != indices[node]:
                return
            # node is the root of a strongly connected component.
            component: list[str] = []
            while stack:
                current = stack.pop()
                on_stack.remove(current)
                component.append(current)
                if current == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

        for node in nodes:
            if node not in indices:
                strongconnect(node)
        return sorted(cycles, key=lambda cycle: (-len(cycle), cycle))

    def _hotspot_score(
        self,
        code_lines: int,
        total_complexity: int,
        max_complexity: int,
        fan_in: int,
        fan_out: int,
        in_cycle: bool,
    ) -> float:
        # The score is intentionally transparent rather than statistically
        # trained: complexity dominates, dependency centrality and cycles add
        # maintenance-risk pressure, and size nudges large files upward.
        score = (
            total_complexity * 1.4
            + max_complexity * 1.8
            + code_lines / 35
            + fan_in * 2.2
            + fan_out * 1.1
            + (12 if in_cycle else 0)
        )
        return round(score, 2)

    def analyze(
        self,
        top_n: int = 10,
        include_tests: bool = False,
        path_filter: str = "",
    ) -> dict[str, Any]:
        # Clamp top_n so a chat command cannot flood the interface.
        top_n = max(1, min(50, int(top_n or 10)))
        path_filter = path_filter.strip().replace("\\", "/")
        trees, texts, module_to_path = self._parse_files(
            include_tests=include_tests,
            path_filter=path_filter,
        )
        nodes = sorted(trees)
        forward, reverse = self._dependency_graph(trees, module_to_path)
        cycles = self._find_cycles(nodes, forward)
        cycle_lookup: dict[str, int] = {}
        # Precompute cycle membership for O(1) file metric annotation.
        for idx, cycle in enumerate(cycles, start=1):
            for path in cycle:
                cycle_lookup[path] = idx

        all_functions: list[FunctionMetric] = []
        files: list[FileMetric] = []
        syntax_ok_files = 0
        for rel_path in nodes:
            # Each parsed file gets both local AST metrics and graph metrics.
            tree = trees[rel_path]
            syntax_ok_files += 1
            collector = FunctionCollector(rel_path)
            collector.visit(tree)
            functions = collector.functions
            all_functions.extend(functions)

            class_count = sum(1 for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
            method_count = sum(1 for item in functions if item.kind == "method")
            function_count = sum(1 for item in functions if item.kind == "function")
            total_complexity = sum(item.complexity for item in functions)
            max_complexity = max((item.complexity for item in functions), default=0)
            avg_complexity = round(total_complexity / len(functions), 2) if functions else 0.0
            lines, code_lines, comment_lines, blank_lines = self._line_stats(texts[rel_path])
            fan_in = len(reverse.get(rel_path, set()))
            fan_out = len(forward.get(rel_path, set()))
            cycle_id = cycle_lookup.get(rel_path)
            files.append(
                FileMetric(
                    path=rel_path,
                    lines=lines,
                    code_lines=code_lines,
                    comment_lines=comment_lines,
                    blank_lines=blank_lines,
                    class_count=class_count,
                    function_count=function_count,
                    method_count=method_count,
                    total_complexity=total_complexity,
                    max_complexity=max_complexity,
                    avg_complexity=avg_complexity,
                    fan_in=fan_in,
                    fan_out=fan_out,
                    cycle_id=cycle_id,
                    hotspot_score=self._hotspot_score(
                        code_lines,
                        total_complexity,
                        max_complexity,
                        fan_in,
                        fan_out,
                        bool(cycle_id),
                    ),
                )
            )

        files.sort(key=lambda item: (-item.hotspot_score, item.path))
        # Keep the function list independent of file hotspots; a small file can
        # still contain the most complex function in the project.
        all_functions.sort(key=lambda item: (-item.complexity, item.path, item.line))
        tag_counts = Counter()
        for item in all_functions:
            # Thresholds mirror common lightweight complexity review bands.
            if item.complexity >= 15:
                tag_counts["very_complex_functions"] += 1
            elif item.complexity >= 10:
                tag_counts["complex_functions"] += 1

        return {
            # The payload is shaped for CodeAnalyzer.to_text, Skills, and tests.
            "type": "code_metrics",
            "workspace": str(self.workspace_root),
            "summary": {
                "python_files": len(nodes),
                "parsed_files": syntax_ok_files,
                "total_lines": sum(item.lines for item in files),
                "total_code_lines": sum(item.code_lines for item in files),
                "classes": sum(item.class_count for item in files),
                "functions": len(all_functions),
                "dependency_edges": sum(len(edges) for edges in forward.values()),
                "cycles": len(cycles),
                "complex_functions": tag_counts.get("complex_functions", 0),
                "very_complex_functions": tag_counts.get("very_complex_functions", 0),
            },
            "filters": {
                "top_n": top_n,
                "include_tests": include_tests,
                "path": path_filter,
            },
            "hotspots": [asdict(item) for item in files[:top_n]],
            "complex_functions": [asdict(item) for item in all_functions[:top_n]],
            "cycles": [
                {"id": idx, "size": len(cycle), "files": cycle}
                for idx, cycle in enumerate(cycles, start=1)
            ][:top_n],
        }
