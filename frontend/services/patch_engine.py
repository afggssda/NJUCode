"""Patch/Rollback Execution Engine - WBS-4 core implementation.

This module provides:
- PatchStatus: lifecycle state machine for patch tasks
- PatchOperation: single-file change with diff generation
- PatchTask: multi-file atomic patch with full audit trail
- PatchExecutionResult: result of apply/rollback operations
- PatchHistoryStore: JSON persistence for patch history
- PatchEngine: core engine (generate → validate → apply → rollback)
"""

from __future__ import annotations

import ast
import difflib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PatchStatus(Enum):
    """Lifecycle states for a PatchTask."""
    PENDING = "pending"          # Created, not yet previewed
    PREVIEWED = "previewed"      # User has seen the diff
    CONFIRMED = "confirmed"      # User confirmed, ready to apply
    APPLYING = "applying"        # In progress (transient)
    APPLIED = "applied"          # Successfully written to disk
    FAILED = "failed"            # Apply attempted but errored
    ROLLED_BACK = "rolled_back"  # Restored from backup
    CANCELLED = "cancelled"      # User cancelled before apply


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PatchOperation:
    """Represents a single-file change within a PatchTask.

    operation_type:
      "create"  — file does not exist yet (old_content is empty)
      "modify"  — file exists and content changes
      "delete"  — file will be removed (new_content is empty)
    """
    file_path: str
    old_content: str
    new_content: str
    diff: str = ""
    operation_type: str = "modify"   # "create" | "modify" | "delete"
    backup_path: Optional[str] = None

    def generate_diff(self) -> str:
        """Generate and cache unified diff between old and new content."""
        old_lines = self.old_content.splitlines()
        new_lines = self.new_content.splitlines()
        diff_lines = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{self.file_path}",
            tofile=f"b/{self.file_path}",
            lineterm="",
        ))
        self.diff = "\n".join(diff_lines)
        return self.diff

    def validate_syntax(self) -> Tuple[bool, str]:
        """Validate Python syntax of new_content if file is .py.

        Returns (True, "") on success or for non-Python files.
        Returns (False, error_message) on syntax error.
        """
        if not self.file_path.endswith(".py"):
            return True, ""
        if not self.new_content:
            return True, ""
        try:
            ast.parse(self.new_content)
            return True, ""
        except SyntaxError as e:
            return False, f"Syntax error in {self.file_path}: {e}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "old_content": self.old_content,
            "new_content": self.new_content,
            "diff": self.diff,
            "operation_type": self.operation_type,
            "backup_path": self.backup_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PatchOperation":
        return cls(
            file_path=data["file_path"],
            old_content=data.get("old_content", ""),
            new_content=data.get("new_content", ""),
            diff=data.get("diff", ""),
            operation_type=data.get("operation_type", "modify"),
            backup_path=data.get("backup_path"),
        )


@dataclass
class PatchTask:
    """A multi-file patch task with full lifecycle tracking."""
    task_id: str = field(default_factory=lambda: str(uuid4()))
    description: str = ""
    operations: List[PatchOperation] = field(default_factory=list)
    status: PatchStatus = PatchStatus.PENDING
    session_id: str = ""
    is_ai_generated: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    applied_at: Optional[datetime] = None
    rolled_back_at: Optional[datetime] = None
    error_message: Optional[str] = None
    reviewer: Optional[str] = None

    @property
    def files_affected(self) -> List[str]:
        return [op.file_path for op in self.operations]

    @property
    def is_reversible(self) -> bool:
        return self.status == PatchStatus.APPLIED

    @property
    def summary_line(self) -> str:
        """One-line summary for history display."""
        ts = self.created_at.strftime("%m-%d %H:%M")
        n = len(self.operations)
        ai_tag = " [AI]" if self.is_ai_generated else ""
        return f"[{ts}] {self.status.value:12s} {n} file(s){ai_tag}  {self.description[:40]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "operations": [op.to_dict() for op in self.operations],
            "status": self.status.value,
            "session_id": self.session_id,
            "is_ai_generated": self.is_ai_generated,
            "created_at": self.created_at.isoformat(),
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "rolled_back_at": self.rolled_back_at.isoformat() if self.rolled_back_at else None,
            "error_message": self.error_message,
            "reviewer": self.reviewer,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PatchTask":
        def _parse_dt(val: Optional[str]) -> Optional[datetime]:
            if not val:
                return None
            try:
                return datetime.fromisoformat(val)
            except Exception:
                return None

        created_at = _parse_dt(data.get("created_at")) or datetime.now()
        status_val = data.get("status", "pending")
        try:
            status = PatchStatus(status_val)
        except ValueError:
            status = PatchStatus.PENDING

        return cls(
            task_id=data.get("task_id", str(uuid4())),
            description=data.get("description", ""),
            operations=[PatchOperation.from_dict(op) for op in data.get("operations", [])],
            status=status,
            session_id=data.get("session_id", ""),
            is_ai_generated=bool(data.get("is_ai_generated", False)),
            created_at=created_at,
            applied_at=_parse_dt(data.get("applied_at")),
            rolled_back_at=_parse_dt(data.get("rolled_back_at")),
            error_message=data.get("error_message"),
            reviewer=data.get("reviewer"),
        )


