"""Comprehensive regression tests for NJUCode.

Run from the repository root with:

    python test_all_features.py
    python test_all_features.py --coverage

The file intentionally uses only the Python standard library test runner so it
can run in a fresh conda environment after ``pip install -r requirements.txt``.
It exercises both focused service behavior and the cross-project doctor added
for full-system checks.
"""

from __future__ import annotations

import ast
import io
import json
import os
import shutil
import sys
import time
import unittest
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Any


CORE_COVERAGE_FILES = [
    "njucode/models.py",
    "njucode/mcp/models.py",
    "njucode/mcp/tool_adapter.py",
    "njucode/skills/models.py",
    "njucode/services/code_analysis.py",
    "njucode/services/code_extractor.py",
    "njucode/services/code_metrics.py",
    "njucode/services/context_compressor.py",
    "njucode/services/openai_client.py",
    "njucode/services/project_testing.py",
    "njucode/services/runtime_tools.py",
    "njucode/services/task_index.py",
]

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
# The standalone unittest report is the only report we want from this script.
# /doctor is still tested, but its application-time report files are disabled
# here to avoid producing duplicate project_doctor_*.md/json artifacts.
os.environ.setdefault("NJU_CODE_DISABLE_DOCTOR_REPORT", "1")
sys.dont_write_bytecode = True

# Resolve the repository root once so all tests can use stable absolute paths.
# This makes the file runnable from the repo root or through IDE test runners.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    # The project is not packaged as an installable wheel, so tests add the
    # repository root to sys.path before importing njucode.* modules.
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Test doubles and workspace helpers
# ---------------------------------------------------------------------------

class DummyAuditLogger:
    """Small audit logger stand-in used by PatchEngine and SkillExecutor tests."""

    def __init__(self) -> None:
        # Tests assert on this list to verify audit events are emitted.
        self.logs: list[Any] = []

    def record(self, log: Any) -> None:
        # Match the production AuditLogger.record interface without touching
        # .nju_code/audit logs.
        self.logs.append(log)

    def load(self) -> None:
        return None

    def save(self) -> None:
        return None


class FakeModelClient:
    """Deterministic model client so compression tests never call the network."""

    def __init__(self, text: str = "") -> None:
        # The default text is deliberately structured enough to pass summary
        # validation in ContextCompressor.
        self.text = text or (
            "[User Intent]\n"
            "- Preserve project behavior.\n\n"
            "[Key Conclusions]\n"
            "- Keep recent messages and summarize older ones."
        )
        self.requests: list[Any] = []

    def chat(self, request: Any) -> str:
        # Store requests so a future assertion can verify prompt construction.
        self.requests.append(request)
        return self.text


class WorkspaceTempDir:
    """Workspace-local temporary directory helper.

    The test suite creates scratch projects under the repository rather than
    under the OS temp directory because the course Windows sandbox may deny
    writes outside the workspace.
    """

    def __init__(self) -> None:
        # Use a UUID folder so parallel or repeated runs do not collide.
        self.path = ROOT / ".test_tmp" / f"case_{uuid4().hex}"
        self.name = str(self.path)
        self.path.mkdir(parents=True, exist_ok=True)

    def __enter__(self) -> str:
        self.path.mkdir(parents=True, exist_ok=True)
        return str(self.path)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        # ignore_errors keeps cleanup best-effort in sandboxes that deny unlink.
        shutil.rmtree(self.path, ignore_errors=True)


def make_workspace() -> WorkspaceTempDir:
    """Create an isolated scratch workspace for one test case."""
    return WorkspaceTempDir()


