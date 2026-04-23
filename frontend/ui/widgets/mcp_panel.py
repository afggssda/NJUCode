"""MCP Panel - UI for MCP server and tool management.

Pattern mirrors SkillsPanel with server connection status.
Provides:
- Server list with connect/disconnect buttons
- Tool list with enable/disable toggles
- Statistics display
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Label, ListView, ListItem, Static

from frontend.mcp.models import MCPServerConfig, MCPConnectionState, MCPToolToggle


class MCPServerConnectRequested(Message):
    """Event: user requests to connect/disconnect server."""

    def __init__(self, server_id: str, connect: bool) -> None:
        self.server_id = server_id
        self.connect = connect  # True = connect, False = disconnect
        super().__init__()


class MCPServerAddRequested(Message):
    """Event: user wants to add new server config."""
    pass


class MCPToolToggled(Message):
    """Event: MCP tool toggle changed."""

    def __init__(self, skill_id: str, enabled: bool) -> None:
        self.skill_id = skill_id
        self.enabled = enabled
        super().__init__()


class ServerListItem(ListItem):
    """List item for MCP server."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        super().__init__()

    def compose(self) -> ComposeResult:
        config = self.config
        state_icon = {
            MCPConnectionState.CONNECTED: "●",
            MCPConnectionState.CONNECTING: "◐",
            MCPConnectionState.DISCONNECTED: "○",
            MCPConnectionState.ERROR: "✗",
        }
        icon = state_icon.get(config.connection_state, "○")
        color_class = f"state-{config.connection_state.value}"

        yield Horizontal(
            Static(icon, classes=color_class),
            Static(config.name, classes="server-name"),
            Static(f"[{config.transport.value}]", classes="server-transport"),
            Button(
                "Connect" if config.connection_state != MCPConnectionState.CONNECTED
                else "Disconnect",
                id=f"connect_{config.server_id}",
                classes="connect-btn",
            ),
            classes="server-item",
        )


class ToolListItem(ListItem):
    """List item for MCP tool."""

    def __init__(self, toggle: MCPToolToggle) -> None:
        self.toggle = toggle
        super().__init__()

    def compose(self) -> ComposeResult:
        toggle = self.toggle
        info = toggle.tool_info
        status_icon = "●" if toggle.enabled else "○"
        status_class = "tool-enabled" if toggle.enabled else "tool-disabled"

        yield Horizontal(
            Static(status_icon, classes=status_class),
            Static(info.skill_id, classes="tool-name"),
            Static(f"({toggle.usage_count})", classes="tool-usage"),
            classes="tool-item",
        )


