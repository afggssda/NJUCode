"""Patch Panel - TUI widget for patch preview, confirmation, and rollback.

Provides:
- PatchPanel: Textual Vertical widget shown in the "Patch" tab
- PatchConfirmRequested: message emitted when user clicks Confirm
- PatchRollbackRequested: message emitted when user clicks Rollback
- PatchCancelRequested: message emitted when user clicks Cancel
"""

from __future__ import annotations

from typing import List, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.message import Message
from textual.widgets import Button, Label, ListItem, ListView, Static


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class PatchConfirmRequested(Message):
    """User confirmed a patch task — app.py should call apply_patch()."""
    def __init__(self, task_id: str) -> None:
        super().__init__()
        self.task_id = task_id


class PatchRollbackRequested(Message):
    """User requested rollback of a patch task."""
    def __init__(self, task_id: str, confirmed: bool = False) -> None:
        super().__init__()
        self.task_id = task_id
        self.confirmed = confirmed


class PatchCancelRequested(Message):
    """User cancelled a pending patch task."""
    def __init__(self, task_id: str) -> None:
        super().__init__()
        self.task_id = task_id


class PatchRefreshRequested(Message):
    """User clicked Refresh — app.py should reload patch history."""
    pass


# ---------------------------------------------------------------------------
# PatchPanel widget
# ---------------------------------------------------------------------------

