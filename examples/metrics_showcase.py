"""Sample code for demonstrating the /metrics command.

The functions below deliberately contain branching, loops, exception handling,
and a class method so that /metrics has interesting complexity data to show.
"""

from __future__ import annotations

from examples import tasks_showcase


class DemoRiskClassifier:
    """Class with a moderately complex method for hotspot analysis."""

    def classify(self, score: int, tags: list[str], retry_count: int) -> str:
        if score >= 90 and "security" in tags:
            return "critical"
        if score >= 75:
            if retry_count > 2:
                return "high"
            return "medium"
        if score >= 50 or "legacy" in tags:
            return "watch"
        return "low"


def score_demo_items(items: list[dict[str, object]]) -> dict[str, int]:
    """Compute a deliberately branchy score summary for metrics demos."""
    summary = {"critical": 0, "high": 0, "medium": 0, "watch": 0, "low": 0}
    classifier = DemoRiskClassifier()

    for item in items:
        try:
            raw_score = int(item.get("score", 0))
        except (TypeError, ValueError):
            raw_score = 0

        tags = item.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        retry_count = int(item.get("retry_count", 0) or 0)
        label = classifier.classify(raw_score, [str(tag) for tag in tags], retry_count)
        summary[label] += 1

        if raw_score < 0:
            summary["low"] += 1
        elif raw_score > 100:
            summary["critical"] += 1
        elif "demo" in tags and raw_score > 60:
            summary["medium"] += 1

    return summary


def route_demo_event(event: dict[str, object]) -> str:
    """Large branchy router that intentionally stands out in /metrics output."""
    kind = str(event.get("kind", ""))
    priority = int(event.get("priority", 0) or 0)
    user = str(event.get("user", ""))
    retries = int(event.get("retries", 0) or 0)
    flags = event.get("flags", [])
    if not isinstance(flags, list):
        flags = []

    if kind == "security":
        if priority >= 90 and "external" in flags:
            return "page-security-team"
        if priority >= 80:
            return "open-critical-security-ticket"
        if "audit" in flags:
            return "schedule-security-review"
        return "log-security-watch"

    if kind == "build":
        if priority >= 70 and retries > 2:
            return "rollback-build"
        if priority >= 70:
            return "rerun-build"
        if "cache" in flags:
            return "clear-build-cache"
        return "watch-build"

    if kind == "model":
        if "latency" in flags and priority > 60:
            return "reduce-context"
        if "quality" in flags and user:
            return "collect-feedback"
        if priority > 85:
            return "switch-model"
        return "keep-model"

    if kind == "ui":
        if "mobile" in flags and priority > 50:
            return "fix-responsive-layout"
        if "theme" in flags:
            return "review-theme-token"
        if retries > 3:
            return "assign-ui-owner"
        return "queue-ui-polish"

    if kind == "data":
        if priority > 75 and "loss" in flags:
            return "stop-import"
        if priority > 40 and "schema" in flags:
            return "migrate-schema"
        if "export" in flags:
            return "verify-export"
        return "archive-data-note"

    if priority > 95:
        return "manual-escalation"
    if priority > 50 and retries:
        return "retry-with-owner"
    if user:
        return "notify-user"
    return "ignore-demo-event"


def render_demo_summary(items: list[dict[str, object]]) -> str:
    """Render scores together with a command sample from tasks_showcase."""
    scores = score_demo_items(items)
    command_hint = tasks_showcase.describe_demo()
    parts = [f"{name}={count}" for name, count in sorted(scores.items())]
    return f"{command_hint}; " + ", ".join(parts)
