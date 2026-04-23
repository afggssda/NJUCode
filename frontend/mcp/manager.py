"""MCP Manager - Orchestrates multiple MCP server connections.

Pattern mirrors SkillRegistry: registration, lifecycle, persistence.
Handles server configuration loading/saving, connection management,
and tool discovery.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .client import MCPClient
from .models import MCPServerConfig, MCPConnectionState, MCPTransportType, MCPToolInfo, MCPToolToggle
from .tool_adapter import MCPToolAdapter


class MCPManager:
    """Central manager for MCP server connections.

    Responsibilities:
    1. Server configuration loading/saving to settings.json
    2. Connection lifecycle management (connect/disconnect)
    3. Tool discovery and registration
    4. Tool toggle state management

    Pattern mirrors SkillRegistry with async support.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self._settings_path = workspace_root / ".nju_code" / "settings.json"

        self.servers: Dict[str, MCPServerConfig] = {}
        self.clients: Dict[str, MCPClient] = {}
        self.tool_toggles: Dict[str, MCPToolToggle] = {}

        self._tool_adapter = MCPToolAdapter()

    def load(self) -> None:
        """Load server configurations from settings.json.

        Pattern mirrors SkillRegistry.load() at lines 295-314.
        """
        if not self._settings_path.exists():
            return

        try:
            with open(self._settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            return

        mcp_data = settings.get("mcp", {})

        # Load server configs
        servers_data = mcp_data.get("servers", {})
        for server_id, data in servers_data.items():
            transport = MCPTransportType(data.get("transport", "stdio"))
            config = MCPServerConfig(
                server_id=server_id,
                name=data.get("name", server_id),
                transport=transport,
                command=data.get("command", ""),
                args=data.get("args", []),
                env=data.get("env", {}),
                url=data.get("url", ""),
                headers=data.get("headers", {}),
                description=data.get("description", ""),
                enabled=data.get("enabled", True),
                auto_connect=data.get("auto_connect", True),
            )
            self.servers[server_id] = config

        # Load tool toggles (will be populated after connection)
        tools_data = mcp_data.get("tools", {})
        self._saved_tool_states = tools_data  # Store for later application

    def save(self) -> None:
        """Save configurations to settings.json.

        Pattern mirrors SkillRegistry.save() at lines 266-293.
        """
        # Load existing settings
        settings: Dict[str, Any] = {}
        if self._settings_path.exists():
            try:
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except Exception:
                pass

        # Add MCP section
        settings["mcp"] = {
            "servers": {
                server_id: {
                    "server_id": config.server_id,
                    "name": config.name,
                    "transport": config.transport.value,
                    "command": config.command,
                    "args": config.args,
                    "env": config.env,
                    "url": config.url,
                    "headers": config.headers,
                    "description": config.description,
                    "enabled": config.enabled,
                    "auto_connect": config.auto_connect,
                }
                for server_id, config in self.servers.items()
            },
            "tools": {
                skill_id: {
                    "enabled": toggle.enabled,
                    "usage_count": toggle.usage_count,
                }
                for skill_id, toggle in self.tool_toggles.items()
            }
        }

        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._settings_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[MCPManager] Failed to save: {e}")

    async def connect_server(self, server_id: str) -> bool:
        """Connect to a specific server.

        Pattern mirrors SkillRegistry.register_skill().
        Creates client, connects, and registers discovered tools.

        Args:
            server_id: Server to connect

        Returns:
            True if connected successfully
        """
        if server_id not in self.servers:
            return False

        config = self.servers[server_id]
        if not config.enabled:
            return False

        # Disconnect existing client if present
        if server_id in self.clients:
            await self.clients[server_id].disconnect()

        # Create new client
        client = MCPClient(config)
        self.clients[server_id] = client

        # Connect
        success = await client.connect()
        if not success:
            # Remove failed client from dict
            del self.clients[server_id]
            return False

        # Register discovered tools
        for tool_info in client.tools.values():
            # Create toggle with saved state
            saved_state = getattr(self, '_saved_tool_states', {})
            saved_tool = saved_state.get(tool_info.skill_id, {})

            toggle = MCPToolToggle(
                tool_info=tool_info,
                enabled=saved_tool.get("enabled", True),
                usage_count=saved_tool.get("usage_count", 0),
            )
            self.tool_toggles[tool_info.skill_id] = toggle

            # Convert to SkillManifest
            tool_info.skill_manifest = self._tool_adapter.convert_to_manifest(
                tool_info, config
            )

        return True

    async def disconnect_server(self, server_id: str) -> None:
        """Disconnect from a server.

        Removes client and associated tool toggles.
        """
        if server_id in self.clients:
            await self.clients[server_id].disconnect()
            del self.clients[server_id]

        # Remove tool toggles for this server
        to_remove = [
            skill_id for skill_id in self.tool_toggles
            if skill_id.startswith(f"mcp.{server_id}.")
        ]
        for skill_id in to_remove:
            del self.tool_toggles[skill_id]

    async def connect_all_enabled(self) -> Dict[str, bool]:
        """Connect all enabled auto-connect servers.

        Pattern mirrors SkillRegistry.load_plugins().
        """
        results = {}
        for server_id, config in self.servers.items():
            if config.enabled and config.auto_connect:
                results[server_id] = await self.connect_server(server_id)
        return results

    async def disconnect_all(self) -> None:
        """Disconnect all servers."""
        for server_id in list(self.clients.keys()):
            await self.disconnect_server(server_id)

    def get_tool(self, skill_id: str) -> Optional[MCPToolToggle]:
        """Get tool toggle by skill_id."""
        return self.tool_toggles.get(skill_id)

    def get_client(self, server_id: str) -> Optional[MCPClient]:
        """Get client by server_id."""
        return self.clients.get(server_id)

    def update_tool_status(self, skill_id: str, enabled: bool) -> None:
        """Update tool enabled status.

        Pattern mirrors SkillRegistry.update_skill_status().
        """
        if skill_id in self.tool_toggles:
            self.tool_toggles[skill_id].enabled = enabled

    def increment_usage(self, skill_id: str) -> None:
        """Increment tool usage counter."""
        if skill_id in self.tool_toggles:
            self.tool_toggles[skill_id].usage_count += 1
            self.tool_toggles[skill_id].last_used = datetime.now()

    def add_server(self, config: MCPServerConfig) -> None:
        """Add a new server configuration."""
        self.servers[config.server_id] = config
        self.save()

    def remove_server(self, server_id: str) -> bool:
        """Remove a server configuration."""
        if server_id not in self.servers:
            return False
        del self.servers[server_id]
        self.save()
        return True

    def update_server_status(self, server_id: str, enabled: bool) -> None:
        """Update server enabled status."""
        if server_id in self.servers:
            self.servers[server_id].enabled = enabled

    def list_tools(self, enabled_only: bool = False) -> List[MCPToolToggle]:
        """List all MCP tools with optional filter."""
        result = []
        for toggle in self.tool_toggles.values():
            if enabled_only and not toggle.enabled:
                continue
            result.append(toggle)
        return sorted(result, key=lambda t: t.tool_info.skill_id)

    def list_servers(self, enabled_only: bool = False) -> List[MCPServerConfig]:
        """List server configurations."""
        result = []
        for config in self.servers.values():
            if enabled_only and not config.enabled:
                continue
            result.append(config)
        return sorted(result, key=lambda c: c.name)