def write(path: Path, content: str) -> None:
    """Write UTF-8 content while creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def workspace_allows_unlink() -> bool:
    """Return whether the active sandbox permits deleting files.

    Some tests validate delete/rollback behavior.  They are skipped when the
    current environment allows writes but denies unlink operations.
    """
    probe = ROOT / ".test_tmp" / f"unlink_probe_{uuid4().hex}.txt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Probe deletion behavior directly instead of guessing from platform.
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Test report generation
# ---------------------------------------------------------------------------

class ReportingTextTestResult(unittest.TextTestResult):
    """TextTestResult variant that records per-test status and elapsed time."""

    def __init__(self, stream: Any, descriptions: bool, verbosity: int) -> None:
        super().__init__(stream, descriptions, verbosity)
        self.test_records: list[dict[str, Any]] = []
        self._test_started_at: dict[str, float] = {}

    def startTest(self, test: unittest.case.TestCase) -> None:
        self._test_started_at[test.id()] = time.perf_counter()
        super().startTest(test)

    def _elapsed_ms(self, test: unittest.case.TestCase) -> int:
        started_at = self._test_started_at.pop(test.id(), time.perf_counter())
        return int((time.perf_counter() - started_at) * 1000)

    def _record(self, test: unittest.case.TestCase, status: str, detail: str = "") -> None:
        self.test_records.append(
            {
                "id": test.id(),
                "status": status,
                "elapsed_ms": self._elapsed_ms(test),
                "detail": detail,
            }
        )

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        self._record(test, "pass")
        super().addSuccess(test)

    def addFailure(self, test: unittest.case.TestCase, err: Any) -> None:
        self._record(test, "fail", self._exc_info_to_string(err, test))
        super().addFailure(test, err)

    def addError(self, test: unittest.case.TestCase, err: Any) -> None:
        self._record(test, "error", self._exc_info_to_string(err, test))
        super().addError(test, err)

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        self._record(test, "skip", reason)
        super().addSkip(test, reason)

    def addExpectedFailure(self, test: unittest.case.TestCase, err: Any) -> None:
        self._record(test, "expected_failure", self._exc_info_to_string(err, test))
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test: unittest.case.TestCase) -> None:
        self._record(test, "unexpected_success")
        super().addUnexpectedSuccess(test)


def _status_counts(result: ReportingTextTestResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in result.test_records:
        status = record["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


def _write_unittest_report(
    result: ReportingTextTestResult,
    started_at: datetime,
    finished_at: datetime,
    elapsed_seconds: float,
    selected_tests: list[str],
) -> tuple[Path, Path]:
    """Persist JSON and Markdown reports for the standalone unittest suite."""

    reports_dir = ROOT / ".nju_code" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = finished_at.strftime("%Y%m%d_%H%M%S")
    stem = f"test_all_features_{timestamp}_{uuid4().hex[:8]}"
    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"

    counts = _status_counts(result)
    payload = {
        "type": "unittest_report",
        "suite": "test_all_features.py",
        "workspace": str(ROOT),
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "selected_tests": selected_tests,
        "summary": {
            "total": result.testsRun,
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "error": counts.get("error", 0),
            "skip": counts.get("skip", 0),
            "expected_failure": counts.get("expected_failure", 0),
            "unexpected_success": counts.get("unexpected_success", 0),
            "successful": result.wasSuccessful(),
        },
        "tests": result.test_records,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = payload["summary"]
    status = "PASS" if result.wasSuccessful() else "FAIL"
    lines = [
        f"# test_all_features.py Report: {status}",
        "",
        f"- Workspace: `{ROOT}`",
        f"- Started: `{payload['started_at']}`",
        f"- Finished: `{payload['finished_at']}`",
        f"- Duration: `{payload['elapsed_seconds']}s`",
        f"- Selected tests: `{', '.join(selected_tests) if selected_tests else 'all'}`",
        "",
        "## Summary",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Total | {summary['total']} |",
        f"| Pass | {summary['pass']} |",
        f"| Fail | {summary['fail']} |",
        f"| Error | {summary['error']} |",
        f"| Skip | {summary['skip']} |",
        f"| Expected failure | {summary['expected_failure']} |",
        f"| Unexpected success | {summary['unexpected_success']} |",
        "",
        "## Non-Passing Tests",
        "",
    ]
    non_passing = [record for record in result.test_records if record["status"] != "pass"]
    if not non_passing:
        lines.append("All tests passed.")
    else:
        for record in non_passing:
            detail = str(record.get("detail", "")).strip().splitlines()
            short_detail = detail[0] if detail else ""
            lines.append(
                f"- `{record['status']}` `{record['id']}` "
                f"({record['elapsed_ms']} ms) {short_detail}"
            )

    lines.extend(["", "## Slowest Tests", ""])
    for record in sorted(result.test_records, key=lambda item: item["elapsed_ms"], reverse=True)[:10]:
        lines.append(f"- `{record['elapsed_ms']} ms` `{record['status']}` `{record['id']}`")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path


def _coverage_requested(argv: list[str]) -> bool:
    """Return whether this test run should collect line coverage."""

    return "--coverage" in argv or os.environ.get("NJU_CODE_COVERAGE") == "1"


def _start_coverage(argv: list[str]) -> Any:
    """Start coverage.py when requested, returning the collector or None."""

    if not _coverage_requested(argv):
        return None
    try:
        import coverage  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "coverage is not installed; run `pip install coverage` or "
            "`pip install -r requirements.txt` first"
        ) from exc

    reports_dir = ROOT / ".nju_code" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    collector = coverage.Coverage(
        branch=True,
        data_file=str(reports_dir / f".coverage_{uuid4().hex}"),
        include=[str(ROOT / item) for item in CORE_COVERAGE_FILES],
        omit=[
            str(ROOT / ".nju_code" / "*"),
            str(ROOT / ".test_tmp" / "*"),
            str(ROOT / "test_all_features.py"),
            str(ROOT / "__pycache__" / "*"),
        ],
    )
    collector.start()
    return collector


def _write_coverage_report(
    collector: Any,
    finished_at: datetime,
    test_result: ReportingTextTestResult,
) -> tuple[Path, Path, Path]:
    """Persist JSON, Markdown, and HTML coverage reports."""

    reports_dir = ROOT / ".nju_code" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = finished_at.strftime("%Y%m%d_%H%M%S")
    stem = f"coverage_{timestamp}_{uuid4().hex[:8]}"
    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"
    html_dir = reports_dir / f"{stem}_html"

    text_report = io.StringIO()
    combined_percent = collector.report(
        file=text_report,
        show_missing=False,
        skip_empty=True,
        ignore_errors=True,
    )
    collector.json_report(outfile=str(json_path), pretty_print=True, ignore_errors=True)
    collector.html_report(directory=str(html_dir), ignore_errors=True)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    totals = payload.get("totals", {})
    statement_percent = float(totals.get("percent_statements_covered", combined_percent))
    branch_percent = float(totals.get("percent_branches_covered", 100.0))
    files = payload.get("files", {})
    low_files: list[tuple[float, str, dict[str, Any]]] = []
    for path, info in files.items():
        summary = info.get("summary", {})
        if summary.get("num_statements", 0) > 0:
            low_files.append((float(summary.get("percent_covered", 0.0)), path, summary))
    low_files.sort(key=lambda item: (item[0], item[1]))

    status = "PASS" if test_result.wasSuccessful() else "FAIL"
    lines = [
        f"# Coverage Report: statements {round(statement_percent, 2)}%, branches {round(branch_percent, 2)}%",
        "",
        f"- Workspace: `{ROOT}`",
        f"- Generated: `{finished_at.isoformat(timespec='seconds')}`",
        f"- Test status: `{status}`",
        f"- Scope: core regression modules (`{len(CORE_COVERAGE_FILES)}` files)",
        "- Target: statements >= 90%, branches >= 60%",
        f"- HTML report: `{html_dir.relative_to(ROOT).as_posix()}/index.html`",
        f"- JSON report: `{json_path.relative_to(ROOT).as_posix()}`",
        "",
        "## Totals",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Covered lines | {totals.get('covered_lines', 0)} |",
        f"| Missing lines | {totals.get('missing_lines', 0)} |",
        f"| Statements | {totals.get('num_statements', 0)} |",
        f"| Excluded lines | {totals.get('excluded_lines', 0)} |",
        f"| Covered branches | {totals.get('covered_branches', 0)} |",
        f"| Missing branches | {totals.get('missing_branches', 0)} |",
        f"| Branches | {totals.get('num_branches', 0)} |",
        f"| Statement coverage | {round(statement_percent, 2)}% |",
        f"| Branch coverage | {round(branch_percent, 2)}% |",
        f"| Combined coverage.py Cover | {round(float(totals.get('percent_covered', combined_percent)), 2)}% |",
        "",
        "## Lowest Covered Files",
        "",
        "| File | Coverage | Statements | Missing |",
        "|---|---:|---:|---:|",
    ]
    for percent, path, summary in low_files[:15]:
        lines.append(
            f"| `{path}` | {round(percent, 2)}% | "
            f"{summary.get('num_statements', 0)} | {summary.get('missing_lines', 0)} |"
        )

    lines.extend(["", "## Text Summary", "", "```text", text_report.getvalue().strip(), "```"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, json_path, html_dir


def _selected_test_names(argv: list[str]) -> list[str]:
    """Extract unittest name filters while ignoring simple runner flags."""

    runner_flags = {"--coverage"}
    return [arg for arg in argv if not arg.startswith("-") and arg not in runner_flags]


def run_tests_with_report(argv: list[str]) -> int:
    """Run unittest suite and always save a machine/human-readable report."""

    selected_tests = _selected_test_names(argv)
    loader = unittest.defaultTestLoader
    if selected_tests:
        suite = loader.loadTestsFromNames(selected_tests, module=sys.modules[__name__])
    else:
        suite = loader.loadTestsFromModule(sys.modules[__name__])

    verbosity = 1 if "-q" in argv or "--quiet" in argv else 2
    started_at = datetime.now()
    start_time = time.perf_counter()
    coverage_collector = _start_coverage(argv)
    runner = unittest.TextTestRunner(
        verbosity=verbosity,
        resultclass=ReportingTextTestResult,
    )
    result = runner.run(suite)
    finished_at = datetime.now()
    elapsed_seconds = time.perf_counter() - start_time
    if coverage_collector is not None:
        coverage_collector.stop()
        coverage_collector.save()

    md_path, json_path = _write_unittest_report(
        result,
        started_at,
        finished_at,
        elapsed_seconds,
        selected_tests,
    )
    print(f"\nTest report saved:")
    print(f"  Markdown: {md_path.relative_to(ROOT).as_posix()}")
    print(f"  JSON    : {json_path.relative_to(ROOT).as_posix()}")
    if coverage_collector is not None:
        cov_md, cov_json, cov_html = _write_coverage_report(
            coverage_collector,
            finished_at,
            result,
        )
        print("Coverage report saved:")
        print(f"  Markdown: {cov_md.relative_to(ROOT).as_posix()}")
        print(f"  JSON    : {cov_json.relative_to(ROOT).as_posix()}")
        print(f"  HTML    : {cov_html.relative_to(ROOT).as_posix()}/index.html")
    return 0 if result.wasSuccessful() else 1


class ProjectRepositoryTests(unittest.TestCase):
    """Repository-level smoke tests that do not instantiate app services."""

    # These checks fail early when files are moved or deleted by accident.
    # They are intentionally cheap and avoid importing most of the app.

    def test_required_repository_files_exist(self) -> None:
        required = [
            "main.py",
            "requirements.txt",
            "README.md",
            "njucode/app.py",
            "njucode/state.py",
            "njucode/services/code_analysis.py",
            "njucode/services/project_testing.py",
            "njucode/skills/builtin/__init__.py",
            "njucode/mcp/manager.py",
            "test_all_features.py",
        ]
        missing = [item for item in required if not (ROOT / item).exists()]
        self.assertEqual([], missing)

    def test_all_python_files_parse_with_ast(self) -> None:
        # AST parsing catches syntax errors without writing bytecode caches.
        bad: list[str] = []
        for path in ROOT.rglob("*.py"):
            if "__pycache__" in path.parts or ".git" in path.parts or ".test_tmp" in path.parts:
                continue
            try:
                ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
            except SyntaxError as exc:
                bad.append(f"{path.relative_to(ROOT)}:{exc.lineno}:{exc.msg}")
        self.assertEqual([], bad)

    def test_requirements_include_runtime_dependencies(self) -> None:
        # The runtime stack should stay visible in requirements.txt.
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for package in ["textual", "rich", "pydantic", "python-dotenv", "openai", "mcp"]:
            self.assertIn(package, requirements)

    def test_main_imports_textual_application(self) -> None:
        # Importing the entrypoint is a lightweight proxy for python main.py.
        import main
        from njucode.app import NjuCodeApp

        self.assertIs(main.NjuCodeApp, NjuCodeApp)
        self.assertIn("NJU", NjuCodeApp.TITLE)


class CodeAnalyzerTests(unittest.TestCase):
    """Behavior tests for the local code analysis engine."""

    # The analyzer is the backbone for /scan, /search, /symbol, /deps,
    # /recall, and /impact.  The fixture package below gives it real imports,
    # symbols, and text to index.

    def setUp(self) -> None:
        # Build a tiny Python package with imports and symbols so every analyzer
        # path can be exercised without relying on the real project layout.
        self.tmp = make_workspace()
        self.root = Path(self.tmp.name)
        write(
            self.root / "main.py",
            "from pkg.alpha import Alpha\n\n"
            "def run():\n"
            "    return Alpha().value()\n\n"
            "if __name__ == '__main__':\n"
            "    print(run())\n",
        )
        write(
            self.root / "pkg" / "__init__.py",
            "",
        )
        write(
            self.root / "pkg" / "alpha.py",
            "import json\n"
            "from pkg.beta import helper\n\n"
            "class Alpha:\n"
            "    def value(self):\n"
            "        return helper() + 1\n",
        )
        write(
            self.root / "pkg" / "beta.py",
            "def helper():\n"
            "    return 41\n",
        )
        write(self.root / "README.md", "Alpha helper project\n")
        from njucode.services.code_analysis import CodeAnalyzer

        self.analyzer = CodeAnalyzer(self.root)

    def tearDown(self) -> None:
        # Clean up the synthetic package after each analyzer test.
        self.tmp.cleanup()

    def test_scan_project_counts_files_and_suffixes(self) -> None:
        # /scan should return high-level file and suffix statistics.
        result = self.analyzer.scan_project()
        self.assertEqual("scan", result["type"])
        self.assertGreaterEqual(result["summary"]["file_count"], 4)
        self.assertIn(".py", result["summary"]["suffix_counts"])

    def test_search_text_finds_keyword(self) -> None:
        # Keyword search should report file paths and snippets.
        result = self.analyzer.search_text("helper")
        self.assertEqual("text_search", result["type"])
        self.assertGreaterEqual(result["hit_count"], 2)
        paths = {hit["path"] for hit in result["hits"]}
        self.assertIn("pkg/alpha.py", paths)

    def test_search_text_supports_case_sensitive(self) -> None:
        # Case-sensitive search should be narrower than insensitive search.
        insensitive = self.analyzer.search_text("alpha")
        sensitive = self.analyzer.search_text("alpha", case_sensitive=True)
        self.assertGreater(insensitive["hit_count"], sensitive["hit_count"])

    def test_search_text_supports_regex(self) -> None:
        # Regex search is used by the Tools panel when --regex is supplied.
        result = self.analyzer.search_text(r"def\s+helper", use_regex=True)
        self.assertEqual(1, result["hit_count"])
        self.assertEqual("pkg/beta.py", result["hits"][0]["path"])

    def test_symbol_search_finds_classes_and_functions(self) -> None:
        # Symbol search should distinguish classes and function definitions.
        class_result = self.analyzer.symbol_search("Alpha")
        function_result = self.analyzer.symbol_search("helper")
        self.assertEqual(1, class_result["hit_count"])
        self.assertEqual("class", class_result["hits"][0]["kind"])
        self.assertEqual(1, function_result["hit_count"])
        self.assertEqual("def", function_result["hits"][0]["kind"])

    def test_summarize_file_extracts_python_metadata(self) -> None:
        # File summary should expose classes, functions, imports, and entry flag.
        result = self.analyzer.summarize_file("pkg/alpha.py")
        self.assertEqual("file_summary", result["type"])
        self.assertIn("Alpha", result["main_classes"])
        self.assertIn("value", result["main_functions"])
        self.assertIn("json", result["external_dependencies"])

    def test_dependency_graph_contains_import_edges(self) -> None:
        # The dependency graph should translate Python imports into file edges.
        graph = self.analyzer.build_dependency_graph()
        self.assertEqual("dependency_graph", graph["type"])
        self.assertIn("pkg/alpha.py", graph["forward"])
        self.assertIn("pkg/beta.py", graph["forward"]["pkg/alpha.py"])

    def test_neighbors_reports_dependencies(self) -> None:
        # /deps is backed by neighbor lookup over the dependency graph.
        result = self.analyzer.neighbors("pkg/alpha.py", depth=1)
        self.assertEqual("neighbors", result["type"])
        self.assertIn("pkg/beta.py", result["depends_on"].get("1", []))

    def test_recall_files_ranks_relevant_files(self) -> None:
        # Recall is intentionally fuzzy; assert the relevant file is included.
        result = self.analyzer.recall_files("Alpha helper", top_k=2)
        self.assertEqual("recall", result["type"])
        self.assertLessEqual(len(result["results"]), 2)
        self.assertTrue(any(item["path"] == "pkg/alpha.py" for item in result["results"]))

    def test_impact_analysis_for_symbol(self) -> None:
        # Impact analysis should resolve a symbol to its defining file.
        result = self.analyzer.impact_analysis("helper", depth=1)
        self.assertEqual("impact", result["type"])
        self.assertIn("pkg/beta.py", result["target_path"])

    def test_run_command_dispatches_known_commands(self) -> None:
        # Slash-command dispatch is tested end-to-end against analyzer methods.
        self.assertEqual("help", self.analyzer.run_command("/help")["type"])
        self.assertEqual("scan", self.analyzer.run_command("/scan")["type"])
        self.assertEqual("text_search", self.analyzer.run_command("/search helper")["type"])
        self.assertEqual("symbol_search", self.analyzer.run_command("/symbol Alpha")["type"])
        self.assertEqual("file_summary", self.analyzer.run_command("/summary pkg/alpha.py")["type"])
        self.assertEqual("neighbors", self.analyzer.run_command("/deps pkg/alpha.py --depth 1")["type"])
        self.assertEqual("recall", self.analyzer.run_command("/recall helper --top 2")["type"])
        self.assertEqual("impact", self.analyzer.run_command("/impact Alpha --depth 1")["type"])
        self.assertEqual("task_index", self.analyzer.run_command("/tasks --top 5")["type"])
        self.assertEqual("code_metrics", self.analyzer.run_command("/metrics --top 5")["type"])

    def test_run_command_rejects_unknown_command(self) -> None:
        # Unknown commands should return structured errors, not exceptions.
        result = self.analyzer.run_command("/missing")
        self.assertEqual("error", result["type"])
        self.assertEqual("unknown_command", result["error"])
        self.assertEqual("unknown_command", self.analyzer.run_command("/todos")["error"])
        self.assertEqual("unknown_command", self.analyzer.run_command("/test-all")["error"])

    def test_to_text_formats_core_payloads(self) -> None:
        # TUI chat output uses to_text() for human-readable rendering.
        scan_text = self.analyzer.to_text(self.analyzer.scan_project())
        search_text = self.analyzer.to_text(self.analyzer.search_text("helper"))
        self.assertIsInstance(scan_text, str)
        self.assertIsInstance(search_text, str)
        self.assertGreater(len(scan_text), 10)
        self.assertGreater(len(search_text), 10)

    def test_code_analyzer_edge_paths_and_project_report_text(self) -> None:
        # Cover command parser fallbacks and display paths that are easy to miss
        # in happy-path command tests.
        from njucode.services.project_testing import ProjectCheckResult, ProjectTestReport

        self.analyzer.set_workspace_root(self.root)
        self.assertEqual("", self.analyzer._line_context("", 1))
        self.assertEqual("tiny", self.analyzer._shorten("tiny", max_len=20))
        self.assertTrue(self.analyzer._shorten("x" * 40, max_len=10).endswith("..."))
        self.assertEqual("error", self.analyzer.run_command("   ")["type"])
        self.assertEqual("scan", self.analyzer.run_command('/scan "unterminated')["type"])

        missing_impact = self.analyzer.impact_analysis("MissingSymbol")
        self.assertEqual("target_not_found", missing_impact["error"])

        report = ProjectTestReport(
            workspace=str(self.root),
            generated_at="2026-06-03T00:00:00",
            results=[ProjectCheckResult("layout", "pass", "ok")],
        )
        payload = report.to_dict()
        payload["text"] = "[Project Doctor] PASS"
        self.assertIn("[Project Doctor]", self.analyzer.to_text(payload))


class CodeMetricsTests(unittest.TestCase):
    """Tests for static complexity, dependency, and hotspot analysis."""

    # Metrics should combine AST complexity and dependency shape, giving the
    # project a higher-level maintenance signal than raw file search.

    def setUp(self) -> None:
        self.tmp = make_workspace()
        self.root = Path(self.tmp.name)
        write(self.root / "pkg" / "__init__.py", "")
        write(
            self.root / "pkg" / "a.py",
            "\n".join(
                [
                    "from pkg import b",
                    "",
                    "def complex_value(items):",
                    "    total = 0",
                    "    for item in items:",
                    "        if item > 10 and item % 2 == 0:",
                    "            total += item",
                    "        elif item < 0:",
                    "            total -= item",
                    "        else:",
                    "            try:",
                    "                total += int(item)",
                    "            except Exception:",
                    "                total += 0",
                    "    return total",
                ]
            )
            + "\n",
        )
        write(
            self.root / "pkg" / "b.py",
            "from pkg import a\n\nclass Box:\n    def value(self):\n        return a.complex_value([1, 2, 3])\n",
        )
        write(self.root / "test_metrics.py", "def test_helper():\n    return 1\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_metrics_detect_complexity_and_cycles(self) -> None:
        # a.py and b.py import each other, so Tarjan SCC detection should report a cycle.
        from njucode.services.code_metrics import ProjectMetricsAnalyzer

        result = ProjectMetricsAnalyzer(self.root).analyze(top_n=5)
        self.assertEqual("code_metrics", result["type"])
        self.assertEqual(3, result["summary"]["python_files"])
        self.assertEqual(1, result["summary"]["cycles"])
        self.assertTrue(result["complex_functions"])
        self.assertEqual("pkg/a.py", result["hotspots"][0]["path"])
        self.assertGreaterEqual(result["hotspots"][0]["hotspot_score"], result["hotspots"][-1]["hotspot_score"])
        self.assertFalse(any(item["path"] == "test_metrics.py" for item in result["hotspots"]))

    def test_metrics_include_tests_option(self) -> None:
        # Test files are hidden by default but available for explicit audits.
        from njucode.services.code_metrics import ProjectMetricsAnalyzer

        result = ProjectMetricsAnalyzer(self.root).analyze(top_n=10, include_tests=True)
        paths = {item["path"] for item in result["hotspots"]}
        self.assertIn("test_metrics.py", paths)

        scoped = ProjectMetricsAnalyzer(self.root).analyze(top_n=10, path_filter="pkg/a.py")
        self.assertEqual(["pkg/a.py"], [item["path"] for item in scoped["hotspots"]])

    def test_metrics_counts_modern_control_flow(self) -> None:
        # This fixture exercises async defs, match/case, comprehensions, ternary
        # expressions, and try/else/finally so the complexity counter's branch
        # accounting is covered beyond the simple for/if fixture above.
        write(
            self.root / "pkg" / "modern.py",
            "\n".join(
                [
                    "async def async_value(items):",
                    "    return [item async for item in items]",
                    "",
                    "def classify(value, items):",
                    "    nested = lambda x: x + 1",
                    "    def helper(flag):",
                    "        return 1 if flag else 0",
                    "    match value:",
                    "        case 0:",
                    "            result = helper(False)",
                    "        case 1 | 2:",
                    "            result = helper(True)",
                    "        case _:",
                    "            result = sum(x for x in items if x > 0)",
                    "    try:",
                    "        parsed = int(value)",
                    "    except ValueError:",
                    "        parsed = 0",
                    "    else:",
                    "        parsed += 1",
                    "    finally:",
                    "        parsed += nested(0)",
                    "    return result + parsed",
                ]
            )
            + "\n",
        )
        from njucode.services.code_metrics import ProjectMetricsAnalyzer

        result = ProjectMetricsAnalyzer(self.root).analyze(top_n=10, path_filter="modern.py")
        names = {item["name"]: item for item in result["complex_functions"]}
        self.assertIn("async_value", names)
        self.assertIn("classify", names)
        self.assertGreaterEqual(names["classify"]["complexity"], 8)

    def test_code_analyzer_metrics_command_and_text_output(self) -> None:
        # Slash command output should include hotspot and complex-function sections.
        from njucode.services.code_analysis import CodeAnalyzer

        analyzer = CodeAnalyzer(self.root)
        result = analyzer.run_command("/metrics --path pkg --top 3")
        text = analyzer.to_text(result)
        self.assertEqual("code_metrics", result["type"])
        self.assertIn("path=pkg", text)
        self.assertIn("Hotspots", text)
        self.assertIn("Most Complex Functions", text)

    def test_builtin_metrics_skill_executes(self) -> None:
        # The Skills layer exposes metrics as a read-only project analysis tool.
        from njucode.services.code_analysis import CodeAnalyzer
        from njucode.skills.builtin import execute_builtin_skill

        result = execute_builtin_skill(
            "builtin.metrics",
            CodeAnalyzer(self.root),
            {"top_n": 5, "include_tests": False, "path": "pkg"},
        )
        self.assertEqual("code_metrics", result["type"])
        self.assertGreaterEqual(result["summary"]["dependency_edges"], 2)


class ProjectTaskIndexTests(unittest.TestCase):
    """Tests for the TODO/checklist task scanner feature."""

    # The task scanner is a user-facing development aid, not a test runner.
    # These cases keep marker parsing, filtering, and command output aligned.

    def setUp(self) -> None:
        self.tmp = make_workspace()
        self.root = Path(self.tmp.name)
        write(
            self.root / "pkg" / "work.py",
            "\n".join(
                [
                    'message = "TODO: this string is documentation, not a task"',
                    "# TODO(alice): wire the navigation cache",
                    "# FIXME: handle empty project roots",
                    "# a bug can appear in prose without becoming a task",
                    "# BUG(bob): stale preview after rollback",
                    "value = 42",
                ]
            )
            + "\n",
        )
        write(
            self.root / "notes.md",
            "\n".join(
                [
                    "- [ ] update README screenshot",
                    "- [x] archive previous demo notes",
                    "/tasks [--tag TODO|FIXME|BUG|HACK|NOTE|CHECKBOX]",
                    "This paragraph mentions bug triage without a task marker.",
                    "TODO: markdown standalone task",
                    "Plain text without a task",
                ]
            )
            + "\n",
        )
        write(self.root / ".nju_code" / "ignored.md", "- [ ] internal runtime state\n")
        write(self.root / "test_noise.py", "# TODO: sample task used only by tests\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_task_index_scans_markers_and_checkboxes(self) -> None:
        # Open markers and unfinished checkboxes should appear by default.
        from njucode.services.task_index import ProjectTaskIndex

        result = ProjectTaskIndex(self.root).scan()
        self.assertEqual("task_index", result["type"])
        self.assertEqual(5, result["summary"]["open"])
        self.assertEqual(0, result["summary"]["done"])
        tags = {item["tag"] for item in result["items"]}
        self.assertIn("TODO", tags)
        self.assertIn("FIXME", tags)
        self.assertIn("BUG", tags)
        self.assertIn("CHECKBOX", tags)
        self.assertFalse(any(item["path"].startswith(".nju_code") for item in result["items"]))
        self.assertFalse(any(item["path"] == "test_noise.py" for item in result["items"]))
        self.assertFalse(any("documentation, not a task" in item["text"] for item in result["items"]))
        filtered = ProjectTaskIndex(self.root).scan(path_filter="pkg/")
        self.assertTrue(all(item["path"].startswith("pkg/") for item in filtered["items"]))

    def test_task_index_filters_by_tag_owner_and_done_state(self) -> None:
        # Filters make the scanner useful for focused daily work lists.
        from njucode.services.task_index import ProjectTaskIndex

        scanner = ProjectTaskIndex(self.root)
        todo = scanner.scan(tag="TODO", owner="alice")
        self.assertEqual(1, todo["summary"]["total"])
        self.assertEqual("alice", todo["items"][0]["owner"])

        with_done = scanner.scan(tag="CHECKBOX", include_done=True)
        self.assertEqual(2, with_done["summary"]["total"])
        self.assertEqual(1, with_done["summary"]["done"])

        with_tests = scanner.scan(tag="TODO", include_tests=True)
        self.assertTrue(any(item["path"] == "test_noise.py" for item in with_tests["items"]))

    def test_task_index_handles_text_and_unreadable_edge_cases(self) -> None:
        # Non-Python files use comment-prefix parsing, while malformed Python
        # files should fail soft instead of crashing the scanner.
        from njucode.services.task_index import ProjectTaskIndex

        write(self.root / "plain.txt", "# HACK: plain text comment task\nBUG: no prefix ignored\n")
        write(self.root / "broken.py", '"""unterminated\n# TODO: tokenizer never reaches this\n')
        write(self.root / "binary.bin", "# TODO: unsupported suffix ignored\n")
        write(self.root / "huge.txt", "# TODO: too large ignored\n" + ("x" * 200))

        scanner = ProjectTaskIndex(self.root, max_file_bytes=100)
        result = scanner.scan(include_tests=True)
        items = {(item["path"], item["tag"], item["text"]) for item in result["items"]}
        self.assertIn(("plain.txt", "HACK", "plain text comment task"), items)
        self.assertFalse(any(item[0] == "broken.py" for item in items))
        self.assertFalse(any(item[0] == "binary.bin" for item in items))
        self.assertFalse(any(item[0] == "huge.txt" for item in items))

    def test_code_analyzer_tasks_command_and_text_output(self) -> None:
        # Slash commands and chat rendering share the same payload shape.
        from njucode.services.code_analysis import CodeAnalyzer

        analyzer = CodeAnalyzer(self.root)
        result = analyzer.run_command("/tasks --tag FIXME --path pkg --top 10")
        text = analyzer.to_text(result)
        self.assertEqual("task_index", result["type"])
        self.assertEqual(1, result["summary"]["total"])
        self.assertEqual(10, result["filters"]["limit"])
        self.assertIn("include_tests=False", text)
        self.assertIn("path=pkg", text)
        self.assertIn("showing=1/1", text)
        self.assertIn("FIXME", text)
        self.assertIn("pkg/work.py", text)

        limited = analyzer.run_command("/tasks --path pkg --top 1")
        limited_text = analyzer.to_text(limited)
        self.assertEqual(1, limited["filters"]["limit"])
        self.assertEqual(1, len(limited["items"]))
        self.assertIn("showing=1/3", limited_text)

    def test_builtin_tasks_skill_executes(self) -> None:
        # The Skills layer exposes the same scanner for command registry users.
        from njucode.services.code_analysis import CodeAnalyzer
        from njucode.skills.builtin import execute_builtin_skill

        result = execute_builtin_skill(
            "builtin.tasks",
            CodeAnalyzer(self.root),
            {"tag": "BUG", "limit": 10, "include_tests": False},
        )
        self.assertEqual("task_index", result["type"])
        self.assertEqual(1, result["summary"]["total"])
        self.assertEqual("BUG", result["items"][0]["tag"])


