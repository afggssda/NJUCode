"""Skills data models for NJUCode plugin system.

This module defines the core data structures for skill management:
- SkillManifest: Defines skill metadata, parameters, output, permissions
- SkillToggle: Manages skill enable/disable state
- SkillExecutionLog: Records execution audit trail
- SkillPermissionLevel: Permission hierarchy for skill access control
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


class SkillPermissionLevel(Enum):
    """Skill permission levels - determines what operations a skill can perform."""
    READ_ONLY = "read_only"          # Only read files, no write access
    MODIFY_LOCAL = "modify_local"    # Can modify local files
    EXECUTE_COMMAND = "execute_command"  # Can execute shell commands
    NETWORK_ACCESS = "network_access"    # Can access network/web
    FULL_ACCESS = "full_access"      # Full permissions


class SkillStatus(Enum):
    """Skill operational status."""
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"
    PENDING = "pending"


class SkillKind(Enum):
    """High-level skill type.

    Agent skills provide instructions/context for the model. Command skills are
    executable local actions such as /scan or plugin entry points.
    """
    AGENT = "agent"
    COMMAND = "command"
    PLUGIN = "plugin"


@dataclass
class SkillParameter:
    """Skill parameter definition for input validation."""
    name: str
    type: str  # "string", "integer", "boolean", "path", "list"
    required: bool = True
    default: Optional[Any] = None
    description: str = ""
    validation_pattern: Optional[str] = None  # Regex for validation


@dataclass
class SkillOutput:
    """Skill output definition."""
    type: str  # "text", "json", "markdown", "file", "diff"
    schema: Optional[Dict[str, Any]] = None
    description: str = ""


@dataclass
class SkillManifest:
    """Skill manifest - defines skill metadata and capabilities.

    All skills (builtin and plugins) use this unified manifest format.
    """
    skill_id: str  # Unique ID: "builtin.scan", "plugin.custom_analyzer"
    name: str  # Display name
    version: str = "1.0.0"
    description: str = ""
    category: str = "analysis"  # analysis, retrieval, modification, testing, utility

    # Input parameters
    parameters: List[SkillParameter] = field(default_factory=list)

    # Output definition
    output: Optional[SkillOutput] = None

    # Permission requirements
    permissions: List[SkillPermissionLevel] = field(
        default_factory=lambda: [SkillPermissionLevel.READ_ONLY]
    )

    # Skill dependencies
    dependencies: List[str] = field(default_factory=list)

    # Command aliases (e.g., "/scan", "/search")
    command_aliases: List[str] = field(default_factory=list)

    # Author info (for external plugins)
    author: str = "NJUCode Team"
    homepage: Optional[str] = None

    # Builtin flag
    is_builtin: bool = True

    # Plugin path (for external plugins)
    plugin_path: Optional[str] = None

    # Entry point (for external plugins)
    entry_point: Optional[str] = None

    # Skill type and instruction source for agent skills
    kind: SkillKind = SkillKind.COMMAND
    instructions_path: Optional[str] = None
    instructions: str = ""


@dataclass
class SkillToggle:
    """Skill toggle state - extends ToolToggle pattern for AppState integration."""
    skill_id: str
    manifest: SkillManifest
    enabled: bool = True
    status: SkillStatus = SkillStatus.ENABLED
    last_error: Optional[str] = None
    usage_count: int = 0

    @property
    def label(self) -> str:
        """Alias for manifest.name for UI compatibility."""
        return self.manifest.name

    @property
    def description(self) -> str:
        """Alias for manifest.description for UI compatibility."""
        return self.manifest.description


@dataclass
class SkillExecutionLog:
    """Skill execution audit log - supports FR-07 and FR-10 requirements."""
    log_id: str = field(default_factory=lambda: str(uuid4()))
    skill_id: str = ""
    session_id: str = ""

    # Input parameters
    input_params: Dict[str, Any] = field(default_factory=dict)

    # Output summary
    output_summary: str = ""
    output_type: str = "text"

    # Execution status
    success: bool = True
    error_message: Optional[str] = None

    # Timing
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    duration_ms: int = 0

    # Resource tracking
    files_read: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)

    # AI tracking (course requirement)
    is_ai_generated: bool = False
    ai_task_id: Optional[str] = None
    reviewer: Optional[str] = None

    def finish(self, success: bool, error: Optional[str] = None) -> None:
        """Mark execution as finished and calculate duration."""
        self.finished_at = datetime.now()
        self.success = success
        self.error_message = error
        if self.started_at and self.finished_at:
            delta = self.finished_at - self.started_at
            self.duration_ms = int(delta.total_seconds() * 1000)


@dataclass
class SkillExecutionResult:
    """Skill execution result wrapper."""
    log: SkillExecutionLog
    success: bool
    output: Any = None
    output_type: str = "text"
    suggestions: List[str] = field(default_factory=list)


# Permission to tool mapping
PERMISSION_TO_TOOL_MAP: Dict[SkillPermissionLevel, str] = {
    SkillPermissionLevel.READ_ONLY: "read_file",
    SkillPermissionLevel.MODIFY_LOCAL: "write_file",
    SkillPermissionLevel.EXECUTE_COMMAND: "terminal",
    SkillPermissionLevel.NETWORK_ACCESS: "web_fetch",
    SkillPermissionLevel.FULL_ACCESS: "git",  # Full access requires git (highest privilege)
}
