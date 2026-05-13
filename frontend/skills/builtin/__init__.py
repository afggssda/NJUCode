"""Built-in skills package - Contains all built-in skill implementations.

This module wraps CodeAnalyzer commands into Skills, and provides the
Patch/Rollback execution skills (WBS-4).
"""

from typing import Any, Dict, Optional

from ..models import (
    SkillKind,
    SkillManifest,
    SkillParameter,
    SkillOutput,
    SkillPermissionLevel,
)

# Module-level PatchEngine reference — set by AppState.init_patch_engine()
_patch_engine: Optional[Any] = None


def set_patch_engine(engine: Any) -> None:
    """Register the PatchEngine instance for use by patch skill executors."""
    global _patch_engine
    _patch_engine = engine


def execute_builtin_skill(
    skill_id: str,
    analyzer: Any,
    params: Dict[str, Any],
) -> Any:
    """Execute a built-in skill by ID.

    Args:
        skill_id: Skill to execute (e.g., "builtin.scan")
        analyzer: CodeAnalyzer instance
        params: Input parameters

    Returns:
        Skill output (dict)
    """
    skill_map = {
        "builtin.help": execute_help,
        "builtin.scan": execute_scan,
        "builtin.search": execute_search,
        "builtin.symbol": execute_symbol,
        "builtin.summary": execute_summary,
        "builtin.deps": execute_deps,
        "builtin.recall": execute_recall,
        "builtin.impact": execute_impact,
        "builtin.patch.diff": execute_patch_diff,
        "builtin.patch.apply": execute_patch_apply,
        "builtin.patch.rollback": execute_patch_rollback,
        "builtin.patch.history": execute_patch_history,
    }

    executor = skill_map.get(skill_id)
    if not executor:
        return {"type": "error", "error": f"Unknown builtin skill: {skill_id}"}

    return executor(analyzer, params)


# ============== Skill Manifests ==============