class CodeExtractorTests(unittest.TestCase):
    """Tests for parsing LLM markdown code blocks into patch candidates."""

    # These cases protect the bridge from model replies to Patch tasks.
    # The parser must keep source blocks but ignore command examples.

    def test_extracts_filename_from_info_string(self) -> None:
        # Common model style: language followed by workspace-relative path.
        from njucode.services.code_extractor import extract_code_blocks

        blocks = extract_code_blocks("```python frontend/app.py\nprint('x')\n```")
        self.assertEqual(1, len(blocks))
        self.assertEqual("python", blocks[0].language)
        self.assertEqual("frontend/app.py", blocks[0].filename)

    def test_extracts_filename_from_colon_form(self) -> None:
        # Explicit language:path form should allow bare filenames.
        from njucode.services.code_extractor import extract_code_blocks

        blocks = extract_code_blocks("```python:main.py\nprint('x')\n```")
        self.assertEqual("main.py", blocks[0].filename)

    def test_extracts_filename_when_info_is_path(self) -> None:
        # A path-only fence should infer language from extension.
        from njucode.services.code_extractor import extract_code_blocks

        blocks = extract_code_blocks("```frontend/app.py\nprint('x')\n```")
        self.assertEqual("python", blocks[0].language)
        self.assertEqual("frontend/app.py", blocks[0].filename)

    def test_filters_shell_blocks(self) -> None:
        # Shell snippets are usage examples, not patchable source files.
        from njucode.services.code_extractor import extract_code_blocks

        blocks = extract_code_blocks("```bash\npython main.py\n```\n```python\nx = 1\n```")
        self.assertEqual(1, len(blocks))
        self.assertEqual("python", blocks[0].language)

    def test_splits_multifile_blocks(self) -> None:
        # LLMs often return several files in one code fence with boundary comments.
        from njucode.services.code_extractor import extract_code_blocks

        text = (
            "```python\n"
            "# frontend/a.py\n"
            "A = 1\n"
            "# frontend/b.py\n"
            "B = 2\n"
            "```\n"
        )
        blocks = extract_code_blocks(text)
        self.assertEqual(["frontend/a.py", "frontend/b.py"], [b.filename for b in blocks])

    def test_does_not_split_single_file_boundary(self) -> None:
        # One boundary comment is not enough to prove a multi-file block.
        from njucode.services.code_extractor import extract_code_blocks

        text = "```python\n# frontend/a.py\nA = 1\n```"
        blocks = extract_code_blocks(text)
        self.assertEqual(1, len(blocks))
        self.assertIsNone(blocks[0].filename)