class MCPPanel(Vertical):
    """MCP server and tool management panel.

    Pattern mirrors SkillsPanel with:
    - Server list with connection status
    - Tool list with enable/disable toggles
    - Add server button
    - Statistics display
    """

    DEFAULT_CSS = """
    MCPPanel {
        width: 100%;
        height: 100%;
        padding: 1;
    }

    .panel-title {
        text-style: bold;
        margin-bottom: 1;
    }

    .sub-title {
        text-style: bold underline;
        margin-top: 1;
        margin-bottom: 0;
        color: $text-muted;
    }

    .mcp-actions {
        height: auto;
        margin-bottom: 1;
    }

    .mcp-actions Button {
        margin-right: 1;
    }

    ListView {
        height: auto;
        max-height: 15;
    }

    .server-item {
        height: 1;
    }

    .tool-item {
        height: 1;
    }

    .state-connected { color: $success; }
    .state-connecting { color: $warning; }
    .state-disconnected { color: $text-muted; }
    .state-error { color: $error; }

    .server-name { margin-left: 1; }
    .server-transport { margin-left: 1; color: $text-muted; }
    .connect-btn { margin-left: 1; width: auto; }

    .tool-enabled { color: $success; }
    .tool-disabled { color: $text-muted; }
    .tool-name { margin-left: 1; }
    .tool-usage { margin-left: 1; color: $accent; }
    """

    servers: reactive[list[MCPServerConfig]] = reactive(list)
    tools: reactive[list[MCPToolToggle]] = reactive(list)

    def compose(self) -> ComposeResult:
        yield Label("MCP Servers", classes="panel-title")

        with Horizontal(classes="mcp-actions"):
            yield Button("Add Server", id="add_server_btn", variant="primary")
            yield Button("Connect All", id="connect_all_btn")
            yield Button("Disconnect All", id="disconnect_all_btn", variant="warning")

        yield Label("Server Connections", classes="sub-title")
        yield ListView(id="servers_list")

        yield Label("MCP Tools", classes="sub-title")
        yield ListView(id="tools_list")

        yield Label("", id="mcp_stats")

    def on_mount(self) -> None:
        """Initialize the panel."""
        self._refresh_lists()
        self._update_stats()

    def refresh_servers(self, servers: list[MCPServerConfig]) -> None:
        """Refresh server list display.

        Args:
            servers: List of MCPServerConfig from state
        """
        self.servers = servers
        servers_list = self.query_one("#servers_list", ListView)
        servers_list.clear()
        for config in servers:
            servers_list.append(ServerListItem(config))
        self._update_stats()

    def refresh_tools(self, tools: list[MCPToolToggle]) -> None:
        """Refresh tool list display.

        Args:
            tools: List of MCPToolToggle from state
        """
        self.tools = tools
        tools_list = self.query_one("#tools_list", ListView)
        tools_list.clear()
        for toggle in tools:
            tools_list.append(ToolListItem(toggle))

    def _refresh_lists(self) -> None:
        """Update list views with current data."""
        servers_list = self.query_one("#servers_list", ListView)
        tools_list = self.query_one("#tools_list", ListView)

        servers_list.clear()
        tools_list.clear()

        for config in self.servers:
            servers_list.append(ServerListItem(config))

        for toggle in self.tools:
            tools_list.append(ToolListItem(toggle))

    def _update_stats(self) -> None:
        """Update statistics display."""
        total_servers = len(self.servers)
        connected_servers = sum(
            1 for s in self.servers
            if s.connection_state == MCPConnectionState.CONNECTED
        )
        total_tools = len(self.tools)
        enabled_tools = sum(1 for t in self.tools if t.enabled)

        stats = self.query_one("#mcp_stats", Label)
        stats.update(
            f"Servers: {connected_servers}/{total_servers} connected | "
            f"Tools: {enabled_tools}/{total_tools} enabled"
        )

    @on(Button.Pressed, "#add_server_btn")
    def on_add_server(self) -> None:
        """Handle add server button."""
        self.post_message(MCPServerAddRequested())

    @on(Button.Pressed, "#connect_all_btn")
    def on_connect_all(self) -> None:
        """Handle connect all button."""
        for server in self.servers:
            if server.enabled and server.connection_state != MCPConnectionState.CONNECTED:
                self.post_message(MCPServerConnectRequested(server.server_id, True))

    @on(Button.Pressed, "#disconnect_all_btn")
    def on_disconnect_all(self) -> None:
        """Handle disconnect all button."""
        for server in self.servers:
            if server.connection_state == MCPConnectionState.CONNECTED:
                self.post_message(MCPServerConnectRequested(server.server_id, False))

    @on(Button.Pressed, ".connect-btn")
    def on_server_connect_btn(self, event: Button.Pressed) -> None:
        """Handle individual server connect/disconnect button."""
        btn_id = event.button.id or ""
        if btn_id.startswith("connect_"):
            server_id = btn_id.replace("connect_", "")
            server = next((s for s in self.servers if s.server_id == server_id), None)
            if server:
                connect = server.connection_state != MCPConnectionState.CONNECTED
                self.post_message(MCPServerConnectRequested(server_id, connect))

    @on(ListView.Selected, "#tools_list")
    def on_tool_selected(self, event: ListView.Selected) -> None:
        """Handle tool selection - toggle enable/disable."""
        if isinstance(event.item, ToolListItem):
            toggle = event.item.toggle
            new_enabled = not toggle.enabled
            self.post_message(MCPToolToggled(toggle.tool_info.skill_id, new_enabled))

    @on(ListView.Selected, "#servers_list")
    def on_server_selected(self, event: ListView.Selected) -> None:
        """Handle server selection - toggle connect/disconnect."""
        if isinstance(event.item, ServerListItem):
            config = event.item.config
            connect = config.connection_state != MCPConnectionState.CONNECTED
            self.post_message(MCPServerConnectRequested(config.server_id, connect))