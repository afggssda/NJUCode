"""Skills Panel - UI component for skill management.

This module provides:
- Skill list display with enable/disable toggles
- Plugin installation button
- Audit log viewer
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from textual.app import ComposeResult
from textual import on
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Checkbox, Label, ListView, ListItem, Static

from frontend.skills.models import SkillToggle, SkillStatus


class SkillToggled(Message):
    """Event emitted when a skill toggle changes."""

    def __init__(self, skill_id: str, enabled: bool) -> None:
        self.skill_id = skill_id
        self.enabled = enabled
        super().__init__()


class PluginInstallRequested(Message):
    """Event emitted when user requests plugin installation."""

    def __init__(self, plugin_path: Path) -> None:
        self.plugin_path = plugin_path
        super().__init__()


class AuditLogRequested(Message):
    """Event emitted when user wants to view audit logs."""

    pass


class SkillListItem(ListItem):
    """List item for a single skill."""

    def __init__(self, skill_toggle: SkillToggle) -> None:
        self.skill_toggle = skill_toggle
        super().__init__()

    def compose(self) -> ComposeResult:
        toggle = self.skill_toggle
        status_icon = "●" if toggle.enabled else "○"
        status_class = "skill-enabled" if toggle.enabled else "skill-disabled"

        yield Horizontal(
            Static(f"{status_icon}", classes=status_class),
            Static(f"{toggle.manifest.name}", classes="skill-name"),
            Static(f"[{toggle.manifest.category}]", classes="skill-category"),
            Static(f"({toggle.usage_count})", classes="skill-usage"),
            classes="skill-item-content",
        )


class SkillsPanel(Vertical):
    """Skills management panel.

    Provides:
    1. Skill list with enable/disable toggles
    2. Plugin installation
    3. Audit log access
    """

    DEFAULT_CSS = """
    SkillsPanel {
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

    .skills-actions {
        height: auto;
        margin-bottom: 1;
    }

    .skills-actions Button {
        margin-right: 1;
    }

    ListView {
        height: auto;
        max-height: 15;
    }

    .skill-item-content {
        height: 1;
    }

    .skill-enabled {
        color: $success;
    }

    .skill-disabled {
        color: $error;
    }

    .skill-name {
        margin-left: 1;
    }

    .skill-category {
        margin-left: 1;
        color: $text-muted;
    }

    .skill-usage {
        margin-left: 1;
        color: $accent;
    }
    """

    skills: reactive[List[SkillToggle]] = reactive(list)

    def compose(self) -> ComposeResult:
        yield Label("Skills Management", classes="panel-title")

        with Horizontal(classes="skills-actions"):
            yield Button("Install Plugin", id="install_plugin_btn", variant="primary")
            yield Button("Audit Log", id="audit_log_btn", variant="warning")

        yield Label("Built-in Skills", classes="sub-title")
        yield ListView(id="builtin_skills_list")

        yield Label("Installed Plugins", classes="sub-title")
        yield ListView(id="plugin_skills_list")

        yield Label("", id="skills_stats")

    def on_mount(self) -> None:
        """Initialize the panel."""
        self._refresh_lists()

    def refresh_skills(self, skills: List[SkillToggle]) -> None:
        """Refresh skill list display.

        Args:
            skills: List of skill toggles from state
        """
        self.skills = skills
        self._refresh_lists()
        self._update_stats()

    def _refresh_lists(self) -> None:
        """Update list views with current skills."""
        builtin_list = self.query_one("#builtin_skills_list", ListView)
        plugin_list = self.query_one("#plugin_skills_list", ListView)

        # Clear existing items
        builtin_list.clear()
        plugin_list.clear()

        # Separate builtin and plugin skills
        builtin_skills = [s for s in self.skills if s.manifest.is_builtin]
        plugin_skills = [s for s in self.skills if not s.manifest.is_builtin]

        # Add builtin skills
        for skill in builtin_skills:
            builtin_list.append(SkillListItem(skill))

        # Add plugin skills
        for skill in plugin_skills:
            plugin_list.append(SkillListItem(skill))

    def _update_stats(self) -> None:
        """Update statistics display."""
        total = len(self.skills)
        enabled = sum(1 for s in self.skills if s.enabled)
        total_usage = sum(s.usage_count for s in self.skills)

        stats = self.query_one("#skills_stats", Label)
        stats.update(f"Total: {total} | Enabled: {enabled} | Executions: {total_usage}")

    @on(Button.Pressed, "#install_plugin_btn")
    def on_install_plugin(self) -> None:
        """Handle install plugin button."""
        # For now, just show a notification
        # In full implementation, would open file picker
        self.notify("Plugin installation: Place plugin in .nju_code/plugins/ directory")

    @on(Button.Pressed, "#audit_log_btn")
    def on_audit_log(self) -> None:
        """Handle audit log button."""
        self.post_message(AuditLogRequested())

    @on(ListView.Selected)
    def on_skill_selected(self, event: ListView.Selected) -> None:
        """Handle skill selection - toggle enable/disable."""
        if isinstance(event.item, SkillListItem):
            skill = event.item.skill_toggle
            new_enabled = not skill.enabled
            self.post_message(SkillToggled(skill.skill_id, new_enabled))