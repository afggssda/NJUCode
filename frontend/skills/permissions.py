"""Permission Checker - Validates skill permissions against tool toggles.

This module provides:
- Permission level validation
- Skill-to-tool permission mapping
- Permission grant/revoke operations
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .models import (
    SkillPermissionLevel,
    SkillToggle,
    PERMISSION_TO_TOOL_MAP,
)


class PermissionChecker:
    """Permission checker for skill execution.

    Maps skill permission levels to existing ToolToggle system.
    """

    def __init__(
        self,
        tools: Dict[str, "ToolToggle"],  # From AppState.tools
        skills: Dict[str, SkillToggle],
    ) -> None:
        self.tools = tools
        self.skills = skills

    def check_permission(
        self,
        skill_id: str,
        required_permissions: List[SkillPermissionLevel],
    ) -> Tuple[bool, Optional[str]]:
        """Check if skill has required permissions.

        Args:
            skill_id: Skill to check
            required_permissions: Required permission levels

        Returns:
            (has_permission, missing_permission_description)
        """
        missing = []

        for perm in required_permissions:
            tool_key = PERMISSION_TO_TOOL_MAP.get(perm)
            if not tool_key:
                continue

            if tool_key not in self.tools:
                missing.append(f"Tool '{tool_key}' not registered")
                continue

            if not self.tools[tool_key].enabled:
                missing.append(f"Tool '{tool_key}' is disabled")

        if missing:
            return False, "Missing permissions: " + ", ".join(missing)

        return True, None

    def check_tool_permission(self, tool_key: str) -> bool:
        """Check if a specific tool is enabled.

        Args:
            tool_key: Tool to check (read_file, write_file, etc.)

        Returns:
            True if tool is enabled
        """
        if tool_key not in self.tools:
            return False
        return self.tools[tool_key].enabled

    def map_skill_to_tools(
        self,
        skill_permissions: List[SkillPermissionLevel],
    ) -> List[str]:
        """Map skill permissions to tool keys.

        Args:
            skill_permissions: Permission levels

        Returns:
            List of required tool keys
        """
        tools = []
        for perm in skill_permissions:
            tool_key = PERMISSION_TO_TOOL_MAP.get(perm)
            if tool_key:
                tools.append(tool_key)
        return tools

    def get_required_tools_for_skill(
        self,
        skill_id: str,
    ) -> List[str]:
        """Get tools required by a skill.

        Args:
            skill_id: Skill to analyze

        Returns:
            List of required tool keys
        """
        if skill_id not in self.skills:
            return []

        manifest = self.skills[skill_id].manifest
        return self.map_skill_to_tools(manifest.permissions)

    def get_missing_tools_for_skill(
        self,
        skill_id: str,
    ) -> List[str]:
        """Get tools that are required but disabled.

        Args:
            skill_id: Skill to analyze

        Returns:
            List of disabled tool keys
        """
        required = self.get_required_tools_for_skill(skill_id)
        missing = []

        for tool_key in required:
            if tool_key in self.tools and not self.tools[tool_key].enabled:
                missing.append(tool_key)

        return missing

    def can_execute_skill(self, skill_id: str) -> Tuple[bool, str]:
        """Quick check if skill can be executed.

        Args:
            skill_id: Skill to check

        Returns:
            (can_execute, reason_if_not)
        """
        if skill_id not in self.skills:
            return False, f"Skill '{skill_id}' not found"

        if not self.skills[skill_id].enabled:
            return False, f"Skill '{skill_id}' is disabled"

        missing_tools = self.get_missing_tools_for_skill(skill_id)
        if missing_tools:
            return False, f"Required tools disabled: {missing_tools}"

        return True, ""