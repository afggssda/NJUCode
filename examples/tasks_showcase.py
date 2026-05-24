"""Small sample file for demonstrating the /tasks command.

This file is intentionally simple.  The task scanner only treats strict comment
markers such as ``# TODO:`` or ``# BUG(owner):`` as task items, so the examples
below are easy to find during a classroom/demo run.
"""

from __future__ import annotations


# TODO(alice): Add a real project selector before running batch analysis.
def choose_workspace(default_path: str) -> str:
    """Return the workspace path used by the demo."""
    return default_path.strip() or "."


# FIXME: Validate that the selected workspace exists before scanning it.
def normalize_command(command: str) -> str:
    """Normalize a user command for display."""
    return " ".join(command.strip().split())


# BUG(bob): Empty command history currently hides the most recent failure.
def latest_command(history: list[str]) -> str:
    """Return the most recent command, or an empty string."""
    if not history:
        return ""
    return history[-1]


# HACK: Demo data is in-memory so the example stays safe to run.
DEMO_COMMANDS = [
    "/tasks --top 10",
    "/metrics --top 5",
    "/doctor --verbose",
]


# NOTE: Run `/tasks --include-tests` if you also want test fixtures included.
def describe_demo() -> str:
    """Produce a short display string for the demo panel."""
    workspace = choose_workspace(".")
    commands = ", ".join(normalize_command(item) for item in DEMO_COMMANDS)
    return f"Workspace={workspace}; commands={commands}"
