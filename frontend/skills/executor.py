"""Skill Executor - Handles skill execution, validation, and result formatting.

This module provides:
- Parameter validation and normalization
- Permission checking before execution
- Skill execution (builtin and plugin)
- Result formatting for chat display
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    SkillExecutionLog,
    SkillExecutionResult,
    SkillManifest,
    SkillParameter,
    SkillPermissionLevel,
    SkillToggle,
)
from .registry import SkillRegistry
from .permissions import PermissionChecker
from .audit_log import AuditLogger


class SkillExecutor:
    """Skill execution engine.

    Responsible for:
    1. Parameter validation
    2. Permission checking
    3. Skill invocation (builtin or plugin)
    4. Audit logging
    """

    def __init__(
        self,
        registry: SkillRegistry,
        permission_checker: PermissionChecker,
        audit_logger: AuditLogger,
        analyzer: Any,  # CodeAnalyzer reference
    ) -> None:
        self.registry = registry
        self.permission_checker = permission_checker
        self.audit_logger = audit_logger
        self.analyzer = analyzer

    def execute(
        self,
        skill_id: str,
        params: Dict[str, Any],
        session_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillExecutionResult:
        """Execute a skill by ID.

        Args:
            skill_id: Skill to execute
            params: Input parameters
            session_id: Current session ID for audit
            context: Execution context (workspace_root, etc.)

        Returns:
            SkillExecutionResult with output and audit log
        """
        log = SkillExecutionLog(
            skill_id=skill_id,
            session_id=session_id,
            input_params=params,
            started_at=datetime.now(),
        )

        # Get skill manifest
        manifest = self.registry.get_manifest(skill_id)
        if not manifest:
            log.finish(False, f"Skill not found: {skill_id}")
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=["Check skill_id is correct", "Use /help to see available skills"],
            )

        # Check if skill is enabled
        toggle = self.registry.get_skill(skill_id)
        if toggle and not toggle.enabled:
            log.finish(False, f"Skill is disabled: {skill_id}")
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=[f"Enable {manifest.name} in Skills panel"],
            )

        # Check dependencies
        missing_deps = self.registry.check_dependencies(skill_id)
        if missing_deps:
            log.finish(False, f"Missing dependencies: {missing_deps}")
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=[f"Enable dependencies: {missing_deps}"],
            )

        # Validate parameters
        validated_params, param_error = self.validate_params(manifest, params)
        if param_error:
            log.finish(False, param_error)
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=[f"Check parameter format: {manifest.name}"],
            )

        # Check permissions
        has_perm, perm_msg = self.permission_checker.check_permission(
            skill_id, manifest.permissions
        )
        if not has_perm:
            log.finish(False, perm_msg)
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=[f"Grant permissions for {manifest.name}"],
            )

        # Execute skill
        try:
            if manifest.is_builtin:
                output = self._run_builtin_skill(manifest, validated_params, context)
            else:
                output = self._run_plugin_skill(manifest, validated_params, context)

            log.output_type = manifest.output.type if manifest.output else "text"
            log.output_summary = self._summarize_output(output)
            log.finish(True)

            # Increment usage
            self.registry.increment_usage(skill_id)

            # Record audit
            self.audit_logger.record(log)

            return SkillExecutionResult(
                success=True,
                output=output,
                output_type=log.output_type,
                log=log,
                suggestions=self._generate_suggestions(manifest, output),
            )

        except Exception as e:
            log.finish(False, str(e))
            self.audit_logger.record(log)
            return SkillExecutionResult(
                success=False,
                output=None,
                log=log,
                suggestions=[f"Skill execution error: {e}"],
            )

    def execute_by_command(
        self,
        command: str,
        session_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillExecutionResult:
        """Execute skill by command alias.

        Parses command string like "/search keyword --case" and
        executes the corresponding skill.

        Args:
            command: Command string starting with /
            session_id: Current session ID
            context: Execution context

        Returns:
            SkillExecutionResult
        """
        manifest = self.registry.get_skill_by_command(command)
        if not manifest:
            return SkillExecutionResult(
                success=False,
                output={"type": "error", "error": "unknown_command", "command": command},
                log=SkillExecutionLog(
                    skill_id="unknown",
                    session_id=session_id,
                    input_params={"command": command},
                    success=False,
                    error_message="Unknown command",
                ),
                suggestions=["Use /help to see available commands"],
            )

        # Parse parameters from command
        params = self._parse_command_params(command, manifest)

        return self.execute(manifest.skill_id, params, session_id, context)

    def validate_params(
        self,
        manifest: SkillManifest,
        params: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """Validate and normalize parameters against manifest.

        Returns:
            (normalized_params, error_message)
        """
        normalized = {}

        for param_def in manifest.parameters:
            name = param_def.name
            value = params.get(name)

            # Check required
            if param_def.required and value is None:
                return {}, f"Missing required parameter: {name}"

            # Apply default
            if value is None and param_def.default is not None:
                value = param_def.default

            # Type validation
            if value is not None:
                validated, error = self._validate_param_type(param_def, value)
                if error:
                    return {}, error
                normalized[name] = validated

        return normalized, None

    def _validate_param_type(
        self,
        param_def: SkillParameter,
        value: Any,
    ) -> Tuple[Any, Optional[str]]:
        """Validate parameter type."""
        expected_type = param_def.type

        if expected_type == "string":
            if not isinstance(value, str):
                return str(value), None
            # Regex validation
            if param_def.validation_pattern:
                if not re.match(param_def.validation_pattern, value):
                    return None, f"Parameter {param_def.name} doesn't match pattern"
            return value, None

        elif expected_type == "integer":
            try:
                return int(value), None
            except (ValueError, TypeError):
                return None, f"Parameter {param_def.name} must be integer"

        elif expected_type == "boolean":
            if isinstance(value, bool):
                return value, None
            if isinstance(value, str):
                return value.lower() in ("true", "yes", "1"), None
            return bool(value), None

        elif expected_type == "path":
            path_str = str(value)
            # Basic path validation
            return path_str, None

        elif expected_type == "list":
            if isinstance(value, list):
                return value, None
            if isinstance(value, str):
                # Parse comma-separated or space-separated
                return value.split(), None
            return [value], None

        return value, None

    def _parse_command_params(
        self,
        command: str,
        manifest: SkillManifest,
    ) -> Dict[str, Any]:
        """Parse parameters from command string.

        Handles formats like:
        - /search keyword --case --regex
        - /summary path/to/file
        - /deps path --depth 2
        """
        params = {}
        parts = command.strip().split()

        if len(parts) < 2:
            return params

        # First part is command, rest are args
        args = parts[1:]

        # Map positional args to parameters
        positional_idx = 0
        i = 0

        while i < len(args):
            arg = args[i]

            # Named parameter (--name value)
            if arg.startswith("--"):
                name = arg[2:]
                # Handle boolean flags (--case = true)
                if i + 1 < len(args) and not args[i + 1].startswith("--"):
                    params[name] = args[i + 1]
                    i += 2
                else:
                    params[name] = True
                    i += 1
            else:
                # Positional parameter
                param_defs = [p for p in manifest.parameters if p.required]
                if positional_idx < len(param_defs):
                    params[param_defs[positional_idx].name] = arg
                positional_idx += 1
                i += 1

        return params

    def _run_builtin_skill(
        self,
        manifest: SkillManifest,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> Any:
        """Execute built-in skill by calling CodeAnalyzer methods."""
        skill_id = manifest.skill_id

        # Import builtin skills module
        from .builtin import execute_builtin_skill

        return execute_builtin_skill(skill_id, self.analyzer, params)

    def _run_plugin_skill(
        self,
        manifest: SkillManifest,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> Any:
        """Execute external plugin skill."""
        if not manifest.plugin_path or not manifest.entry_point:
            raise ValueError("Plugin missing plugin_path or entry_point")

        plugin_dir = Path(manifest.plugin_path)
        entry_parts = manifest.entry_point.split(":")
        module_file = entry_parts[0] if entry_parts else "main.py"
        func_name = entry_parts[1] if len(entry_parts) > 1 else "execute"

        module_path = plugin_dir / module_file
        if not module_path.exists():
            raise FileNotFoundError(f"Plugin module not found: {module_path}")

        # Dynamically load module
        spec = importlib.util.spec_from_file_location(
            f"plugin_{manifest.skill_id}",
            module_path
        )
        if not spec or not spec.loader:
            raise ImportError(f"Cannot load plugin module: {module_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[f"plugin_{manifest.skill_id}"] = module
        spec.loader.exec_module(module)

        # Get execute function
        execute_func = getattr(module, func_name, None)
        if not execute_func:
            raise AttributeError(f"Plugin missing execute function: {func_name}")

        # Execute with context
        return execute_func(params, context)

    def _summarize_output(self, output: Any) -> str:
        """Create brief summary of output for audit log."""
        if isinstance(output, dict):
            if "type" in output:
                return f"type={output['type']}"
            return f"dict with {len(output)} keys"
        elif isinstance(output, str):
            return f"{len(output)} chars"
        elif isinstance(output, list):
            return f"{len(output)} items"
        return str(type(output).__name__)

    def _generate_suggestions(
        self,
        manifest: SkillManifest,
        output: Any,
    ) -> List[str]:
        """Generate follow-up suggestions based on output."""
        suggestions = []

        if manifest.category == "analysis":
            suggestions.append("Use /summary for detailed file analysis")
            suggestions.append("Use /deps to see dependencies")

        elif manifest.category == "retrieval":
            if isinstance(output, dict) and output.get("hit_count", 0) > 10:
                suggestions.append("Refine search keywords for fewer results")

        return suggestions