class ContextCompressorTests(unittest.TestCase):
    """Tests for token estimation, compression, and compression statistics."""

    # Compression tests use FakeModelClient to avoid external API access.
    # They focus on deterministic token/count behavior and summary bookkeeping.

    def test_static_token_estimate_handles_cjk_and_ascii(self) -> None:
        # Bilingual token estimation is important because the project docs are Chinese.
        from njucode.services.context_compressor import ContextCompressor

        self.assertEqual(0, ContextCompressor.estimate_text_tokens_static(""))
        ascii_tokens = ContextCompressor.estimate_text_tokens_static("abcd" * 10)
        cjk_tokens = ContextCompressor.estimate_text_tokens_static("南京大学" * 10)
        self.assertGreater(ascii_tokens, 0)
        self.assertGreater(cjk_tokens, 0)

    def test_message_token_estimate_includes_overhead(self) -> None:
        # Message wrappers add role/boundary overhead beyond raw text tokens.
        from njucode.services.context_compressor import ContextCompressor

        content_only = ContextCompressor.estimate_text_tokens_static("hello world")
        message_estimate = ContextCompressor.estimate_message_tokens_from_content("hello world")
        self.assertGreater(message_estimate, content_only)

    def test_compress_produces_summary_and_keeps_recent_messages(self) -> None:
        # Compression should summarize older messages and keep the latest context.
        from njucode.models import ChatMessage, ModelConfig
        from njucode.services.context_compressor import ContextCompressor

        messages = [
            ChatMessage("user", "first question " * 20),
            ChatMessage("assistant", "first answer " * 20),
            ChatMessage("user", "second question " * 20),
            ChatMessage("assistant", "second answer " * 20),
            ChatMessage("user", "latest question"),
        ]
        compressor = ContextCompressor(
            FakeModelClient(),
            ModelConfig(),
            token_threshold=10,
            keep_recent=2,
            min_messages_to_compress=1,
        )
        result = compressor.compress(messages, session_title="unit")
        self.assertTrue(result.summary)
        self.assertLessEqual(len(result.kept_messages), len(messages))
        self.assertGreaterEqual(result.token_before, result.token_after)

    def test_compression_history_tracks_savings(self) -> None:
        # The Session panel and doctor report rely on compression statistics.
        from njucode.models import ChatMessage, ModelConfig
        from njucode.services.context_compressor import ContextCompressor

        compressor = ContextCompressor(
            FakeModelClient(),
            ModelConfig(),
            token_threshold=10,
            keep_recent=2,
            min_messages_to_compress=1,
        )
        compressor.compress(
            [
                ChatMessage("user", "a" * 100),
                ChatMessage("assistant", "b" * 100),
                ChatMessage("user", "c" * 100),
                ChatMessage("assistant", "d" * 100),
            ],
            session_title="history",
        )
        self.assertEqual(1, compressor.get_compression_count())
        self.assertGreaterEqual(compressor.get_total_tokens_saved(), 0)
        self.assertIn("token", compressor.format_compression_stats())

    def test_context_compressor_validation_prompt_and_fallback_paths(self) -> None:
        # Small helper branches affect whether compression is trusted or degraded.
        from njucode.models import ChatMessage, ModelConfig
        from njucode.services.context_compressor import ContextCompressor

        compressor = ContextCompressor(FakeModelClient(), ModelConfig(), token_threshold=0)
        messages = [
            ChatMessage("user", "请修改配置文件" + "x" * 1100),
            ChatMessage("assistant", "可以，我会保留关键上下文"),
        ]

        self.assertEqual(0.0, compressor.get_token_usage_ratio(messages))
        self.assertEqual(compressor.keep_recent, compressor._compute_adaptive_keep_recent([]))
        self.assertFalse(compressor._validate_summary(""))
        self.assertFalse(compressor._validate_summary("too short"))
        self.assertTrue(compressor._validate_summary("【用户意图】" + "用户希望继续维护项目。" * 4))

        prompt = compressor._build_summary_prompt(
            messages,
            existing_summary="旧摘要\n保留结论",
            session_title="Coverage Work",
        )
        self.assertIn("Coverage Work", prompt)
        self.assertIn("已有历史摘要", prompt)
        self.assertIn("已截断", prompt)
        self.assertIn("[用户]", prompt)
        self.assertIn("[助手]", prompt)

        fallback = compressor._build_fallback_summary(messages, existing_summary="old\nsummary")
        self.assertIn("已有摘要片段", fallback)
        self.assertIn("关键结论", fallback)

        summary, used_fallback = compressor.generate_summary(messages, existing_summary="old")
        self.assertTrue(used_fallback)
        self.assertIn("自动降级摘要", summary)
        self.assertIn("尚未发生", compressor.format_compression_stats())

    def test_context_compressor_generate_summary_retry_and_error_paths(self) -> None:
        # Summary generation accepts valid model output, retries weak output,
        # accepts long-but-unstructured output at the retry limit, and degrades
        # on explicit model errors.
        from njucode.models import ChatMessage, ModelConfig
        from njucode.services.context_compressor import ContextCompressor

        messages = [ChatMessage("user", "需要压缩历史"), ChatMessage("assistant", "好的")]
        valid = "【关键结论】" + "已经总结出可继续对话的上下文。" * 8
        client = FakeModelClient(valid)
        compressor = ContextCompressor(client, ModelConfig(api_key="key"), max_summary_retries=1)
        summary, used_fallback = compressor.generate_summary(messages, session_title="Chat 1")
        self.assertFalse(used_fallback)
        self.assertEqual(valid, summary)
        self.assertEqual(1, len(client.requests))

        retry_client = FakeModelClient("short")
        retry_compressor = ContextCompressor(retry_client, ModelConfig(api_key="key"), max_summary_retries=1)
        summary, used_fallback = retry_compressor.generate_summary(messages)
        self.assertTrue(used_fallback)
        self.assertIn("自动降级摘要", summary)
        self.assertEqual(2, len(retry_client.requests))

        class ErrorClient:
            def chat(self, request: Any) -> str:
                return "[系统错误] boom"

        error_compressor = ContextCompressor(ErrorClient(), ModelConfig(api_key="key"))
        summary, used_fallback = error_compressor.generate_summary(messages)
        self.assertTrue(used_fallback)
        self.assertIn("自动降级摘要", summary)

    def test_context_compressor_short_history_and_record_formatting(self) -> None:
        # Short histories should be returned unchanged; CompressionRecord should
        # format zero-token and fallback metadata safely.
        from datetime import datetime

        from njucode.models import ChatMessage, ModelConfig
        from njucode.services.context_compressor import CompressionRecord, ContextCompressor

        compressor = ContextCompressor(
            FakeModelClient(),
            ModelConfig(),
            min_messages_to_compress=5,
        )
        messages = [ChatMessage("user", "hello")]
        result = compressor.compress(messages, existing_summary="existing")
        self.assertEqual("existing", result.summary)
        self.assertEqual(messages, result.kept_messages)
        self.assertEqual(0, result.removed_count)
        self.assertEqual(0, compressor.get_compression_count())

        record = CompressionRecord(
            compressed_at=datetime(2026, 6, 3, 15, 0, 0),
            messages_removed=0,
            token_before=0,
            token_after=0,
            tokens_saved=0,
            summary_length=0,
            used_fallback=True,
            session_title="demo",
        )
        text = record.format_summary_line()
        self.assertIn("0%", text)
        self.assertIn("降级", text)


