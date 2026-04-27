"""Built-in skills package - Contains all built-in skill implementations.

This module wraps CodeAnalyzer commands into Skills.
"""

from typing import Any, Dict

from ..models import (
    SkillKind,
    SkillManifest,
    SkillParameter,
    SkillOutput,
    SkillPermissionLevel,
)


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