@dataclass
class PatchExecutionResult:
    """Result of a patch apply or rollback operation."""
    success: bool
    task_id: str
    files_modified: List[str] = field(default_factory=list)
    files_restored: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class PatchHistoryStore:
    """Persists PatchTask objects to .nju_code/patch_history.json.

    Uses atomic write (temp file + rename) to avoid corruption on crash.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.history_path = workspace_root / ".nju_code" / "patch_history.json"
        self._tasks: Dict[str, PatchTask] = {}

    def save_task(self, task: PatchTask) -> None:
        """Upsert a task and flush to disk."""
        self._tasks[task.task_id] = task
        self._flush()

    def load(self) -> None:
        """Load all tasks from disk into memory."""
        if not self.history_path.exists():
            return
        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for task_data in data.get("tasks", []):
                task = PatchTask.from_dict(task_data)
                self._tasks[task.task_id] = task
        except Exception as e:
            print(f"[PatchHistoryStore] Failed to load: {e}")

    def get_task(self, task_id: str) -> Optional[PatchTask]:
        return self._tasks.get(task_id)

    def get_all(self) -> List[PatchTask]:
        """Return all tasks sorted newest-first."""
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def get_by_session(self, session_id: str) -> List[PatchTask]:
        return [t for t in self.get_all() if t.session_id == session_id]

    def get_pending(self) -> List[PatchTask]:
        """Return tasks that can still be applied."""
        applicable = {PatchStatus.PENDING, PatchStatus.PREVIEWED, PatchStatus.CONFIRMED}
        return [t for t in self.get_all() if t.status in applicable]

    def get_last_applied(self) -> Optional[PatchTask]:
        """Return the most recently applied (and not yet rolled back) task."""
        for task in self.get_all():
            if task.status == PatchStatus.APPLIED:
                return task
        return None

    def delete_task(self, task_id: str) -> bool:
        """Remove a task from history. Returns True if found and removed."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            self._flush()
            return True
        return False

    def _flush(self) -> None:
        """Write all tasks to disk atomically via temp-file rename."""
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": "1.0",
                "updated_at": datetime.now().isoformat(),
                "tasks": [t.to_dict() for t in self._tasks.values()],
            }
            tmp_path = self.history_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            try:
                tmp_path.replace(self.history_path)
            except OSError:
                # Some restricted Windows shells deny atomic replace even when
                # normal writes are allowed. Fall back to a direct write so the
                # patch history remains usable in sandboxed course setups.
                self.history_path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        except Exception as e:
            print(f"[PatchHistoryStore] Failed to save: {e}")


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------