class SettingsStoreTests(unittest.TestCase):
    """Tests for local settings persistence and session import/export."""

    # SettingsStore is the persistence boundary for state that survives app
    # restarts, so roundtrip and validation behavior must be stable.

    def test_settings_roundtrip_and_backup(self) -> None:
        # Saving twice should create a backup of the previous settings file.
        from njucode.services.settings_store import SettingsStore

        with make_workspace() as tmp:
            root = Path(tmp)
            store = SettingsStore(root)
            first = {"model": {"base_url": "one", "api_key": ""}, "sessions": []}
            second = {"model": {"base_url": "two", "api_key": ""}, "sessions": []}
            store.save(first)
            self.assertEqual(first, store.load())
            store.save(second)
            self.assertTrue(store.has_backup())
            self.assertEqual(first, store.restore_from_backup())

    def test_session_export_import_validation(self) -> None:
        # Exported sessions must be importable by the same schema validator.
        from njucode.services.settings_store import SettingsStore

        with make_workspace() as tmp:
            root = Path(tmp)
            store = SettingsStore(root)
            payload = {
                "session_id": "abc",
                "title": "Session",
                "messages": [{"role": "user", "content": "hello"}],
            }
            path = root / ".nju_code" / "exports" / "session_abc.json"
            store.export_session_file(payload, path)
            self.assertEqual(payload, store.import_session_file(path))
            self.assertEqual("Session", store._read_export_title(path))

    def test_session_import_rejects_bad_shape(self) -> None:
        # Bad imports should fail loudly instead of corrupting AppState.
        from njucode.services.settings_store import SettingsStore

        with make_workspace() as tmp:
            root = Path(tmp)
            store = SettingsStore(root)
            path = root / "bad.json"
            write(path, '{"messages": []}')
            with self.assertRaises(ValueError):
                store.import_session_file(path)

    def test_export_listing_and_cleanup(self) -> None:
        # Export cleanup keeps the .nju_code/exports directory from growing forever.
        from njucode.services.settings_store import SettingsStore

        # The cleanup method physically deletes files; skip only when the
        # current sandbox blocks unlink while still allowing normal writes.
        if not workspace_allows_unlink():
            self.skipTest("current sandbox denies file unlink operations")

        with make_workspace() as tmp:
            root = Path(tmp)
            store = SettingsStore(root)
            for i in range(3):
                payload = {
                    "session_id": f"s{i}",
                    "title": f"Session {i}",
                    "messages": [{"role": "user", "content": str(i)}],
                }
                store.export_session_file(payload, root / ".nju_code" / "exports" / f"session_s{i}.json")
            self.assertEqual(3, len(store.list_export_files()))
            self.assertGreater(store.get_exports_dir_size_bytes(), 0)
            deleted = store.cleanup_old_exports(keep_count=1)
            self.assertEqual(2, deleted)


