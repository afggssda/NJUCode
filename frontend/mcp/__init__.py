"""MCP module for NJUCode - Model Context Protocol client integration.

This module provides:
- MCPClient: Async client wrapper for MCP SDK
- MCPManager: Server connection orchestration
- MCPToolExecutor: Tool execution with permission and audit
"""

from .models import (
    MCPConnectionState,
    MCPServerConfig,
    MCPToolInfo,
    MCPToolToggle,
    MCPTransportType,
)
from .client import MCPClient
from .manager import MCPManager
from .executor import MCPToolExecutor

__all__ = [
    "MCPClient",
    "MCPManager",
    "MCPToolExecutor",
    "MCPConnectionState",
    "MCPServerConfig",
    "MCPToolInfo",
    "MCPToolToggle",
    "MCPTransportType",
]