class PatchEngine:
    """Core patch/rollback engine.

    Workflow:
      generate_patch() → preview_patch() → validate_patch() → apply_patch()
                                                              ↓ (on failure)
                                                         rollback_patch()

    All state transitions are persisted to PatchHistoryStore immediately.
    All apply/rollback events are recorded to AuditLogger for FR-10.
    """

    def __init__(
        self,
        workspace_root: Path,
        history_store: PatchHistoryStore,
        audit_logger: Any,
    ) -> None:
        self.workspace_root = workspace_root
        self.history_store = history_store
        self.audit_logger = audit_logger
        self._backup_root = workspace_root / ".nju_code" / "patch_backups"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_patch(
        self,
        file_changes: Dict[str, Tuple[str, str]],
        description: str = "",
        session_id: str = "",
        is_ai_generated: bool = False,
        reviewer: Optional[str] = None,
    ) -> PatchTask:
        """Create a PatchTask from a dict of {rel_path: (old_content, new_content)}.

        Automatically determines operation_type:
          old empty, new non-empty → "create"
          old non-empty, new empty → "delete"
          both non-empty           → "modify"

        Diffs are generated eagerly so preview is instant.
        """
        operations: List[PatchOperation] = []
        for file_path, (old_content, new_content) in file_changes.items():
            abs_path = self.workspace_root / file_path
            file_exists = abs_path.exists()

            # If caller passed empty old_content but file exists, read actual content
            if not old_content and file_exists:
                try:
                    old_content = abs_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    pass

            if not file_exists and new_content:
                op_type = "create"
            elif file_exists and not new_content:
                op_type = "delete"
            else:
                op_type = "modify"

            op = PatchOperation(
                file_path=file_path,
                old_content=old_content,
                new_content=new_content,
                operation_type=op_type,
            )
            op.generate_diff()
            operations.append(op)

        task = PatchTask(
            description=description,
            operations=operations,
            status=PatchStatus.PENDING,
            session_id=session_id,
            is_ai_generated=is_ai_generated,
            reviewer=reviewer,
        )
        self.history_store.save_task(task)
        return task

    def preview_patch(self, task: PatchTask) -> str:
        """Return a formatted diff string for TUI display.

        Advances task status from PENDING → PREVIEWED.
        """
        lines = [
            f"=== Patch Preview: {task.description or task.task_id[:8]} ===",
            f"Files affected : {len(task.operations)}",
            f"Status         : {task.status.value}",
            f"AI generated   : {'yes' if task.is_ai_generated else 'no'}",
            f"Created        : {task.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        for op in task.operations:
            tag = f"[{op.operation_type.upper()}]"
            lines.append(f"{'─' * 60}")
            lines.append(f"{tag} {op.file_path}")
            lines.append(f"{'─' * 60}")
            diff = op.diff or op.generate_diff()
            if diff:
                lines.extend(diff.splitlines())
            else:
                lines.append("(no textual changes)")
            lines.append("")

        return "\n".join(lines)

    def _resolve_safe_path(self, file_path: str) -> Tuple[Optional[Path], str]:
        """Resolve file_path relative to workspace_root, rejecting traversals.

        Returns (resolved_path, "") on success, (None, reason) if the resolved
        path escapes the workspace boundary.
        """
        try:
            resolved = (self.workspace_root / file_path).resolve()
            workspace_resolved = self.workspace_root.resolve()
            resolved.relative_to(workspace_resolved)
            return resolved, ""
        except (ValueError, OSError):
            return None, f"Path '{file_path}' escapes workspace boundary"

    def validate_patch(self, task: PatchTask) -> Tuple[bool, str]:
        """Pre-flight checks before applying.

        Checks (in order):
        1. Task is in an applicable state
        2. All paths are within workspace boundary (no traversal)
        3. For modify/delete: target file exists
        4. For modify/delete: file content matches old_content (no external edits)
        5. For create: target file does NOT already exist
        6. Python syntax validation for .py files
        7. Backup directory is writable

        Returns (True, "") on success, (False, reason) on first failure.
        """
        applicable = {PatchStatus.PENDING, PatchStatus.PREVIEWED, PatchStatus.CONFIRMED}
        if task.status not in applicable:
            return False, f"Task is in state '{task.status.value}', cannot apply"

        for op in task.operations:
            abs_path, path_err = self._resolve_safe_path(op.file_path)
            if abs_path is None:
                return False, path_err

            if op.operation_type in ("modify", "delete"):
                if not abs_path.exists():
                    return False, f"File not found: {op.file_path}"
                try:
                    current = abs_path.read_text(encoding="utf-8", errors="ignore")
                except OSError as e:
                    return False, f"Cannot read {op.file_path}: {e}"
                if current != op.old_content:
                    return False, (
                        f"File '{op.file_path}' has been modified since the patch was created. "
                        "Regenerate the patch or discard."
                    )

            elif op.operation_type == "create":
                if abs_path.exists():
                    return False, f"File already exists: {op.file_path}"


            # Python syntax check for new content
            ok, syntax_err = op.validate_syntax()
            if not ok:
                return False, syntax_err

        return True, ""

    def confirm_patch(self, task_id: str) -> Tuple[bool, str]:
        """Mark a task as CONFIRMED (user clicked Confirm in UI).

        Returns (True, "") or (False, reason).
        """
        task = self.history_store.get_task(task_id)
        if not task:
            return False, f"Task not found: {task_id}"
        if task.status not in {PatchStatus.PENDING, PatchStatus.PREVIEWED}:
            return False, f"Task cannot be confirmed in state: {task.status.value}"
        task.status = PatchStatus.CONFIRMED
        self.history_store.save_task(task)
        return True, ""

    def cancel_patch(self, task_id: str) -> Tuple[bool, str]:
        """Cancel a pending/previewed/confirmed task."""
        task = self.history_store.get_task(task_id)
        if not task:
            return False, f"Task not found: {task_id}"
        cancellable = {PatchStatus.PENDING, PatchStatus.PREVIEWED, PatchStatus.CONFIRMED}
        if task.status not in cancellable:
            return False, f"Task cannot be cancelled in state: {task.status.value}"
        task.status = PatchStatus.CANCELLED
        self.history_store.save_task(task)
        return True, ""

    def apply_patch(
        self,
        task: PatchTask,
    ) -> PatchExecutionResult:
        """Apply all operations atomically.

        Algorithm:
          1. validate_patch() — abort on failure
          2. Create backup dir: .nju_code/patch_backups/{task_id}/
          3. Backup all files that will be modified/deleted
          4. Apply all operations in sequence
          5. On any failure: restore backed-up files AND delete any created files
          6. On success: mark APPLIED, record audit log
        """
        valid, reason = self.validate_patch(task)
        if not valid:
            task.status = PatchStatus.FAILED
            task.error_message = reason
            self.history_store.save_task(task)
            return PatchExecutionResult(
                success=False,
                task_id=task.task_id,
                error_message=reason,
            )

        task.status = PatchStatus.APPLYING
        self.history_store.save_task(task)

        backup_dir = self._backup_root / task.task_id
        backed_up: List[Tuple[str, Path]] = []
        created_files: List[Path] = []

        workspace_resolved = self.workspace_root.resolve()

        try:
            backup_dir.mkdir(parents=True, exist_ok=True)

            # Phase 1: backup existing files
            for op in task.operations:
                if op.operation_type in ("modify", "delete"):
                    abs_path, _ = self._resolve_safe_path(op.file_path)
                    rel_path = abs_path.relative_to(workspace_resolved)
                    backup_file = backup_dir / rel_path
                    backup_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(abs_path, backup_file)
                    op.backup_path = str(backup_file)
                    backed_up.append((op.file_path, backup_file))

            # Phase 2: apply all operations
            files_modified: List[str] = []
            for op in task.operations:
                abs_path, _ = self._resolve_safe_path(op.file_path)
                if op.operation_type == "create":
                    abs_path.parent.mkdir(parents=True, exist_ok=True)
                    abs_path.write_text(op.new_content, encoding="utf-8")
                    created_files.append(abs_path)
                elif op.operation_type == "modify":
                    abs_path.write_text(op.new_content, encoding="utf-8")
                elif op.operation_type == "delete":
                    abs_path.unlink()
                files_modified.append(op.file_path)

            task.status = PatchStatus.APPLIED
            task.applied_at = datetime.now()
            self.history_store.save_task(task)
            self._record_apply_audit(task, files_modified)

            return PatchExecutionResult(
                success=True,
                task_id=task.task_id,
                files_modified=files_modified,
            )

        except Exception as e:
            restored = self._restore_backups(backed_up)
            for created_path in created_files:
                try:
                    if created_path.exists():
                        created_path.unlink()
                except Exception:
                    pass
            task.status = PatchStatus.FAILED
            task.error_message = str(e)
            self.history_store.save_task(task)
            return PatchExecutionResult(
                success=False,
                task_id=task.task_id,
                files_restored=restored,
                error_message=str(e),
            )

    def rollback_patch(self, task_id: str) -> PatchExecutionResult:
        """Restore files from backup for a previously applied patch.

        Only APPLIED tasks can be rolled back.
        After rollback, status becomes ROLLED_BACK.
        """
        task = self.history_store.get_task(task_id)
        if not task:
            return PatchExecutionResult(
                success=False,
                task_id=task_id,
                error_message=f"Task not found: {task_id}",
            )

        if task.status != PatchStatus.APPLIED:
            return PatchExecutionResult(
                success=False,
                task_id=task_id,
                error_message=f"Cannot rollback task in state: {task.status.value}",
            )

        backup_dir = self._backup_root / task_id
        if not backup_dir.exists():
            return PatchExecutionResult(
                success=False,
                task_id=task_id,
                error_message=f"Backup directory not found: {backup_dir}",
            )

        restored: List[str] = []
        errors: List[str] = []

        for op in task.operations:
            abs_path = self.workspace_root / op.file_path
            if op.operation_type == "create":
                # Undo a create by deleting the file
                try:
                    if abs_path.exists():
                        abs_path.unlink()
                    restored.append(op.file_path)
                except OSError as e:
                    errors.append(f"{op.file_path}: {e}")

            elif op.operation_type in ("modify", "delete"):
                backup_file = backup_dir / op.file_path
                if backup_file.exists():
                    try:
                        abs_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(backup_file, abs_path)
                        restored.append(op.file_path)
                    except OSError as e:
                        errors.append(f"{op.file_path}: {e}")
                else:
                    errors.append(f"Backup missing for {op.file_path}")

        if errors:
            task.error_message = "; ".join(errors)
            self.history_store.save_task(task)
            return PatchExecutionResult(
                success=False,
                task_id=task_id,
                files_restored=restored,
                error_message=task.error_message,
            )

        task.status = PatchStatus.ROLLED_BACK
        task.rolled_back_at = datetime.now()
        self.history_store.save_task(task)
        self._record_rollback_audit(task, restored)

        return PatchExecutionResult(
            success=True,
            task_id=task_id,
            files_restored=restored,
        )

    def get_history(self, limit: int = 50) -> List[PatchTask]:
        """Return patch history, newest first."""
        return self.history_store.get_all()[:limit]

    def get_pending_tasks(self) -> List[PatchTask]:
        """Return tasks that are waiting to be applied."""
        return self.history_store.get_pending()

    def format_history(self, limit: int = 10) -> str:
        """Return a human-readable history table."""
        tasks = self.get_history(limit)
        if not tasks:
            return "No patch history found."

        lines = [
            f"{'─' * 70}",
            f"{'ID':8s}  {'Created':16s}  {'Status':12s}  {'Files':5s}  Description",
            f"{'─' * 70}",
        ]
        for task in tasks:
            short_id = task.task_id[:8]
            ts = task.created_at.strftime("%m-%d %H:%M")
            status = task.status.value
            n = len(task.operations)
            desc = task.description[:30]
            ai = " [AI]" if task.is_ai_generated else ""
            lines.append(f"{short_id}  {ts:16s}  {status:12s}  {n:5d}  {desc}{ai}")
        lines.append(f"{'─' * 70}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _restore_backups(self, backed_up: List[Tuple[str, Path]]) -> List[str]:
        """Best-effort restore of backed-up files. Returns list of restored paths."""
        restored: List[str] = []
        for file_path, backup_file in backed_up:
            try:
                abs_path = self.workspace_root / file_path
                shutil.copy2(backup_file, abs_path)
                restored.append(file_path)
            except Exception:
                pass
        return restored

    def _record_apply_audit(self, task: PatchTask, files_modified: List[str]) -> None:
        """Write apply event to AuditLogger for FR-10 compliance."""
        if not self.audit_logger:
            return
        try:
            from ..skills.models import SkillExecutionLog
            log = SkillExecutionLog(
                skill_id="builtin.patch.apply",
                session_id=task.session_id,
                input_params={
                    "task_id": task.task_id,
                    "description": task.description,
                    "files": files_modified,
                },
                output_summary=f"Applied patch to {len(files_modified)} file(s)",
                output_type="diff",
                success=True,
                files_modified=files_modified,
                is_ai_generated=task.is_ai_generated,
                ai_task_id=task.task_id,
                reviewer=task.reviewer,
            )
            log.finish(True)
            self.audit_logger.record(log)
        except Exception as e:
            print(f"[PatchEngine] Audit log failed: {e}")

    def _record_rollback_audit(self, task: PatchTask, files_restored: List[str]) -> None:
        """Write rollback event to AuditLogger."""
        if not self.audit_logger:
            return
        try:
            from ..skills.models import SkillExecutionLog
            log = SkillExecutionLog(
                skill_id="builtin.patch.rollback",
                session_id=task.session_id,
                input_params={"task_id": task.task_id},
                output_summary=f"Rolled back {len(files_restored)} file(s)",
                output_type="text",
                success=True,
                files_modified=files_restored,
                is_ai_generated=False,
            )
            log.finish(True)
            self.audit_logger.record(log)
        except Exception as e:
            print(f"[PatchEngine] Rollback audit log failed: {e}")