HELP_MANIFEST = SkillManifest(
    skill_id="builtin.help",
    name="Help",
    version="1.0.0",
    description="Show available commands and skills",
    category="utility",
    parameters=[],
    output=SkillOutput(type="json", description="List of available commands"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/help"],
    is_builtin=True,
)

SCAN_MANIFEST = SkillManifest(
    skill_id="builtin.scan",
    name="Project Scan",
    version="1.0.0",
    description="Scan project directory and generate file index",
    category="analysis",
    parameters=[],
    output=SkillOutput(
        type="json",
        description="File list, suffix stats, total size"
    ),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/scan"],
    is_builtin=True,
)

SEARCH_MANIFEST = SkillManifest(
    skill_id="builtin.search",
    name="Text Search",
    version="1.0.0",
    description="Search text content in project files",
    category="retrieval",
    parameters=[
        SkillParameter(
            name="query",
            type="string",
            required=True,
            description="Search keyword or regex pattern"
        ),
        SkillParameter(
            name="case_sensitive",
            type="boolean",
            required=False,
            default=False,
            description="Case sensitive search"
        ),
        SkillParameter(
            name="use_regex",
            type="boolean",
            required=False,
            default=False,
            description="Use regex pattern"
        ),
    ],
    output=SkillOutput(type="json", description="Search hits with file/line/snippet"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/search"],
    is_builtin=True,
)

SYMBOL_MANIFEST = SkillManifest(
    skill_id="builtin.symbol",
    name="Symbol Search",
    version="1.0.0",
    description="Search Python symbols (class, def, async def)",
    category="retrieval",
    parameters=[
        SkillParameter(
            name="symbol_name",
            type="string",
            required=True,
            description="Symbol name to search"
        ),
    ],
    output=SkillOutput(type="json", description="Symbol definitions with location"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/symbol"],
    is_builtin=True,
)

SUMMARY_MANIFEST = SkillManifest(
    skill_id="builtin.summary",
    name="File Summary",
    version="1.0.0",
    description="Generate summary of a file",
    category="analysis",
    parameters=[
        SkillParameter(
            name="path",
            type="path",
            required=True,
            description="Relative path to file"
        ),
    ],
    output=SkillOutput(type="json", description="Classes, functions, dependencies, entry"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/summary"],
    is_builtin=True,
)

DEPS_MANIFEST = SkillManifest(
    skill_id="builtin.deps",
    name="Dependency Analysis",
    version="1.0.0",
    description="Analyze file dependencies (imports)",
    category="analysis",
    parameters=[
        SkillParameter(
            name="path",
            type="path",
            required=True,
            description="Relative path to Python file"
        ),
        SkillParameter(
            name="depth",
            type="integer",
            required=False,
            default=1,
            description="Dependency depth (1 or 2)"
        ),
    ],
    output=SkillOutput(type="json", description="Dependency graph layers"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/deps"],
    is_builtin=True,
)

RECALL_MANIFEST = SkillManifest(
    skill_id="builtin.recall",
    name="File Recall",
    version="1.0.0",
    description="Recall relevant files by natural language query",
    category="retrieval",
    parameters=[
        SkillParameter(
            name="query",
            type="string",
            required=True,
            description="Natural language requirement"
        ),
        SkillParameter(
            name="top_k",
            type="integer",
            required=False,
            default=10,
            description="Number of results (5-30)"
        ),
    ],
    output=SkillOutput(type="json", description="Ranked file list with scores"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/recall"],
    is_builtin=True,
)

IMPACT_MANIFEST = SkillManifest(
    skill_id="builtin.impact",
    name="Impact Analysis",
    version="1.0.0",
    description="Analyze change impact for file or symbol",
    category="analysis",
    parameters=[
        SkillParameter(
            name="target",
            type="string",
            required=True,
            description="File path or symbol name"
        ),
        SkillParameter(
            name="depth",
            type="integer",
            required=False,
            default=2,
            description="Analysis depth (1 or 2)"
        ),
    ],
    output=SkillOutput(type="json", description="Affected files, risk level, read order"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/impact"],
    is_builtin=True,
)

# ============== Patch/Rollback Skill Manifests (WBS-4) ==============

PATCH_DIFF_MANIFEST = SkillManifest(
    skill_id="builtin.patch.diff",
    name="File Diff",
    version="1.0.0",
    description="Show unified diff of a file vs its last patch backup",
    category="modification",
    parameters=[
        SkillParameter(
            name="path",
            type="path",
            required=True,
            description="Relative path to file",
        ),
    ],
    output=SkillOutput(type="diff", description="Unified diff output"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/diff"],
    is_builtin=True,
)

PATCH_APPLY_MANIFEST = SkillManifest(
    skill_id="builtin.patch.apply",
    name="Apply Patch",
    version="1.0.0",
    description="Apply the most recent pending patch task",
    category="modification",
    parameters=[
        SkillParameter(
            name="task_id",
            type="string",
            required=False,
            default="",
            description="Specific task ID to apply (omit for most recent pending)",
        ),
    ],
    output=SkillOutput(type="text", description="Apply result"),
    permissions=[SkillPermissionLevel.MODIFY_LOCAL],
    command_aliases=["/patch"],
    is_builtin=True,
)

PATCH_ROLLBACK_MANIFEST = SkillManifest(
    skill_id="builtin.patch.rollback",
    name="Rollback Patch",
    version="1.0.0",
    description="Rollback the last applied patch or a specific task by ID",
    category="modification",
    parameters=[
        SkillParameter(
            name="task_id",
            type="string",
            required=False,
            default="",
            description="Task ID to rollback (omit for last applied patch)",
        ),
    ],
    output=SkillOutput(type="text", description="Rollback result"),
    permissions=[SkillPermissionLevel.MODIFY_LOCAL],
    command_aliases=["/rollback"],
    is_builtin=True,
)

PATCH_HISTORY_MANIFEST = SkillManifest(
    skill_id="builtin.patch.history",
    name="Patch History",
    version="1.0.0",
    description="Show patch operation history log",
    category="modification",
    parameters=[
        SkillParameter(
            name="limit",
            type="integer",
            required=False,
            default=10,
            description="Number of history entries to show",
        ),
    ],
    output=SkillOutput(type="text", description="Formatted patch history table"),
    permissions=[SkillPermissionLevel.READ_ONLY],
    command_aliases=["/patchlog"],
    is_builtin=True,
)


CODEBASE_NAVIGATOR_AGENT = SkillManifest(
    skill_id="agent.codebase-navigator",
    name="codebase-navigator",
    version="1.0.0",
    description=(
        "Use when the user asks to understand, inspect, navigate, summarize, or trace a codebase; "
        "find relevant files, symbols, dependencies, and implementation entry points."
    ),
    category="agent",
    permissions=[],
    command_aliases=[],
    is_builtin=True,
    kind=SkillKind.AGENT,
    instructions=(
        "# Codebase Navigator\n\n"
        "When helping with codebase understanding, first identify the relevant files and symbols before explaining behavior. "
        "Prefer local analysis commands such as /scan, /search, /symbol, /summary, /deps, /recall, and /impact when they will reduce guesswork. "
        "Explain the current architecture from concrete files and call out uncertainty when a path was inferred rather than verified. "
        "Keep the final answer organized around entry points, data flow, and the files the user should read next."
    ),
)

CODE_REVIEWER_AGENT = SkillManifest(
    skill_id="agent.code-reviewer",
    name="code-reviewer",
    version="1.0.0",
    description=(
        "Use when the user asks for review, bug finding, risk analysis, regression analysis, or whether a code change looks correct."
    ),
    category="agent",
    permissions=[],
    command_aliases=[],
    is_builtin=True,
    kind=SkillKind.AGENT,
    instructions=(
        "# Code Reviewer\n\n"
        "Review code in a findings-first style. Prioritize concrete bugs, behavioral regressions, security issues, broken edge cases, "
        "and missing tests. For each finding, point to the smallest relevant file/line location and explain why it matters. "
        "Avoid broad style commentary unless it creates real maintenance or correctness risk. If no issues are found, say that clearly "
        "and mention residual test gaps."
    ),
)

IMPLEMENTATION_PLANNER_AGENT = SkillManifest(
    skill_id="agent.implementation-planner",
    name="implementation-planner",
    version="1.0.0",
    description=(
        "Use when the user asks to implement, refactor, fix, or extend code and the change needs a careful multi-step approach."
    ),
    category="agent",
    permissions=[],
    command_aliases=[],
    is_builtin=True,
    kind=SkillKind.AGENT,
    instructions=(
        "# Implementation Planner\n\n"
        "Before editing, inspect the existing patterns and choose the smallest coherent change. Preserve unrelated user edits. "
        "Separate capability layers clearly: instructions/context, local tools, external MCP tools, UI state, and persistence. "
        "After implementation, run focused verification that exercises the changed path. Report what changed and what could not be verified."
    ),
)

MCP_TOOL_USER_AGENT = SkillManifest(
    skill_id="agent.mcp-tool-user",
    name="mcp-tool-user",
    version="1.0.0",
    description=(
        "Use when the user asks to use MCP, connect external tools, call MCP servers, or reason about filesystem, git, memory, fetch, or other MCP tools."
    ),
    category="agent",
    permissions=[],
    command_aliases=[],
    is_builtin=True,
    kind=SkillKind.AGENT,
    instructions=(
        "# MCP Tool User\n\n"
        "Treat MCP servers as tools, not as agent skills. Check whether a server is configured and connected before relying on its tools. "
        "Use /mcp <mcp.server.tool> with a JSON object for explicit manual calls when needed. If a preset server is disabled or unavailable, "
        "explain the missing dependency or configuration instead of assuming it worked."
    ),
)


# All builtin manifests
BUILTIN_MANIFESTS = [
    HELP_MANIFEST,
    SCAN_MANIFEST,
    SEARCH_MANIFEST,
    SYMBOL_MANIFEST,
    SUMMARY_MANIFEST,
    DEPS_MANIFEST,
    RECALL_MANIFEST,
    IMPACT_MANIFEST,
    PATCH_DIFF_MANIFEST,
    PATCH_APPLY_MANIFEST,
    PATCH_ROLLBACK_MANIFEST,
    PATCH_HISTORY_MANIFEST,
]


BUILTIN_AGENT_MANIFESTS = [
    CODEBASE_NAVIGATOR_AGENT,
    CODE_REVIEWER_AGENT,
    IMPLEMENTATION_PLANNER_AGENT,
    MCP_TOOL_USER_AGENT,
]


# ============== Skill Executors ==============

def execute_help(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute help skill."""
    return analyzer.run_command("/help")


def execute_scan(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute scan skill."""
    return analyzer.scan_project()


def execute_search(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute search skill."""
    return analyzer.search_text(
        query=params.get("query", ""),
        case_sensitive=params.get("case_sensitive", False),
        use_regex=params.get("use_regex", False),
    )


def execute_symbol(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute symbol search skill."""
    return analyzer.symbol_search(params.get("symbol_name", ""))


def execute_summary(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute file summary skill."""
    return analyzer.summarize_file(params.get("path", ""))


def execute_deps(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute dependency analysis skill."""
    return analyzer.neighbors(
        params.get("path", ""),
        depth=params.get("depth", 1),
    )


def execute_recall(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute file recall skill."""
    return analyzer.recall_files(
        params.get("query", ""),
        top_k=params.get("top_k", 10),
    )


def execute_impact(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute impact analysis skill."""
    return analyzer.impact_analysis(
        params.get("target", ""),
        depth=params.get("depth", 2),
    )


# ============== Patch/Rollback Skill Executors (WBS-4) ==============

def execute_patch_diff(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Show unified diff of a file vs its last patch backup."""
    if not _patch_engine:
        return {"type": "error", "error": "Patch engine not initialized"}
    file_path = params.get("path", "").strip()
    if not file_path:
        return {"type": "error", "error": "Parameter 'path' is required"}

    history = _patch_engine.get_history()
    for task in history:
        for op in task.operations:
            if op.file_path == file_path:
                diff = op.diff or op.generate_diff()
                return {
                    "type": "diff",
                    "file_path": file_path,
                    "diff": diff,
                    "task_id": task.task_id,
                    "task_status": task.status.value,
                    "operation_type": op.operation_type,
                }
    return {"type": "text", "text": f"No patch history found for: {file_path}"}


def execute_patch_apply(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Apply a pending patch task."""
    if not _patch_engine:
        return {"type": "error", "error": "Patch engine not initialized"}
    task_id = params.get("task_id", "").strip()

    if task_id:
        task = _patch_engine.history_store.get_task(task_id)
        if not task:
            return {"type": "error", "error": f"Task not found: {task_id}"}
    else:
        pending = _patch_engine.get_pending_tasks()
        if not pending:
            return {
                "type": "text",
                "text": (
                    "No pending patch tasks found.\n"
                    "Use the Patch panel or create a patch via the AI chat."
                ),
            }
        task = pending[0]

    result = _patch_engine.apply_patch(task)
    if result.success:
        return {
            "type": "text",
            "text": (
                f"Patch applied successfully.\n"
                f"Task ID : {result.task_id[:8]}\n"
                f"Files   : {len(result.files_modified)}\n"
                + "\n".join(f"  • {f}" for f in result.files_modified)
            ),
            "files_modified": result.files_modified,
        }
    return {"type": "error", "error": result.error_message}


def execute_patch_rollback(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Rollback a patch by task_id, or rollback the last applied patch."""
    if not _patch_engine:
        return {"type": "error", "error": "Patch engine not initialized"}

    task_id = params.get("task_id", "").strip()
    if not task_id:
        last = _patch_engine.history_store.get_last_applied()
        if not last:
            return {"type": "text", "text": "No applied patches to rollback."}
        task_id = last.task_id

    result = _patch_engine.rollback_patch(task_id)
    if result.success:
        return {
            "type": "text",
            "text": (
                f"Rollback complete.\n"
                f"Task ID  : {result.task_id[:8]}\n"
                f"Restored : {len(result.files_restored)} file(s)\n"
                + "\n".join(f"  • {f}" for f in result.files_restored)
            ),
            "files_restored": result.files_restored,
        }
    return {"type": "error", "error": result.error_message}


def execute_patch_history(analyzer: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """Return formatted patch history table."""
    if not _patch_engine:
        return {"type": "error", "error": "Patch engine not initialized"}

    limit = int(params.get("limit", 10))
    table = _patch_engine.format_history(limit=limit)
    tasks = _patch_engine.get_history(limit=limit)
    return {
        "type": "text",
        "text": table,
        "count": len(tasks),
    }
