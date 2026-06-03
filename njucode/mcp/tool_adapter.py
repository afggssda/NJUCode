"""MCP Tool Adapter - Converts MCP tool definitions to SkillManifest.

Bridges external MCP tools into internal Skills system for:
- SkillExecutor execution compatibility
- PermissionChecker validation
- AuditLogger tracking
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import MCPServerConfig, MCPToolInfo


class MCPToolAdapter:
    """Converts MCP tool definitions to SkillManifest format.

    MCP tools use JSON Schema for input validation.
    This adapter maps them to SkillParameter format.
    """

    def convert_to_manifest(
        self,
        tool_info: MCPToolInfo,
        server_config: MCPServerConfig,
    ) -> Dict[str, Any]:
        """Convert MCPToolInfo to SkillManifest-compatible dict.

        Returns a dict rather than SkillManifest to avoid circular import.
        The dict can be used by executor without full SkillManifest class.

        Args:
            tool_info: MCP tool information
            server_config: Source server configuration

        Returns:
            Dict with skill manifest fields
        """
        # Parse input schema to parameters
        parameters = self._parse_input_schema(tool_info.input_schema)

        # Infer permissions from tool name patterns
        permissions = self._infer_permissions(tool_info.tool_name)

        return {
            "skill_id": tool_info.skill_id,
            "name": f"{server_config.name}: {tool_info.tool_name}",
            "version": "1.0.0",
            "description": tool_info.description or f"MCP tool from {server_config.name}",
            "category": "mcp",
            "parameters": parameters,
            "output": {
                "type": "json",
                "description": "MCP tool output",
            },
            "permissions": permissions,
            "dependencies": [],
            "command_aliases": [f"/mcp.{tool_info.tool_name}"],
            "author": f"MCP Server: {server_config.name}",
            "is_builtin": False,
        }

    def _parse_input_schema(
        self,
        schema: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Parse JSON Schema input to parameter dicts.

        MCP tools use JSON Schema for input validation.
        """
        parameters = []

        if not schema:
            return parameters

        properties = schema.get("properties", {})
        required = schema.get("required", [])

        for prop_name, prop_def in properties.items():
            prop_type = prop_def.get("type", "string")

            # Map JSON Schema types to SkillParameter types
            type_map = {
                "string": "string",
                "integer": "integer",
                "number": "integer",  # Approximate
                "boolean": "boolean",
                "array": "list",
                "object": "string",  # JSON string
            }

            skill_type = type_map.get(prop_type, "string")

            parameters.append({
                "name": prop_name,
                "type": skill_type,
                "required": prop_name in required,
                "default": prop_def.get("default"),
                "description": prop_def.get("description", ""),
            })

        return parameters

    def _infer_permissions(
        self,
        tool_name: str,
    ) -> List[str]:
        """Infer permissions from tool name patterns.

        Common patterns:
        - read_file, get_*, list_* -> read_only
        - write_file, create_*, update_* -> modify_local
        - execute_*, run_* -> execute_command
        - fetch_*, request_* -> network_access
        """
        name_lower = tool_name.lower()

        if any(p in name_lower for p in ["read", "get", "list", "search", "find"]):
            return ["read_only"]
        elif any(p in name_lower for p in ["write", "create", "update", "delete", "modify"]):
            return ["read_only", "modify_local"]
        elif any(p in name_lower for p in ["execute", "run", "shell", "command"]):
            return ["execute_command"]
        elif any(p in name_lower for p in ["fetch", "request", "http", "web", "api"]):
            return ["network_access"]

        return ["read_only"]  # Default safe