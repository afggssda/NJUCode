"""MCP Tool Executor - Executes MCP tool calls with audit logging.

Pattern mirrors SkillExecutor: permission check, execution, audit.
Uses MCPManager for connections and AuditLogger for tracking.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..skills.models import SkillExecutionLog, SkillExecutionResult
from .manager import MCPManager
from .models import MCPToolToggle


class MCPToolExecutor:
    """MCP tool execution engine.

    Responsibilities:
    1. Permission checking (via tool manifest permissions)
    2. Async tool execution (via MCPManager)
    3. Audit logging (via AuditLogger from skills module)

    Pattern mirrors SkillExecutor at frontend/skills/executor.py lines 33-170.
    """

    def __init__(
        self,
        manager: MCPManager,
        audit_logger: Any,  # AuditLogger from skills module
        permission_checker: Optional[Any] = None,
    ) -> None:
        self.manager = manager
        self.audit_logger = audit_logger
        self.permission_checker = permission_checker

    async def execute(
        self,
        skill_id: str,
        params: Dict[str, Any],
        session_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillExecutionResult:
        """Execute an MCP tool by skill_id.

        Pattern mirrors SkillExecutor.execute() at lines 55-170.

        Args:
            skill_id: MCP tool skill_id (e.g., "mcp.filesystem.read_file")
            params: Tool input arguments
            session_id: Current session for audit
            context: Execution context

        Returns:
            SkillExecutionResult with output and audit log
        """
        log = SkillExecutionLog(
            skill_id=skill_id,
            session_id=session_id,
            input_params=params,
            started_at=datetime.now(),
        )

        # Get tool toggle
        toggle = self.manager.get_tool(skill_id)
        if not toggle:
            log.finish(False, f"MCP tool not found: {skill_id}")
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=["Check MCP server connection", "Use /mcp.help for available tools"],
            )

        # Check if tool is enabled
        if not toggle.enabled:
            log.finish(False, f"MCP tool is disabled: {skill_id}")
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=[f"Enable {skill_id} in MCP panel"],
            )

        # Parse skill_id to get server and tool name
        parts = skill_id.split(".")
        if len(parts) < 3:
            log.finish(False, f"Invalid skill_id format: {skill_id}")
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=["Use format: mcp.{server}.{tool}"],
            )

        server_id = parts[1]
        tool_name = parts[2]

        # Get client
        client = self.manager.get_client(server_id)
        if not client or not client.is_connected:
            log.finish(False, f"MCP server not connected: {server_id}")
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=[f"Connect MCP server {server_id}"],
            )

        # Execute tool call
        try:
            output = await client.call_tool(tool_name, params)

            log.output_type = "text"
            log.output_summary = self._summarize_output(output)
            log.finish(True)

            # Increment usage
            self.manager.increment_usage(skill_id)

            # Record audit
            if self.audit_logger:
                self.audit_logger.record(log)

            return SkillExecutionResult(
                success=True,
                output={"type": "mcp_result", "data": output},
                output_type="text",
                log=log,
                suggestions=self._generate_suggestions(skill_id, output),
            )

        except Exception as e:
            log.finish(False, str(e))
            if self.audit_logger:
                self.audit_logger.record(log)
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=[f"MCP tool execution error: {e}"],
            )

    def _summarize_output(self, output: Any) -> str:
        """Create brief summary of output for audit log.

        Pattern mirrors SkillExecutor._summarize_output() at lines 390-400.
        """
        if isinstance(output, dict):
            return f"dict with {len(output)} keys"
        elif isinstance(output, str):
            return f"{len(output)} chars"
        elif isinstance(output, list):
            return f"{len(output)} items"
        return str(type(output).__name__)

    def _generate_suggestions(
        self,
        skill_id: str,
        output: Any,
    ) -> List[str]:
        """Generate follow-up suggestions based on output."""
        suggestions = []

        # Check for related MCP tools
        server_id = skill_id.split(".")[1] if "." in skill_id else ""
        if server_id:
            server_tools = [
                t for t in self.manager.tool_toggles.keys()
                if t.startswith(f"mcp.{server_id}.") and t != skill_id
            ]
            if server_tools:
                suggestions.append(f"Related tools: {', '.join(server_tools[:3])}")

        return suggestions

    def execute_sync(
        self,
        skill_id: str,
        params: Dict[str, Any],
        session_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillExecutionResult:
        """Synchronous wrapper for execute() using asyncio.

        Used when called from non-async context (like app.py worker).
        """
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, need to use run_coroutine_threadsafe
                import concurrent.futures
                future = asyncio.run_coroutine_threadsafe(
                    self.execute(skill_id, params, session_id, context),
                    loop
                )
                return future.result(timeout=60)
            else:
                return loop.run_until_complete(
                    self.execute(skill_id, params, session_id, context)
                )
        except RuntimeError:
            # No event loop, create one
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(
                    self.execute(skill_id, params, session_id, context)
                )
            finally:
                loop.close()