class PatchPanel(Vertical):
    """Patch management panel.

    Layout (top to bottom):
      ┌─ Title bar ──────────────────────────────────────────┐
      │  [Refresh]                                           │
      ├─ Pending tasks list (ListView) ─────────────────────┤
      ├─ Diff preview (RichLog, read-only) ─────────────────┤
    ├─ Action buttons: [Apply] [Cancel] [Rollback] ──────┤
      ├─ History section title ──────────────────────────────┤
      └─ History list (ListView) ───────────────────────────┘
    """

    DEFAULT_CSS = """
    PatchPanel {
        padding: 0 1;
        height: 1fr;
        overflow: hidden;
    }
    #patch_scroll {
        height: 1fr;
        overflow-y: auto;
        overflow-x: auto;
    }
    #patch_inner {
        height: auto;
        min-height: 100%;
        min-width: 60;
    }
    #patch_title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    #patch_modify_title {
        color: $warning;
        text-style: bold;
        margin-top: 1;
    }
    #patch_create_title {
        color: $success;
        text-style: bold;
        margin-top: 1;
    }
    #patch_history_title {
        color: $accent;
        text-style: bold;
        margin-top: 1;
    }
    #patch_modify_list {
        height: 5;
        min-height: 3;
        border: solid $panel;
        margin-bottom: 1;
    }
    #patch_create_list {
        height: 5;
        min-height: 3;
        border: solid $panel;
        margin-bottom: 1;
    }
    #patch_diff_preview {
        height: 12;
        min-height: 6;
        border: solid $panel;
        margin-bottom: 1;
        padding: 0;
    }
    #patch_action_row {
        height: auto;
        margin-bottom: 1;
    }
    #patch_history_list {
        height: 8;
        min-height: 4;
        border: solid $panel;
    }
    #patch_status_label {
        color: $success;
        margin-top: 1;
        height: auto;
    }
    Button {
        margin-right: 1;
        min-width: 14;
        width: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._selected_task_id: Optional[str] = None
        self._modify_tasks: List[dict] = []   # pending modify/delete patches
        self._create_tasks: List[dict] = []   # pending create patches
        self._history_tasks: List[dict] = []  # list of {task_id, summary, reversible}
        self._pending_rollback_confirm: Optional[str] = None  # task_id awaiting delete confirmation

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="patch_scroll"):
            with Vertical(id="patch_inner"):
                yield Label("Modify / Delete Patches", id="patch_modify_title")
                yield ListView(id="patch_modify_list")

                yield Label("New File Patches", id="patch_create_title")
                yield ListView(id="patch_create_list")

                yield Label("Diff Preview", id="patch_diff_label")
                with ScrollableContainer(id="patch_diff_preview"):
                    yield Static("", id="patch_diff_content", markup=True)

                with Horizontal(id="patch_action_row"):
                    yield Button("Apply", id="patch_confirm_btn", variant="success", disabled=True)
                    yield Button("Cancel", id="patch_cancel_btn", variant="error", disabled=True)
                    yield Button("Rollback", id="patch_rollback_btn", variant="warning", disabled=True)

                yield Label("", id="patch_status_label")

                yield Label("Patch History", id="patch_history_title")
                yield ListView(id="patch_history_list")

    # ------------------------------------------------------------------
    # Mount
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API — called by app.py to push data into the panel
    # ------------------------------------------------------------------

    def load_pending(self, tasks: List[dict]) -> None:
        """Populate the modify and create task lists.

        Each task dict: {task_id, summary, description, operation_type}
        operation_type: "create" goes to the New File list; everything else to Modify/Delete.
        """
        self._modify_tasks = [t for t in tasks if t.get("operation_type") != "create"]
        self._create_tasks = [t for t in tasks if t.get("operation_type") == "create"]

        for list_id, task_list, empty_msg in (
            ("#patch_modify_list", self._modify_tasks, "  (no modify/delete patches)"),
            ("#patch_create_list", self._create_tasks, "  (no new-file patches)"),
        ):
            lv = self.query_one(list_id, ListView)
            lv.clear()
            if not task_list:
                lv.append(ListItem(Label(empty_msg)))
                continue
            for task in task_list:
                label_text = task.get("summary", task.get("task_id", "")[:8])
                item = ListItem(Label(f"  {label_text}"))
                item.data = task.get("task_id", "")  # type: ignore[attr-defined]
                lv.append(item)

    def load_history(self, tasks: List[dict]) -> None:
        """Populate the history list.

        Each task dict: {task_id, summary, reversible}
        """
        self._history_tasks = tasks
        list_view = self.query_one("#patch_history_list", ListView)
        list_view.clear()
        if not tasks:
            list_view.append(ListItem(Label("  (no history)")))
            return
        for task in tasks:
            label_text = task.get("summary", task.get("task_id", "")[:8])
            item = ListItem(Label(f"  {label_text}"))
            item.data = task.get("task_id", "")  # type: ignore[attr-defined]
            list_view.append(item)

    def show_diff(self, diff_text: str) -> None:
        """Display diff text with green/red coloring for added/removed lines."""
        try:
            static = self.query_one("#patch_diff_content", Static)
            text = diff_text or "(no diff available)"
            colored_lines = []
            for line in text.splitlines():
                if not line:
                    continue
                escaped = line.replace("[", "\\[")
                if line.startswith("+") and not line.startswith("+++"):
                    colored_lines.append(f"[green]{escaped}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    colored_lines.append(f"[red]{escaped}[/red]")
                else:
                    colored_lines.append(escaped)
            static.update("\n".join(colored_lines))
        except Exception:
            pass

    def set_status(self, message: str, error: bool = False) -> None:
        """Update the status label."""
        try:
            label = self.query_one("#patch_status_label", Label)
            label.update(message)
            if error:
                label.add_class("error")
                label.remove_class("success")
            else:
                label.remove_class("error")
                label.add_class("success")
        except Exception:
            pass

    def _set_action_buttons(self, enabled: bool) -> None:
        """Enable or disable the apply/cancel buttons."""
        for btn_id in ("#patch_confirm_btn", "#patch_cancel_btn"):
            try:
                btn = self.query_one(btn_id, Button)
                btn.disabled = not enabled
            except Exception:
                pass

    def _set_rollback_button(self, enabled: bool) -> None:
        try:
            btn = self.query_one("#patch_rollback_btn", Button)
            btn.disabled = not enabled
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle selection in either pending or history list."""
        item = event.item
        task_id = getattr(item, "data", None)
        if not task_id:
            return

        list_view_id = event.list_view.id

        if list_view_id in ("patch_modify_list", "patch_create_list"):
            self._selected_task_id = task_id
            self._set_action_buttons(True)
            self._set_rollback_button(False)
            # Request diff for the selected task specifically
            self.post_message(_PatchPreviewRequested(task_id))

        elif list_view_id == "patch_history_list":
            self._selected_task_id = task_id
            self._set_action_buttons(False)
            # Check if this task is reversible
            reversible = False
            for t in self._history_tasks:
                if t.get("task_id") == task_id:
                    reversible = t.get("reversible", False)
                    # Show diff from history
                    diff = t.get("diff", "")
                    if diff:
                        self.show_diff(diff)
                    break
            self._set_rollback_button(reversible)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Route button presses to appropriate messages."""
        btn_id = event.button.id

        if btn_id == "patch_refresh_btn":
            self.post_message(PatchRefreshRequested())
            return

        if not self._selected_task_id:
            self.set_status("No patch selected.", error=True)
            return

        if btn_id == "patch_confirm_btn":
            self.post_message(PatchConfirmRequested(self._selected_task_id))

        elif btn_id == "patch_cancel_btn":
            self.post_message(PatchCancelRequested(self._selected_task_id))
            self._selected_task_id = None
            self._set_action_buttons(False)

        elif btn_id == "patch_rollback_btn":
            confirmed = (self._pending_rollback_confirm == self._selected_task_id)
            self._pending_rollback_confirm = None
            self.post_message(PatchRollbackRequested(self._selected_task_id, confirmed=confirmed))
            self._set_rollback_button(False)


class _PatchPreviewRequested(Message):
    """Internal: user selected a pending task — app.py should show its diff."""
    def __init__(self, task_id: str) -> None:
        super().__init__()
        self.task_id = task_id