class PatchEngineTests(unittest.TestCase):
    """Tests for patch creation, validation, persistence, apply, and rollback."""

    # Patch tests model the WBS-4 safety flow: generate, preview, validate,
    # apply, audit, and rollback.  Destructive tests are skipped if the sandbox
    # denies unlink operations.

    def test_patch_operation_generates_diff_and_validates_syntax(self) -> None:
        # Single-operation diff generation is used by PatchPanel previews.
        from njucode.services.patch_engine import PatchOperation

        op = PatchOperation("demo.py", "x = 1\n", "x = 2\n")
        diff = op.generate_diff()
        self.assertIn("-x = 1", diff)
        self.assertIn("+x = 2", diff)
        self.assertEqual((True, ""), op.validate_syntax())

        bad = PatchOperation("bad.py", "", "def broken(:\n")
        ok, message = bad.validate_syntax()
        self.assertFalse(ok)
        self.assertIn("Syntax error", message)

    def test_patch_task_serialization_roundtrip(self) -> None:
        # Patch history persists task dictionaries and reloads them later.
        from njucode.services.patch_engine import PatchOperation, PatchTask

        task = PatchTask(description="roundtrip", operations=[PatchOperation("a.py", "", "x = 1\n")])
        clone = PatchTask.from_dict(task.to_dict())
        self.assertEqual(task.task_id, clone.task_id)
        self.assertEqual(task.description, clone.description)
        self.assertEqual(["a.py"], clone.files_affected)

    def test_patch_history_store_persists_tasks(self) -> None:
        # History persistence is checked without applying a patch.
        from njucode.services.patch_engine import PatchEngine, PatchHistoryStore

        with make_workspace() as tmp:
            root = Path(tmp)
            store = PatchHistoryStore(root)
            engine = PatchEngine(root, store, DummyAuditLogger())
            task = engine.generate_patch({"created.py": ("", "x = 1\n")}, description="create")
            new_store = PatchHistoryStore(root)
            new_store.load()
            self.assertIsNotNone(new_store.get_task(task.task_id))

    def test_patch_engine_apply_and_rollback_modify(self) -> None:
        # Modify rollback restores from backup without deleting files.
        from njucode.services.patch_engine import PatchEngine, PatchHistoryStore, PatchStatus

        with make_workspace() as tmp:
            root = Path(tmp)
            target = root / "demo.py"
            write(target, "value = 1\n")
            store = PatchHistoryStore(root)
            audit = DummyAuditLogger()
            engine = PatchEngine(root, store, audit)
            task = engine.generate_patch({"demo.py": ("value = 1\n", "value = 2\n")})
            self.assertIn("value = 2", engine.preview_patch(task))
            result = engine.apply_patch(task)
            self.assertTrue(result.success)
            self.assertEqual("value = 2\n", target.read_text(encoding="utf-8"))
            rollback = engine.rollback_patch(task.task_id)
            self.assertTrue(rollback.success)
            self.assertEqual("value = 1\n", target.read_text(encoding="utf-8"))
            self.assertEqual(PatchStatus.ROLLED_BACK, store.get_task(task.task_id).status)
            self.assertGreaterEqual(len(audit.logs), 2)

    def test_patch_engine_apply_and_rollback_create(self) -> None:
        # Create rollback removes the created file, so it depends on unlink permission.
        from njucode.services.patch_engine import PatchEngine, PatchHistoryStore

        # Rolling back a create removes the created file, which is not permitted
        # in some sandboxed runs.
        if not workspace_allows_unlink():
            self.skipTest("current sandbox denies file unlink operations")

        with make_workspace() as tmp:
            root = Path(tmp)
            engine = PatchEngine(root, PatchHistoryStore(root), DummyAuditLogger())
            task = engine.generate_patch({"new_file.py": ("", "created = True\n")})
            result = engine.apply_patch(task)
            self.assertTrue(result.success)
            self.assertTrue((root / "new_file.py").exists())
            rollback = engine.rollback_patch(task.task_id)
            self.assertTrue(rollback.success)
            self.assertFalse((root / "new_file.py").exists())

    def test_patch_engine_rejects_stale_content(self) -> None:
        # A patch must not overwrite a file changed after patch generation.
        from njucode.services.patch_engine import PatchEngine, PatchHistoryStore

        with make_workspace() as tmp:
            root = Path(tmp)
            target = root / "demo.py"
            write(target, "value = 1\n")
            engine = PatchEngine(root, PatchHistoryStore(root), DummyAuditLogger())
            task = engine.generate_patch({"demo.py": ("value = 1\n", "value = 2\n")})
            write(target, "value = 3\n")
            ok, reason = engine.validate_patch(task)
            self.assertFalse(ok)
            self.assertIn("modified since", reason)

    def test_patch_engine_rejects_path_traversal(self) -> None:
        # Path traversal is blocked before any file write can occur.
        from njucode.services.patch_engine import PatchEngine, PatchHistoryStore

        with make_workspace() as tmp:
            root = Path(tmp)
            engine = PatchEngine(root, PatchHistoryStore(root), DummyAuditLogger())
            task = engine.generate_patch({"../escape.py": ("", "x = 1\n")})
            ok, reason = engine.validate_patch(task)
            self.assertFalse(ok)
            self.assertIn("escapes workspace", reason)


class SkillSystemTests(unittest.TestCase):
    """Tests for builtin skill metadata, command dispatch, and permissions."""

    # Skills connect local capabilities to slash commands and UI toggles.
    # These tests catch metadata drift as new builtin skills are added.

    def test_builtin_manifests_have_unique_ids_and_aliases(self) -> None:
        # Duplicate aliases would make command dispatch ambiguous.
        from njucode.skills.builtin import BUILTIN_AGENT_MANIFESTS, BUILTIN_MANIFESTS

        ids = [manifest.skill_id for manifest in [*BUILTIN_MANIFESTS, *BUILTIN_AGENT_MANIFESTS]]
        aliases = [alias for manifest in BUILTIN_MANIFESTS for alias in manifest.command_aliases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(aliases), len(set(aliases)))
        self.assertIn("/tasks", aliases)
        self.assertIn("/metrics", aliases)
        self.assertIn("/doctor", aliases)

    def test_skill_registry_registers_and_finds_commands(self) -> None:
        # Registry lookup powers execute_by_command().
        from njucode.skills.builtin import BUILTIN_MANIFESTS
        from njucode.skills.registry import SkillRegistry

        with make_workspace() as tmp:
            registry = SkillRegistry(Path(tmp))
            for manifest in BUILTIN_MANIFESTS:
                registry.register_skill(manifest)
            self.assertIsNotNone(registry.get_skill_by_command("/scan"))
            self.assertIsNotNone(registry.get_skill_by_command("/tasks --tag TODO"))
            self.assertIsNotNone(registry.get_skill_by_command("/metrics --top 5"))
            self.assertIsNotNone(registry.get_skill_by_command("/doctor --verbose"))
            self.assertIsNone(registry.get_skill_by_command("/not-real"))

    def test_skill_command_parser_maps_top_to_manifest_specific_parameter(self) -> None:
        # /tasks uses limit while /metrics uses top_n; both should accept --top.
        from njucode.models import DEFAULT_TOOLS
        from njucode.services.code_analysis import CodeAnalyzer
        from njucode.skills.builtin import METRICS_MANIFEST, TASKS_MANIFEST
        from njucode.skills.executor import SkillExecutor
        from njucode.skills.permissions import PermissionChecker
        from njucode.skills.registry import SkillRegistry

        with make_workspace() as tmp:
            root = Path(tmp)
            write(root / "examples" / "tasks_showcase.py", "# TODO: demo task\n# FIXME: demo fix\n")
            write(
                root / "examples" / "metrics_showcase.py",
                "def branchy(x):\n    if x:\n        return 1\n    return 0\n",
            )
            registry = SkillRegistry(root)
            registry.register_skill(TASKS_MANIFEST)
            registry.register_skill(METRICS_MANIFEST)
            tools = {tool.key: tool for tool in DEFAULT_TOOLS}
            executor = SkillExecutor(
                registry,
                PermissionChecker(tools, registry.skills),
                DummyAuditLogger(),
                CodeAnalyzer(root),
            )

            tasks = executor.execute_by_command("/tasks --path examples --top 1", "session")
            self.assertTrue(tasks.success)
            self.assertEqual(1, tasks.output["filters"]["limit"])
            self.assertEqual(1, len(tasks.output["items"]))

            metrics = executor.execute_by_command("/metrics --path examples --top 1", "session")
            self.assertTrue(metrics.success)
            self.assertEqual(1, metrics.output["filters"]["top_n"])
            self.assertEqual(1, len(metrics.output["hotspots"]))

    def test_skill_executor_validates_parameters(self) -> None:
        # Parameter validation normalizes user input before skill execution.
        from njucode.skills.builtin import SEARCH_MANIFEST
        from njucode.skills.executor import SkillExecutor
        from njucode.skills.permissions import PermissionChecker
        from njucode.skills.registry import SkillRegistry
        from njucode.models import DEFAULT_TOOLS
        from njucode.services.code_analysis import CodeAnalyzer

        with make_workspace() as tmp:
            root = Path(tmp)
            write(root / "a.py", "needle = 1\n")
            registry = SkillRegistry(root)
            registry.register_skill(SEARCH_MANIFEST)
            tools = {tool.key: tool for tool in DEFAULT_TOOLS}
            executor = SkillExecutor(
                registry,
                PermissionChecker(tools, registry.skills),
                DummyAuditLogger(),
                CodeAnalyzer(root),
            )
            valid, error = executor.validate_params(SEARCH_MANIFEST, {"query": "needle"})
            self.assertIsNone(error)
            self.assertEqual("needle", valid["query"])
            invalid, error = executor.validate_params(SEARCH_MANIFEST, {})
            self.assertEqual({}, invalid)
            self.assertIn("Missing required", error)

    def test_skill_executor_runs_builtin_scan(self) -> None:
        # End-to-end builtin execution should emit output and an audit log.
        from njucode.skills.builtin import SCAN_MANIFEST
        from njucode.skills.executor import SkillExecutor
        from njucode.skills.permissions import PermissionChecker
        from njucode.skills.registry import SkillRegistry
        from njucode.models import DEFAULT_TOOLS
        from njucode.services.code_analysis import CodeAnalyzer

        with make_workspace() as tmp:
            root = Path(tmp)
            write(root / "a.py", "x = 1\n")
            registry = SkillRegistry(root)
            registry.register_skill(SCAN_MANIFEST)
            tools = {tool.key: tool for tool in DEFAULT_TOOLS}
            audit = DummyAuditLogger()
            executor = SkillExecutor(
                registry,
                PermissionChecker(tools, registry.skills),
                audit,
                CodeAnalyzer(root),
            )
            result = executor.execute("builtin.scan", {}, "session")
            self.assertTrue(result.success)
            self.assertEqual("scan", result.output["type"])
            self.assertEqual(1, len(audit.logs))

    def test_permission_checker_blocks_disabled_tool(self) -> None:
        # Permission checks enforce AppState tool toggles.
        from njucode.models import DEFAULT_TOOLS
        from njucode.skills.models import SkillPermissionLevel
        from njucode.skills.permissions import PermissionChecker

        tools = {tool.key: tool for tool in DEFAULT_TOOLS}
        tools["write_file"].enabled = False
        checker = PermissionChecker(tools, {})
        ok, message = checker.check_permission("skill", [SkillPermissionLevel.MODIFY_LOCAL])
        self.assertFalse(ok)
        self.assertIn("permission", message.lower())


