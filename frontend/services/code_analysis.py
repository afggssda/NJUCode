from __future__ import annotations

import ast
import json
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


EXCLUDED_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__"}
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


@dataclass
class FileIndexItem:
    path: str
    suffix: str
    size: int
    mtime: str


@dataclass
class TextSearchHit:
    path: str
    line: int
    snippet: str


@dataclass
class SymbolDef:
    name: str
    kind: str
    path: str
    line: int
    context: str


class CodeAnalyzer:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def set_workspace_root(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def _iter_files(self) -> list[Path]:
        files: list[Path] = []
        root = self.workspace_root
        for path in root.rglob("*"):
            if path.is_dir():
                continue
            parts = set(path.parts)
            if parts & EXCLUDED_DIRS:
                continue
            files.append(path)
        return files

    def _safe_read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace_root)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def _line_context(self, content: str, line_no: int, radius: int = 1) -> str:
        lines = content.splitlines()
        if not lines:
            return ""
        start = max(1, line_no - radius)
        end = min(len(lines), line_no + radius)
        out: list[str] = []
        for idx in range(start, end + 1):
            out.append(f"{idx}: {lines[idx - 1]}")
        return "\n".join(out)

    def scan_project(self) -> dict[str, Any]:
        items: list[FileIndexItem] = []
        for path in self._iter_files():
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append(
                FileIndexItem(
                    path=self._relative(path),
                    suffix=path.suffix.lower(),
                    size=stat.st_size,
                    mtime=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                )
            )

        items.sort(key=lambda x: x.path)
        by_suffix: dict[str, int] = defaultdict(int)
        total_size = 0
        for item in items:
            by_suffix[item.suffix or "<none>"] += 1
            total_size += item.size

        return {
            "type": "scan",
            "workspace": str(self.workspace_root),
            "summary": {
                "file_count": len(items),
                "total_bytes": total_size,
                "suffix_counts": dict(sorted(by_suffix.items(), key=lambda kv: kv[0])),
            },
            "files": [asdict(i) for i in items],
        }

    def search_text(self, query: str, case_sensitive: bool = False, use_regex: bool = False) -> dict[str, Any]:
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern: re.Pattern[str] | None = None
        if use_regex:
            pattern = re.compile(query, flags)

        hits: list[TextSearchHit] = []
        for path in self._iter_files():
            if path.suffix.lower() not in TEXT_FILE_EXTENSIONS:
                continue
            if path.stat().st_size > 1024 * 1024:
                continue
            content = self._safe_read_text(path)
            if not content:
                continue
            for idx, line in enumerate(content.splitlines(), start=1):
                matched = False
                if use_regex and pattern is not None:
                    matched = pattern.search(line) is not None
                else:
                    target = line if case_sensitive else line.lower()
                    needle = query if case_sensitive else query.lower()
                    matched = needle in target
                if matched:
                    hits.append(
                        TextSearchHit(path=self._relative(path), line=idx, snippet=line[:300])
                    )

        return {
            "type": "text_search",
            "query": query,
            "case_sensitive": case_sensitive,
            "use_regex": use_regex,
            "hit_count": len(hits),
            "hits": [asdict(h) for h in hits],
        }

    def _extract_python_defs(self, path: Path) -> tuple[list[SymbolDef], list[str], bool]:
        content = self._safe_read_text(path)
        if not content:
            return [], [], False
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return [], [], False

        symbols: list[SymbolDef] = []
        imports: list[str] = []
        has_entry = False

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbols.append(
                    SymbolDef(
                        name=node.name,
                        kind="class",
                        path=self._relative(path),
                        line=getattr(node, "lineno", 1),
                        context=self._line_context(content, getattr(node, "lineno", 1), radius=1),
                    )
                )
            elif isinstance(node, ast.FunctionDef):
                symbols.append(
                    SymbolDef(
                        name=node.name,
                        kind="def",
                        path=self._relative(path),
                        line=getattr(node, "lineno", 1),
                        context=self._line_context(content, getattr(node, "lineno", 1), radius=1),
                    )
                )
            elif isinstance(node, ast.AsyncFunctionDef):
                symbols.append(
                    SymbolDef(
                        name=node.name,
                        kind="async def",
                        path=self._relative(path),
                        line=getattr(node, "lineno", 1),
                        context=self._line_context(content, getattr(node, "lineno", 1), radius=1),
                    )
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
            elif isinstance(node, ast.If):
                # Detect: if __name__ == "__main__":
                test = node.test
                if (
                    isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"
                    and len(test.comparators) == 1
                    and isinstance(test.comparators[0], ast.Constant)
                    and test.comparators[0].value == "__main__"
                ):
                    has_entry = True

        symbols.sort(key=lambda s: (s.path, s.line, s.name))
        return symbols, sorted(set(imports)), has_entry

    def symbol_search(self, symbol_name: str, kind: str | None = None) -> dict[str, Any]:
        symbol_name_lower = symbol_name.lower()
        results: list[dict[str, Any]] = []

        for path in self._iter_files():
            if path.suffix.lower() != ".py":
                continue
            symbols, _, _ = self._extract_python_defs(path)
            for symbol in symbols:
                if symbol_name_lower != symbol.name.lower():
                    continue
                if kind and symbol.kind != kind:
                    continue
                results.append(asdict(symbol))

        return {
            "type": "symbol_search",
            "symbol": symbol_name,
            "kind": kind,
            "hit_count": len(results),
            "hits": results,
        }

    def summarize_file(self, rel_path: str) -> dict[str, Any]:
        full_path = (self.workspace_root / rel_path).resolve()
        # Security: Ensure path stays within workspace
        try:
            full_path.relative_to(self.workspace_root.resolve())
        except ValueError:
            return {
                "type": "file_summary",
                "path": rel_path,
                "error": "path_outside_workspace",
            }

        if not full_path.exists() or not full_path.is_file():
            return {
                "type": "file_summary",
                "path": rel_path,
                "error": "file_not_found",
            }

        summary: dict[str, Any] = {
            "type": "file_summary",
            "path": self._relative(full_path),
            "suffix": full_path.suffix.lower(),
            "main_classes": [],
            "main_functions": [],
            "external_dependencies": [],
            "entry_function": False,
            "purpose": "",
        }

        if full_path.suffix.lower() == ".py":
            symbols, imports, has_entry = self._extract_python_defs(full_path)
            summary["main_classes"] = [s["name"] for s in [asdict(x) for x in symbols] if s["kind"] == "class"]
            summary["main_functions"] = [s["name"] for s in [asdict(x) for x in symbols] if "def" in s["kind"]]
            summary["external_dependencies"] = imports
            summary["entry_function"] = has_entry
            purpose_parts: list[str] = []
            if summary["main_classes"]:
                purpose_parts.append(f"定义了 {len(summary['main_classes'])} 个类")
            if summary["main_functions"]:
                purpose_parts.append(f"定义了 {len(summary['main_functions'])} 个函数")
            if has_entry:
                purpose_parts.append("包含可直接运行入口")
            if imports:
                purpose_parts.append(f"依赖 {len(imports)} 个模块")
            summary["purpose"] = "，".join(purpose_parts) if purpose_parts else "该文件主要包含基础脚本或配置内容"
        else:
            content = self._safe_read_text(full_path)
            lines = content.splitlines()
            summary["purpose"] = f"文本类文件，共 {len(lines)} 行"

        return summary

    def _build_python_module_map(self) -> dict[str, str]:
        module_to_path: dict[str, str] = {}
        for path in self._iter_files():
            if path.suffix.lower() != ".py":
                continue
            rel = self._relative(path)
            module = rel[:-3].replace("/", ".")
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")]
            module_to_path[module] = rel
        return module_to_path

    def build_dependency_graph(self) -> dict[str, Any]:
        module_map = self._build_python_module_map()
        forward: dict[str, set[str]] = defaultdict(set)
        reverse: dict[str, set[str]] = defaultdict(set)

        for module_name, rel_path in module_map.items():
            full_path = self.workspace_root / rel_path
            _, imports, _ = self._extract_python_defs(full_path)
            for imported in imports:
                candidate = imported
                if candidate in module_map:
                    to_path = module_map[candidate]
                    forward[rel_path].add(to_path)
                    reverse[to_path].add(rel_path)
                else:
                    # Try package-level match: a.b.c -> a.b
                    bits = candidate.split(".")
                    while len(bits) > 1:
                        bits.pop()
                        prefix = ".".join(bits)
                        if prefix in module_map:
                            to_path = module_map[prefix]
                            forward[rel_path].add(to_path)
                            reverse[to_path].add(rel_path)
                            break

        graph = {
            "type": "dependency_graph",
            "nodes": sorted(module_map.values()),
            "forward": {k: sorted(v) for k, v in sorted(forward.items())},
            "reverse": {k: sorted(v) for k, v in sorted(reverse.items())},
        }
        return graph

    def neighbors(self, rel_path: str, depth: int = 1) -> dict[str, Any]:
        depth = max(1, min(depth, 2))
        graph = self.build_dependency_graph()
        forward = graph["forward"]
        reverse = graph["reverse"]

        dep_layers: dict[str, list[str]] = {}
        rev_layers: dict[str, list[str]] = {}

        dep_seen = {rel_path}
        q = deque([(rel_path, 0)])
        while q:
            node, d = q.popleft()
            if d >= depth:
                continue
            next_nodes = forward.get(node, [])
            dep_layers[str(d + 1)] = sorted(set(dep_layers.get(str(d + 1), []) + next_nodes))
            for nxt in next_nodes:
                if nxt in dep_seen:
                    continue
                dep_seen.add(nxt)
                q.append((nxt, d + 1))

        rev_seen = {rel_path}
        q = deque([(rel_path, 0)])
        while q:
            node, d = q.popleft()
            if d >= depth:
                continue
            next_nodes = reverse.get(node, [])
            rev_layers[str(d + 1)] = sorted(set(rev_layers.get(str(d + 1), []) + next_nodes))
            for nxt in next_nodes:
                if nxt in rev_seen:
                    continue
                rev_seen.add(nxt)
                q.append((nxt, d + 1))

        return {
            "type": "neighbors",
            "path": rel_path,
            "depth": depth,
            "depends_on": dep_layers,
            "depended_by": rev_layers,
        }

    def recall_files(self, requirement: str, top_k: int = 10) -> dict[str, Any]:
        top_k = max(1, min(top_k, 30))
        q_tokens = [t for t in re.split(r"[^\w]+", requirement.lower()) if t]

        graph = self.build_dependency_graph()
        nodes = graph.get("nodes", [])
        symbol_cache: dict[str, list[str]] = {}

        scored: list[tuple[float, str, dict[str, float]]] = []
        for rel_path in nodes:
            full_path = self.workspace_root / rel_path
            content = self._safe_read_text(full_path).lower()
            path_lower = rel_path.lower()

            if rel_path not in symbol_cache:
                symbols, _, _ = self._extract_python_defs(full_path)
                symbol_cache[rel_path] = [s.name.lower() for s in symbols]

            keyword_score = 0.0
            symbol_score = 0.0
            for token in q_tokens:
                if token in content:
                    keyword_score += 1.0
                if token in path_lower:
                    keyword_score += 0.8
                if any(token in s for s in symbol_cache[rel_path]):
                    symbol_score += 1.2

            path_weight = 0.0
            if "/core/" in f"/{path_lower}/" or path_lower.startswith("src/core"):
                path_weight += 1.5
            if path_lower.startswith("frontend/"):
                path_weight += 0.5

            total = keyword_score + symbol_score + path_weight
            if total <= 0:
                continue
            scored.append(
                (
                    total,
                    rel_path,
                    {
                        "keyword_score": round(keyword_score, 2),
                        "symbol_score": round(symbol_score, 2),
                        "path_weight": round(path_weight, 2),
                    },
                )
            )

        scored.sort(key=lambda x: (-x[0], x[1]))
        top = scored[:top_k]
        return {
            "type": "recall",
            "query": requirement,
            "top_k": top_k,
            "results": [
                {
                    "path": path,
                    "score": round(score, 2),
                    "components": components,
                }
                for score, path, components in top
            ],
        }

    def impact_analysis(self, target: str, depth: int = 2) -> dict[str, Any]:
        depth = max(1, min(depth, 2))
        graph = self.build_dependency_graph()
        reverse = graph.get("reverse", {})
        nodes = set(graph.get("nodes", []))

        target_path = target
        target_symbol: str | None = None
        if target not in nodes:
            symbol_result = self.symbol_search(target)
            if symbol_result.get("hits"):
                first = symbol_result["hits"][0]
                target_path = first["path"]
                target_symbol = target

        if target_path not in nodes:
            return {
                "type": "impact",
                "target": target,
                "error": "target_not_found",
            }

        affected: set[str] = set()
        q = deque([(target_path, 0)])
        while q:
            node, d = q.popleft()
            if d >= depth:
                continue
            for nxt in reverse.get(node, []):
                if nxt in affected:
                    continue
                affected.add(nxt)
                q.append((nxt, d + 1))

        fan_in = len(reverse.get(target_path, []))
        if fan_in >= 5 or len(affected) >= 8:
            risk = "high"
        elif fan_in >= 2 or len(affected) >= 3:
            risk = "medium"
        else:
            risk = "low"

        suggestions = [target_path]
        suggestions.extend(sorted(list(reverse.get(target_path, [])))[:3])
        suggestions.extend(sorted(list(affected - set(suggestions)))[:4])

        return {
            "type": "impact",
            "target": target,
            "target_path": target_path,
            "target_symbol": target_symbol,
            "depth": depth,
            "risk": risk,
            "direct_callers": sorted(reverse.get(target_path, [])),
            "affected_files": sorted(affected),
            "suggested_read_order": suggestions,
        }

    def to_json(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _shorten(self, text: str, max_len: int = 140) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "..."

    def to_text(self, payload: dict[str, Any]) -> str:
        ptype = payload.get("type", "unknown")
        if ptype == "scan":
            summary = payload.get("summary", {})
            return (
                "[项目扫描]\n"
                f"文件数: {summary.get('file_count', 0)}\n"
                f"总大小: {summary.get('total_bytes', 0)} bytes\n"
                f"后缀统计: {summary.get('suffix_counts', {})}"
            )

        if ptype == "text_search":
            query = payload.get("query", "")
            all_hits = payload.get("hits", [])
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for hit in all_hits:
                grouped[hit["path"]].append(hit)

            lines = [
                f"[文本检索] 关键词: {query}",
                f"总命中: {payload.get('hit_count', 0)} 条",
            ]

            max_files = 8
            max_hits_per_file = 3
            shown_files = 0
            shown_hits = 0
            for path, file_hits in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                if shown_files >= max_files:
                    break
                shown_files += 1
                lines.append(f"\n文件: {path} (命中 {len(file_hits)} 条)")
                for item in file_hits[:max_hits_per_file]:
                    shown_hits += 1
                    snippet = self._shorten(item.get("snippet", ""), 150)
                    lines.append(f"  L{item['line']}: {snippet}")

            hidden = payload.get("hit_count", 0) - shown_hits
            if hidden > 0:
                lines.append(f"\n... 还有 {hidden} 条结果未展示，可缩小关键词范围继续检索")
            return "\n".join(lines)

        if ptype == "symbol_search":
            lines = [f"[符号检索] 命中 {payload.get('hit_count', 0)} 条"]
            for hit in payload.get("hits", [])[:20]:
                lines.append(f"- {hit['kind']} {hit['name']} @ {hit['path']}:{hit['line']}")
            if payload.get("hit_count", 0) > 20:
                lines.append("- ... (仅显示前20条)")
            return "\n".join(lines)

        if ptype == "file_summary":
            if payload.get("error"):
                return f"[文件摘要] 失败: {payload['error']}"
            return (
                f"[文件摘要] {payload.get('path')}\n"
                f"作用: {payload.get('purpose', '')}\n"
                f"类: {payload.get('main_classes', [])}\n"
                f"函数: {payload.get('main_functions', [])}\n"
                f"依赖: {payload.get('external_dependencies', [])}\n"
                f"入口: {payload.get('entry_function', False)}"
            )

        if ptype == "neighbors":
            return (
                f"[依赖邻接] {payload.get('path')} depth={payload.get('depth')}\n"
                f"依赖谁: {payload.get('depends_on', {})}\n"
                f"谁依赖它: {payload.get('depended_by', {})}"
            )

        if ptype == "recall":
            lines = [f"[任务召回] query={payload.get('query')} top_k={payload.get('top_k')}"]
            for item in payload.get("results", []):
                lines.append(
                    f"- {item['path']} score={item['score']} components={item['components']}"
                )
            return "\n".join(lines)

        if ptype == "impact":
            if payload.get("error"):
                return f"[影响面分析] 失败: {payload['error']}"
            return (
                f"[影响面分析] target={payload.get('target')} risk={payload.get('risk')}\n"
                f"目标文件: {payload.get('target_path')}\n"
                f"直接依赖方: {payload.get('direct_callers', [])}\n"
                f"影响文件: {payload.get('affected_files', [])}\n"
                f"建议阅读顺序: {payload.get('suggested_read_order', [])}"
            )

        return self.to_json(payload)

    def run_command(self, raw_command: str) -> dict[str, Any]:
        cmd = raw_command.strip()
        if not cmd:
            return {"type": "error", "error": "empty_command"}

        if cmd in {"/help", "/analysis help"}:
            return {
                "type": "help",
                "commands": [
                    "/scan",
                    "/search <keyword> [--regex] [--case]",
                    "/symbol <name>",
                    "/summary <relative_path>",
                    "/deps <relative_path> [--depth 1|2]",
                    "/recall <requirement text> [--top 5..30]",
                    "/impact <symbol_or_relative_path> [--depth 1|2]",
                    "/mcp <mcp.server.tool> [json-params]",
                ],
            }

        if cmd.startswith("/scan"):
            return self.scan_project()

        if cmd.startswith("/search "):
            body = cmd[len("/search "):].strip()
            use_regex = "--regex" in body
            case_sensitive = "--case" in body
            body = body.replace("--regex", "").replace("--case", "").strip()
            return self.search_text(body, case_sensitive=case_sensitive, use_regex=use_regex)

        if cmd.startswith("/symbol "):
            symbol_name = cmd[len("/symbol "):].strip()
            return self.symbol_search(symbol_name)

        if cmd.startswith("/summary "):
            rel_path = cmd[len("/summary "):].strip()
            return self.summarize_file(rel_path)

        if cmd.startswith("/deps "):
            body = cmd[len("/deps "):].strip()
            depth = 1
            m = re.search(r"--depth\s+(\d+)", body)
            if m:
                depth = int(m.group(1))
                body = re.sub(r"--depth\s+\d+", "", body).strip()
            return self.neighbors(body, depth=depth)

        if cmd.startswith("/recall "):
            body = cmd[len("/recall "):].strip()
            top_k = 10
            m = re.search(r"--top\s+(\d+)", body)
            if m:
                top_k = int(m.group(1))
                body = re.sub(r"--top\s+\d+", "", body).strip()
            return self.recall_files(body, top_k=top_k)

        if cmd.startswith("/impact "):
            body = cmd[len("/impact "):].strip()
            depth = 2
            m = re.search(r"--depth\s+(\d+)", body)
            if m:
                depth = int(m.group(1))
                body = re.sub(r"--depth\s+\d+", "", body).strip()
            return self.impact_analysis(body, depth=depth)

        return {"type": "error", "error": "unknown_command", "command": cmd}
