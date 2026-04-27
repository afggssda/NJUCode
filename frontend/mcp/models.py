"""MCP data models for NJUCode.

This module defines core data structures for MCP integration:
- MCPServerConfig: Server connection configuration
- MCPConnectionState: Connection status enum
- MCPTransportType: Transport method enum (stdio/http)
- MCPToolInfo: Discovered tool information
- MCPToolToggle: Tool enable/disable state
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


class MCPTransportType(Enum):
    """MCP server transport type."""
    STDIO = "stdio"      # Local process via stdin/stdout
    HTTP = "http"        # Remote HTTP/SSE transport


class MCPConnectionState(Enum):
    """MCP server connection state."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection.

    Supports both stdio (local) and HTTP (remote) transports.
    """
    server_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""                    # Display name: "FileSystem Server"
    transport: MCPTransportType = MCPTransportType.STDIO

    # Stdio transport config
    command: str = ""                 # Executable: "uvx", "python"
    args: List[str] = field(default_factory=list)  # Arguments
    env: Dict[str, str] = field(default_factory=dict)  # Environment vars

    # HTTP transport config
    url: str = ""                     # HTTP endpoint URL
    headers: Dict[str, str] = field(default_factory=dict)

    # Server metadata
    description: str = ""
    enabled: bool = True
    auto_connect: bool = True         # Connect on app startup

    # Connection state (runtime)
    connection_state: MCPConnectionState = MCPConnectionState.DISCONNECTED
    last_error: Optional[str] = None
    connected_at: Optional[datetime] = None

    # Tool tracking
    discovered_tools: List[str] = field(default_factory=list)


@dataclass
class MCPToolInfo:
    """Information about an MCP tool discovered from server.

    Contains the original MCP tool schema. MCP tools stay in the tool layer;
    agent skills can reference them but they are not agent skills themselves.
    """
    tool_name: str                    # Original MCP tool name
    server_id: str                    # Source server
    skill_id: str                     # Mapped skill_id: "mcp.{server}.{tool}"

    # MCP tool schema
    input_schema: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    # Optional metadata reserved for future UI/tool adapters.
    tool_metadata: Optional[Any] = None


@dataclass
class MCPToolToggle:
    """Toggle state for an MCP tool.

    Pattern mirrors SkillToggle for UI compatibility.
    """
    tool_info: MCPToolInfo
    enabled: bool = True
    usage_count: int = 0
    last_used: Optional[datetime] = None

    @property
    def label(self) -> str:
        """Alias for UI compatibility."""
        return self.tool_info.tool_name

    @property
    def description(self) -> str:
        """Alias for UI compatibility."""
        return self.tool_info.description