class AppStateTests(unittest.TestCase):
    """Tests for the central state container without launching the TUI."""

    # AppState coordinates sessions, model config, skills, MCP, and patches.
    # These tests avoid Textual startup while checking the state APIs directly.

    def test_app_state_session_lifecycle(self) -> None:
        # Basic session CRUD must remain stable for the Session panel.
        from njucode.state import AppState

        with make_workspace() as tmp:
            state = AppState(Path(tmp))
            first_id = state.active_session_id
            created = state.create_session("Second")
            self.assertNotEqual(first_id, created.session_id)
            state.switch_session(created.session_id)
            self.assertEqual(created.session_id, state.active_session_id)
            state.rename_session(created.session_id, "Renamed")
            self.assertEqual("Renamed", state.active_session.title)
            state.append_message("user", "hello")
            self.assertEqual(1, len(state.active_session.messages))
            state.delete_session(created.session_id)
            self.assertNotEqual(created.session_id, state.active_session_id)

    def test_app_state_save_does_not_persist_api_key(self) -> None:
        # Security requirement: API keys should come from env/.env, not settings.json.
        from njucode.state import AppState

        with make_workspace() as tmp:
            root = Path(tmp)
            state = AppState(root)
            state.model_config.api_key = "secret"
            state.save()
            payload = json.loads((root / ".nju_code" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual("", payload["model"]["api_key"])

    def test_app_state_patch_engine_wrappers(self) -> None:
        # AppState exposes thin wrappers used by app.py event handlers.
        from njucode.services.code_analysis import CodeAnalyzer
        from njucode.state import AppState

        with make_workspace() as tmp:
            root = Path(tmp)
            write(root / "file.py", "x = 1\n")
            state = AppState(root)
            state.init_skills(CodeAnalyzer(root))
            state.init_patch_engine()
            task = state.create_patch({"file.py": ("x = 1\n", "x = 2\n")}, "change")
            self.assertIsNotNone(task)
            preview = state.preview_patch(task.task_id)
            self.assertIn("x = 2", preview)
            result = state.apply_patch(task.task_id)
            self.assertTrue(result.success)
            rollback = state.rollback_patch(task.task_id)
            self.assertTrue(rollback.success)

    def test_app_state_builds_agent_skill_context(self) -> None:
        # Agent skill selection injects procedural instructions into model context.
        from njucode.services.code_analysis import CodeAnalyzer
        from njucode.state import AppState

        with make_workspace() as tmp:
            root = Path(tmp)
            state = AppState(root)
            state.init_skills(CodeAnalyzer(root))
            context = state.build_agent_skill_context("review this code", max_skills=2)
            self.assertIsInstance(context, str)
            self.assertIn("Agent Skills", context)


class MCPTests(unittest.TestCase):
    """Tests for MCP configuration models and preset registration."""

    # MCP tests stay offline: they validate configuration and adapters without
    # starting npx/uvx server processes.

    def test_mcp_manager_loads_default_presets(self) -> None:
        # Presets should be visible even before the user connects a server.
        from njucode.mcp.manager import MCPManager

        with make_workspace() as tmp:
            manager = MCPManager(Path(tmp))
            manager.load()
            self.assertIn("filesystem", manager.servers)
            self.assertIn("memory", manager.servers)
            self.assertIn("fetch", manager.servers)
            self.assertIn("git", manager.servers)

    def test_mcp_tool_toggle_properties(self) -> None:
        # UI panels rely on label/description compatibility properties.
        from njucode.mcp.models import MCPToolInfo, MCPToolToggle

        info = MCPToolInfo(
            tool_name="read_file",
            server_id="filesystem",
            skill_id="mcp.filesystem.read_file",
            description="Read a file",
        )
        toggle = MCPToolToggle(info)
        self.assertEqual("read_file", toggle.label)
        self.assertEqual("Read a file", toggle.description)

    def test_mcp_tool_adapter_converts_schema_to_manifest(self) -> None:
        # JSON schema from MCP tools is converted into internal parameter metadata.
        from njucode.mcp.models import MCPServerConfig, MCPToolInfo
        from njucode.mcp.tool_adapter import MCPToolAdapter

        info = MCPToolInfo(
            tool_name="lookup",
            server_id="demo",
            skill_id="mcp.demo.lookup",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            description="Lookup data",
        )
        server = MCPServerConfig(server_id="demo", name="Demo")
        manifest = MCPToolAdapter().convert_to_manifest(info, server)
        self.assertEqual("mcp.demo.lookup", manifest["skill_id"])
        self.assertEqual("Lookup data", manifest["description"])
        self.assertEqual(1, len(manifest["parameters"]))


class OpenAIClientTests(unittest.TestCase):
    """Tests for OpenAI-compatible request assembly without real API calls."""

    # These tests are deliberately offline.  They only exercise request shaping
    # and the no-key fallback path.

    def test_stream_chat_without_api_key_returns_system_hint(self) -> None:
        # Empty API key should produce a helpful local hint, not a network error.
        from njucode.services.openai_client import OpenAICompatibleClient, OpenAIRequest

        client = OpenAICompatibleClient()
        request = OpenAIRequest(
            base_url="https://example.test/v1",
            api_key="",
            model="demo",
            messages=[{"role": "user", "content": "hello"}],
        )
        chunks = list(client.stream_chat(request))
        self.assertEqual(1, len(chunks))
        self.assertIn("API Key", chunks[0])

    def test_build_messages_injects_file_context(self) -> None:
        # File context is inserted as a system message before user history.
        from njucode.services.openai_client import OpenAICompatibleClient, OpenAIRequest

        client = OpenAICompatibleClient()
        request = OpenAIRequest(
            base_url="https://example.test/v1",
            api_key="x",
            model="demo",
            messages=[{"role": "user", "content": "hello"}],
            file_contexts=[("a.py", "print('a')")],
        )
        messages = client._build_messages(request)
        self.assertEqual("system", messages[0]["role"])
        self.assertIn("a.py", messages[0]["content"])

    def test_build_messages_injects_model_file_and_ignores_missing_file(self) -> None:
        # The optional model file is read locally and truncated into a system message.
        from njucode.services.openai_client import OpenAICompatibleClient, OpenAIRequest

        client = OpenAICompatibleClient()
        with make_workspace() as tmp:
            root = Path(tmp)
            model_file = root / "model_context.md"
            model_file.write_text("重要配置\n" + "x" * 5000, encoding="utf-8")
            request = OpenAIRequest(
                base_url="https://example.test/v1",
                api_key="x",
                model="demo",
                messages=[{"role": "user", "content": "hello"}],
                model_file=str(model_file),
            )
            messages = client._build_messages(request)
            self.assertEqual("system", messages[0]["role"])
            self.assertIn("配置的指定文件内容", messages[0]["content"])
            self.assertLess(len(messages[0]["content"]), 4100)

            missing = OpenAIRequest(
                base_url="https://example.test/v1",
                api_key="x",
                model="demo",
                messages=[{"role": "user", "content": "hello"}],
                model_file=str(root / "missing.md"),
            )
            self.assertEqual([{"role": "user", "content": "hello"}], client._build_messages(missing))

    def test_stream_chat_yields_chunks_closes_response_and_honors_stop(self) -> None:
        # Streaming is tested with a local fake OpenAI object, not the network.
        from threading import Event
        from unittest.mock import patch

        from njucode.services.openai_client import OpenAICompatibleClient, OpenAIRequest

        class Delta:
            def __init__(self, content: str | None) -> None:
                self.content = content

        class Choice:
            def __init__(self, content: str | None) -> None:
                self.delta = Delta(content)

        class Chunk:
            def __init__(self, content: str | None = None, choices: list[Any] | None = None) -> None:
                self.choices = choices if choices is not None else [Choice(content)]

        class FakeResponse:
            closed = False

            def __init__(self, chunks: list[Chunk]) -> None:
                self._chunks = chunks

            def __iter__(self):
                return iter(self._chunks)

            def close(self) -> None:
                FakeResponse.closed = True

        class FakeCompletions:
            last_kwargs: dict[str, Any] = {}

            def create(self, **kwargs: Any) -> FakeResponse:
                FakeCompletions.last_kwargs = kwargs
                return FakeResponse([Chunk(choices=[]), Chunk(None), Chunk("hello"), Chunk(" world")])

        class FakeOpenAI:
            def __init__(self, base_url: str, api_key: str) -> None:
                self.chat = type("Chat", (), {"completions": FakeCompletions()})()

        client = OpenAICompatibleClient()
        request = OpenAIRequest(
            base_url="https://example.test/v1",
            api_key="key",
            model="demo",
            messages=[{"role": "user", "content": "hello"}],
        )
        with patch("njucode.services.openai_client.OpenAI", FakeOpenAI):
            chunks = list(client.stream_chat(request))
        self.assertEqual(["hello", " world"], chunks)
        self.assertTrue(FakeResponse.closed)
        self.assertTrue(FakeCompletions.last_kwargs["stream"])
        self.assertEqual(request, client.last_request)

        stop_event = Event()
        stop_event.set()
        FakeResponse.closed = False
        with patch("njucode.services.openai_client.OpenAI", FakeOpenAI):
            self.assertEqual([], list(client.stream_chat(request, stop_event=stop_event)))
        self.assertTrue(FakeResponse.closed)

    def test_chat_handles_empty_response_and_stream_errors(self) -> None:
        # Non-stream chat aggregates chunks and turns stream failures into text.
        from njucode.services.openai_client import OpenAICompatibleClient, OpenAIRequest

        request = OpenAIRequest(
            base_url="https://example.test/v1",
            api_key="key",
            model="demo",
            messages=[{"role": "user", "content": "hello"}],
        )

        client = OpenAICompatibleClient()
        client.stream_chat = lambda request: iter(["  "])  # type: ignore[method-assign]
        self.assertIn("模型返回为空", client.chat(request))

        def failing_stream(request: Any):
            raise RuntimeError("offline")
            yield ""

        client.stream_chat = failing_stream  # type: ignore[method-assign]
        self.assertIn("调用模型失败", client.chat(request))


class ProjectDoctorTests(unittest.TestCase):
    """Tests for the Project Doctor report and command/skill integration."""

    # Doctor tests ensure the same diagnostics are reachable through direct
    # service calls, analyzer commands, and builtin skill execution.

    def test_project_test_runner_lists_checks(self) -> None:
        # The list is useful for future filtering and command help.
        from njucode.services.project_testing import ProjectTestRunner

        runner = ProjectTestRunner(ROOT)
        checks = runner.list_checks()
        self.assertIn("layout", checks)
        self.assertIn("python_syntax", checks)
        self.assertIn("patch_engine", checks)
        self.assertIn("skills", checks)

    def test_project_test_runner_can_run_selected_checks(self) -> None:
        # Selected checks keep tests fast while exercising report generation.
        from njucode.services.project_testing import ProjectTestRunner

        runner = ProjectTestRunner(ROOT)
        report = runner.run_all(selected=["layout", "python_syntax"])
        self.assertEqual(2, len(report.results))
        self.assertEqual(0, report.fail_count)
        self.assertTrue(report.to_json().startswith("{"))
        self.assertIn("Project Doctor", report.to_markdown())

    def test_doctor_payload_contains_text_and_markdown(self) -> None:
        # Command handlers expect machine fields plus display-ready text.
        from njucode.services.project_testing import run_doctor_as_payload

        payload = run_doctor_as_payload(ROOT, selected=["layout"])
        self.assertEqual("project_test_report", payload["type"])
        self.assertIn("text", payload)
        self.assertIn("markdown", payload)
        self.assertIn("layout", payload["text"])

    def test_doctor_payload_can_save_report_files(self) -> None:
        # The application-time /doctor path saves Markdown and JSON artifacts;
        # the standalone test runner disables this except when explicitly asked.
        from njucode.services.project_testing import run_doctor_as_payload

        with make_workspace() as tmp:
            root = Path(tmp)
            write(root / "README.md", "demo\n")
            payload = run_doctor_as_payload(root, selected=["layout"], save_report=True)
            self.assertIn("markdown_path", payload)
            self.assertIn("json_path", payload)
            self.assertIn("[Saved Report]", payload["text"])
            self.assertTrue((root / payload["markdown_path"]).exists())
            self.assertTrue((root / payload["json_path"]).exists())

    def test_code_analyzer_doctor_command(self) -> None:
        # /doctor is wired through CodeAnalyzer.run_command.
        from njucode.services.code_analysis import CodeAnalyzer

        analyzer = CodeAnalyzer(ROOT)
        result = analyzer.run_command("/doctor")
        self.assertEqual("project_test_report", result["type"])
        self.assertIn("summary", result)
        self.assertNotIn("markdown_path", result)
        self.assertNotIn("json_path", result)

    def test_builtin_doctor_skill_executes(self) -> None:
        # builtin.doctor is the Skills-layer entrypoint for the same report.
        from njucode.services.code_analysis import CodeAnalyzer
        from njucode.skills.builtin import execute_builtin_skill

        result = execute_builtin_skill("builtin.doctor", CodeAnalyzer(ROOT), {"verbose": False})
        self.assertEqual("project_test_report", result["type"])
        self.assertIn("Project Doctor", result["markdown"])
        self.assertNotIn("markdown_path", result)

    def test_report_to_text_includes_failures_and_warnings(self) -> None:
        # Warning details should be visible in compact chat output.
        from njucode.services.project_testing import (
            ProjectCheckResult,
            ProjectIssue,
            ProjectTestReport,
            report_to_text,
        )

        report = ProjectTestReport(
            workspace="demo",
            generated_at=datetime.now().isoformat(timespec="seconds"),
            results=[
                ProjectCheckResult("ok", "pass", "fine"),
                ProjectCheckResult("warn", "warn", "careful", issues=[ProjectIssue("warning", "watch")]),
            ],
        )
        text = report_to_text(report)
        self.assertIn("[WARN] warn", text)
        self.assertIn("watch", text)

    def test_report_helpers_cover_status_and_long_issue_lists(self) -> None:
        # Status helpers and long issue truncation are small, but they shape the
        # chat-facing doctor summary.
        from njucode.services.project_testing import (
            ProjectCheckResult,
            ProjectIssue,
            ProjectTestReport,
            report_to_text,
        )

        failing = ProjectCheckResult(
            "fail",
            "fail",
            "broken",
            issues=[ProjectIssue("error", f"issue-{idx}", hint="fix") for idx in range(12)],
        )
        warning = ProjectCheckResult("warn", "warn", "careful")
        skipped = ProjectCheckResult("skip", "skip", "not needed")
        self.assertTrue(failing.failed)
        self.assertTrue(warning.warned)
        report = ProjectTestReport(
            workspace="demo",
            generated_at=datetime.now().isoformat(timespec="seconds"),
            results=[failing, warning, skipped],
        )
        text = report_to_text(report, verbose=True)
        self.assertIn("[FAIL] fail", text)
        self.assertIn("[SKIP] skip", text)
        self.assertIn("more issue", text)


class DocumentationTests(unittest.TestCase):
    """Checks that README text stays aligned with implemented commands."""

    # These tests turn documentation drift into a visible failure while the
    # project is still evolving quickly.

    def test_readme_mentions_core_commands(self) -> None:
        # README should list every user-facing analysis command.
        readme = (ROOT / "README.md").read_text(encoding="utf-8", errors="ignore")
        for command in [
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
        ]:
            self.assertIn(command, readme)

    def test_readme_mentions_conda_run_flow(self) -> None:
        # New contributors need the basic install/start commands.
        readme = (ROOT / "README.md").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("pip install -r requirements.txt", readme)
        self.assertIn("python main.py", readme)

    def test_requirements_has_no_direct_url_dependencies(self) -> None:
        # Direct URLs reduce reproducibility in classroom environments.
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8", errors="ignore")
        direct = [line for line in requirements.splitlines() if "://" in line and not line.strip().startswith("#")]
        self.assertEqual([], direct)


class ModelDataclassTests(unittest.TestCase):
    """Simple invariants for dataclasses and default configuration constants."""

    # These are small guardrails around default values that many modules assume.

    def test_chat_session_defaults_are_unique(self) -> None:
        # Each session should receive its own UUID.
        from njucode.models import ChatSession

        a = ChatSession()
        b = ChatSession()
        self.assertNotEqual(a.session_id, b.session_id)
        self.assertEqual([], a.messages)

    def test_default_tools_include_expected_permissions(self) -> None:
        # PermissionChecker maps skill permissions to these tool keys.
        from njucode.models import DEFAULT_TOOLS

        keys = {tool.key for tool in DEFAULT_TOOLS}
        self.assertIn("read_file", keys)
        self.assertIn("write_file", keys)
        self.assertIn("terminal", keys)
        self.assertIn("git", keys)

    def test_mirror_presets_include_common_providers(self) -> None:
        # ConfigPanel exposes these model endpoint presets.
        from njucode.models import MIRROR_PRESETS

        self.assertIn("official", MIRROR_PRESETS)
        self.assertIn("modelscope", MIRROR_PRESETS)
        self.assertIn("custom", MIRROR_PRESETS)


class RuntimeToolsTests(unittest.TestCase):
    """Tests for small runtime examples used by the Tools panel."""

    # Runtime tool tests keep the demo button and hello_world sample honest.

    def test_hello_world_runtime_tool_returns_pascal_triangle(self) -> None:
        # The Tools panel example should run in an isolated workspace.
        from njucode.services.runtime_tools import run_hello_world

        with make_workspace() as tmp:
            output = run_hello_world(Path(tmp))
            self.assertIsInstance(output, str)
            self.assertIn("Hello World", output)

    def test_hello_world_generator(self) -> None:
        # hello_world.py still contains a reusable Pascal triangle helper.
        from hello_world import generate_pascals_triangle

        triangle = generate_pascals_triangle(5)
        self.assertEqual([1], triangle[0])
        self.assertEqual([1, 4, 6, 4, 1], triangle[4])


class FullSystemSmokeTests(unittest.TestCase):
    """End-to-end smoke checks that combine several project areas."""

    # These tests do not replace focused unit tests; they assert that several
    # important layers still compose after edits.

    def test_selected_project_doctor_checks_are_clean(self) -> None:
        # Run the highest-signal doctor checks as a final integration smoke test.
        from njucode.services.project_testing import ProjectTestRunner

        report = ProjectTestRunner(ROOT).run_all(
            selected=[
                "layout",
                "requirements",
                "python_syntax",
                "entrypoint",
                "code_extractor",
                "patch_engine",
                "skills",
                "mcp",
                "ui_structure",
                "readme_commands",
            ]
        )
        failing = [result.to_dict() for result in report.results if result.status == "fail"]
        self.assertEqual([], failing)

    def test_complete_doctor_report_is_serializable(self) -> None:
        # Reports should survive JSON roundtrip for future export or CI use.
        from njucode.services.project_testing import ProjectTestRunner

        report = ProjectTestRunner(ROOT).run_all(selected=["layout", "line_budget"])
        payload = report.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False)
        decoded = json.loads(encoded)
        self.assertEqual(payload["type"], decoded["type"])
        self.assertEqual(len(payload["results"]), len(decoded["results"]))


if __name__ == "__main__":
    raise SystemExit(run_tests_with_report(sys.argv[1:]))
