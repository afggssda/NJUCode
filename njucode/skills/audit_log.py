"""Audit Logger - Records skill execution for FR-07 and FR-10 requirements.

This module provides:
- Execution log recording
- Query and filter logs
- AI tracking ledger export
- Statistics generation
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import SkillExecutionLog


class AuditLogger:
    """Audit logging for skill execution.

    Supports:
    1. FR-07: Plugin execution audit
    2. FR-10: AI generation tracking
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.log_path = workspace_root / ".nju_code" / "skills_audit.json"
        self.logs: List[SkillExecutionLog] = []
        self._max_logs = 1000  # Limit to prevent unbounded growth

    def record(self, log: SkillExecutionLog) -> None:
        """Record an execution log.

        Args:
            log: SkillExecutionLog to record
        """
        self.logs.append(log)

        # Trim old logs if exceeded limit
        if len(self.logs) > self._max_logs:
            self.logs = self.logs[-self._max_logs:]

        # Auto-save
        self.save()

    def query(
        self,
        skill_id: Optional[str] = None,
        session_id: Optional[str] = None,
        time_range: Optional[Tuple[datetime, datetime]] = None,
        success_only: Optional[bool] = None,
        limit: int = 100,
    ) -> List[SkillExecutionLog]:
        """Query logs with filters.

        Args:
            skill_id: Filter by skill
            session_id: Filter by session
            time_range: (start, end) datetime range
            success_only: Filter by success status
            limit: Max results

        Returns:
            List of matching logs
        """
        results = []

        for log in reversed(self.logs):  # Most recent first
            if skill_id and log.skill_id != skill_id:
                continue
            if session_id and log.session_id != session_id:
                continue
            if time_range:
                start, end = time_range
                if log.started_at < start or log.started_at > end:
                    continue
            if success_only is not None and log.success != success_only:
                continue

            results.append(log)
            if len(results) >= limit:
                break

        return results

    def get_ai_generated_logs(self) -> List[SkillExecutionLog]:
        """Get logs marked as AI-generated for FR-10 tracking."""
        return [log for log in self.logs if log.is_ai_generated]

    def export_ai_ledger(self) -> str:
        """Export AI generation records in ledger format.

        Generates content suitable for docs/ai-ledger.md
        as required by course FR-10.
        """
        ai_logs = self.get_ai_generated_logs()

        lines = [
            "# AI Generation Ledger",
            "",
            "This document tracks AI-generated content as required by FR-10.",
            f"Generated: {datetime.now().isoformat()}",
            "",
            "## Summary",
            f"Total AI executions: {len(ai_logs)}",
            "",
            "## Records",
            "",
        ]

        for log in ai_logs:
            lines.append(f"### {log.log_id}")
            lines.append(f"- Skill: {log.skill_id}")
            lines.append(f"- Task ID: {log.ai_task_id or 'N/A'}")
            lines.append(f"- Session: {log.session_id}")
            lines.append(f"- Started: {log.started_at.isoformat()}")
            lines.append(f"- Duration: {log.duration_ms}ms")
            lines.append(f"- Success: {log.success}")
            lines.append(f"- Reviewer: {log.reviewer or 'N/A'}")
            lines.append(f"- Input: {json.dumps(log.input_params, ensure_ascii=False)}")
            lines.append(f"- Output: {log.output_summary[:100]}...")
            lines.append("")

        return "\n".join(lines)

    def get_statistics(self, skill_id: Optional[str] = None) -> Dict[str, Any]:
        """Generate execution statistics.

        Args:
            skill_id: Optional skill to filter

        Returns:
            Statistics dict
        """
        logs = self.logs
        if skill_id:
            logs = [log for log in logs if log.skill_id == skill_id]

        if not logs:
            return {
                "total_executions": 0,
                "success_rate": 0,
                "avg_duration_ms": 0,
            }

        success_count = sum(1 for log in logs if log.success)
        total_duration = sum(log.duration_ms for log in logs)

        # Group by skill
        by_skill: Dict[str, int] = {}
        for log in logs:
            by_skill[log.skill_id] = by_skill.get(log.skill_id, 0) + 1

        return {
            "total_executions": len(logs),
            "success_rate": success_count / len(logs) * 100,
            "avg_duration_ms": total_duration / len(logs),
            "by_skill": by_skill,
            "ai_generated_count": sum(1 for log in logs if log.is_ai_generated),
        }

    def clear_old_logs(self, days: int = 30) -> int:
        """Remove logs older than specified days.

        Args:
            days: Age threshold

        Returns:
            Number of removed logs
        """
        cutoff = datetime.now() - __import__("datetime").timedelta(days=days)
        original_count = len(self.logs)
        self.logs = [log for log in self.logs if log.started_at >= cutoff]
        removed = original_count - len(self.logs)

        if removed > 0:
            self.save()

        return removed

    def save(self) -> None:
        """Persist logs to skills_audit.json."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "version": "1.0",
                "updated_at": datetime.now().isoformat(),
                "logs": [
                    {
                        "log_id": log.log_id,
                        "skill_id": log.skill_id,
                        "session_id": log.session_id,
                        "input_params": log.input_params,
                        "output_summary": log.output_summary,
                        "output_type": log.output_type,
                        "success": log.success,
                        "error_message": log.error_message,
                        "started_at": log.started_at.isoformat() if log.started_at else None,
                        "finished_at": log.finished_at.isoformat() if log.finished_at else None,
                        "duration_ms": log.duration_ms,
                        "files_read": log.files_read,
                        "files_modified": log.files_modified,
                        "is_ai_generated": log.is_ai_generated,
                        "ai_task_id": log.ai_task_id,
                        "reviewer": log.reviewer,
                    }
                    for log in self.logs
                ],
            }

            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            print(f"[AuditLogger] Failed to save: {e}")

    def load(self) -> None:
        """Load logs from skills_audit.json."""
        if not self.log_path.exists():
            return

        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            logs_data = data.get("logs", [])
            self.logs = []

            for log_data in logs_data:
                started_at = None
                if log_data.get("started_at"):
                    try:
                        started_at = datetime.fromisoformat(log_data["started_at"])
                    except Exception:
                        started_at = datetime.now()

                log = SkillExecutionLog(
                    log_id=log_data.get("log_id", ""),
                    skill_id=log_data.get("skill_id", ""),
                    session_id=log_data.get("session_id", ""),
                    input_params=log_data.get("input_params", {}),
                    output_summary=log_data.get("output_summary", ""),
                    output_type=log_data.get("output_type", "text"),
                    success=log_data.get("success", True),
                    error_message=log_data.get("error_message"),
                    started_at=started_at or datetime.now(),
                    duration_ms=log_data.get("duration_ms", 0),
                    files_read=log_data.get("files_read", []),
                    files_modified=log_data.get("files_modified", []),
                    is_ai_generated=log_data.get("is_ai_generated", False),
                    ai_task_id=log_data.get("ai_task_id"),
                    reviewer=log_data.get("reviewer"),
                )
                self.logs.append(log)

        except Exception as e:
            print(f"[AuditLogger] Failed to load: {e}")