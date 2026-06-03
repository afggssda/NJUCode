"""UI widgets package for NJUCode."""

from .chat_panel import ChatPanel, MessageSubmitted, StreamInterruptRequested
from .code_viewer_panel import CodeViewerPanel, FileContextAdded
from .config_panel import ConfigPanel, ConfigSaved, MirrorSelected
from .file_tree_panel import FileTreePanel, WorkspaceChanged
from .session_panel import (
    SessionCreateRequested,
    SessionDeleteRequested,
    SessionPanel,
    SessionRenameRequested,
    SessionSelected,
)
from .splitter import SplitterDragEnded, SplitterDragged, VerticalSplitter
from .tools_panel import (
    HelloWorldRequested,
    ToolToggled,
    ToolsPanel,
    AnalysisCommandRequested,
    SkillExecutionRequested,
)
from .skills_panel import (
    SkillsPanel,
    SkillToggled,
    PluginInstallRequested,
    AuditLogRequested,
)
from .mcp_panel import (
    MCPPanel,
    MCPServerConnectRequested,
    MCPServerAddRequested,
    MCPToolToggled,
)

__all__ = [
    "ChatPanel",
    "MessageSubmitted",
    "StreamInterruptRequested",
    "CodeViewerPanel",
    "FileContextAdded",
    "ConfigPanel",
    "ConfigSaved",
    "MirrorSelected",
    "FileTreePanel",
    "WorkspaceChanged",
    "SessionCreateRequested",
    "SessionDeleteRequested",
    "SessionPanel",
    "SessionRenameRequested",
    "SessionSelected",
    "SplitterDragEnded",
    "SplitterDragged",
    "VerticalSplitter",
    "HelloWorldRequested",
    "ToolToggled",
    "ToolsPanel",
    "AnalysisCommandRequested",
    "SkillExecutionRequested",
    "SkillsPanel",
    "SkillToggled",
    "PluginInstallRequested",
    "AuditLogRequested",
    "MCPPanel",
    "MCPServerConnectRequested",
    "MCPServerAddRequested",
    "MCPToolToggled",
]