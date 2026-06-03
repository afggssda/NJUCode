"""MCP Client - Async wrapper for MCP SDK with lifecycle management.

Pattern mirrors SkillExecutor: async init, connection, execution, cleanup.
Uses AsyncExitStack for proper resource management.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .models import MCPServerConfig, MCPConnectionState, MCPTransportType, MCPToolInfo


class MCPClient:
    """Async MCP client with proper lifecycle management.

    Each client instance manages one server connection.
    Uses AsyncExitStack pattern from MCP SDK for clean resource cleanup.

    Usage:
        client = MCPClient(config)
        await client.connect()
        tools = client.tools
        result = await client.call_tool("read_file", {"path": "test.py"})
        await client.disconnect()
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None
        self._tools: Dict[str, MCPToolInfo] = {}
        self._connection_state = MCPConnectionState.DISCONNECTED

    async def connect(self) -> bool:
        """Establish connection to MCP server.

        Returns:
            True if connected successfully, False on error
        """
        if self._session is not None:
            return True

        self._connection_state = MCPConnectionState.CONNECTING
        self.config.connection_state = MCPConnectionState.CONNECTING

        try:
            self._exit_stack = AsyncExitStack()

            if self.config.transport == MCPTransportType.STDIO:
                server_params = StdioServerParameters(
                    command=self.config.command,
                    args=self.config.args,
                    env=self.config.env or None,
                )

                # Enter async context managers in correct order
                read, write = await self._exit_stack.enter_async_context(
                    stdio_client(server_params)
                )
                self._session = await self._exit_stack.enter_async_context(
                    ClientSession(read, write)
                )

            elif self.config.transport == MCPTransportType.HTTP:
                # HTTP transport not yet implemented
                # Would use mcp.client.http module
                raise NotImplementedError("HTTP transport not implemented")

            else:
                raise ValueError(f"Unknown transport: {self.config.transport}")

            # Initialize session
            await self._session.initialize()

            # Discover tools
            await self._discover_tools()

            self._connection_state = MCPConnectionState.CONNECTED
            self.config.connection_state = MCPConnectionState.CONNECTED
            self.config.connected_at = datetime.now()
            self.config.last_error = None

            return True

        except Exception as e:
            self._connection_state = MCPConnectionState.ERROR
            self.config.connection_state = MCPConnectionState.ERROR
            self.config.last_error = str(e)
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        """Clean disconnect from server.

        Releases all resources via AsyncExitStack.
        """
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
            self._exit_stack = None
            self._session = None

        self._connection_state = MCPConnectionState.DISCONNECTED
        self.config.connection_state = MCPConnectionState.DISCONNECTED
        self._tools.clear()
        self.config.discovered_tools.clear()

    async def _discover_tools(self) -> List[MCPToolInfo]:
        """Discover available tools from server.

        Pattern mirrors SkillRegistry.register_skill().
        Caches tools in self._tools dict.

        Returns:
            List of discovered MCPToolInfo objects
        """
        if not self._session:
            return []

        tools_response = await self._session.list_tools()

        self._tools.clear()
        discovered = []

        for tool in tools_response.tools:
            skill_id = f"mcp.{self.config.server_id}.{tool.name}"

            tool_info = MCPToolInfo(
                tool_name=tool.name,
                server_id=self.config.server_id,
                skill_id=skill_id,
                input_schema=tool.inputSchema or {},
                description=tool.description or "",
            )

            self._tools[tool.name] = tool_info
            discovered.append(tool_info)
            self.config.discovered_tools.append(tool.name)

        return discovered

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute a tool call on the MCP server.

        Pattern mirrors SkillExecutor.execute().

        Args:
            tool_name: MCP tool name
            arguments: Tool input arguments

        Returns:
            Tool execution result (usually text or dict)

        Raises:
            RuntimeError: If not connected or tool execution fails
        """
        if not self._session:
            raise RuntimeError(f"Not connected to server {self.config.name}")

        result = await self._session.call_tool(tool_name, arguments)

        # Handle different content types
        if result.isError:
            error_msg = result.errorMessage or "Tool execution failed"
            raise RuntimeError(error_msg)

        # Extract content from result
        content = result.content
        if isinstance(content, list):
            # Concatenate text content blocks
            texts = []
            for item in content:
                if hasattr(item, 'text') and item.text:
                    texts.append(item.text)
                elif hasattr(item, 'data'):
                    texts.append(str(item.data))
            return "\n".join(texts) if texts else content

        return content

    async def list_resources(self) -> List[Any]:
        """List available resources from server."""
        if not self._session:
            return []
        response = await self._session.list_resources()
        return response.resources

    async def read_resource(self, uri: str) -> Any:
        """Read a resource from server."""
        if not self._session:
            raise RuntimeError("Not connected")
        result = await self._session.read_resource(uri)
        return result.contents

    @property
    def tools(self) -> Dict[str, MCPToolInfo]:
        """Available tools from this server."""
        return self._tools

    @property
    def connection_state(self) -> MCPConnectionState:
        """Current connection state."""
        return self._connection_state

    @property
    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connection_state == MCPConnectionState.CONNECTED