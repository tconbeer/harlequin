"""The commands Harlequin's command palette (ctrl+p) offers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from harlequin.app import Harlequin


@dataclass(frozen=True)
class PaletteCommand:
    title: str
    help: str
    action: str
    """A key of `harlequin.actions.HARLEQUIN_ACTIONS`, so its bound key can be shown."""
    over_modals: bool = False
    """Also offered over a modal screen, not only on the main one."""
    is_available: Callable[[Harlequin], bool] | None = None


def _can_cancel(app: Harlequin) -> bool:
    return app.adapter.IMPLEMENTS_CANCEL


def _has_transaction_mode(app: Harlequin) -> bool:
    return app.transaction_mode is not None


def _can_commit(app: Harlequin) -> bool:
    return app.transaction_mode is not None and app.transaction_mode.commit is not None


def _can_rollback(app: Harlequin) -> bool:
    return (
        app.transaction_mode is not None and app.transaction_mode.rollback is not None
    )


def _has_many_buffers(app: Harlequin) -> bool:
    return app.editor_collection.tab_count > 1


PALETTE_COMMANDS: tuple[PaletteCommand, ...] = (
    # app
    PaletteCommand("Help", "Show Harlequin's help", "help"),
    PaletteCommand(
        "Keys",
        "Show or hide the keys for the focused widget",
        "toggle_keys_panel",
        over_modals=True,
    ),
    PaletteCommand(
        "Change Theme", "Pick a new theme", "change_theme", over_modals=True
    ),
    PaletteCommand(
        "Debug Info",
        "Show your config, adapter and connection details",
        "show_debug_info",
    ),
    PaletteCommand("Quit", "Quit Harlequin", "quit", over_modals=True),
    # queries
    PaletteCommand(
        "Run Query",
        "Run the query under the cursor or selection",
        "code_editor.run_query",
    ),
    PaletteCommand(
        "Cancel Query",
        "Cancel the running query",
        "cancel_query",
        is_available=_can_cancel,
    ),
    PaletteCommand(
        "Format Query", "Format the current buffer", "code_editor.format_buffer"
    ),
    PaletteCommand(
        "Query History", "Browse and reopen queries you have run", "show_query_history"
    ),
    PaletteCommand(
        "Export Data", "Export the visible results to a file", "show_data_exporter"
    ),
    # transactions
    PaletteCommand(
        "Toggle Transaction Mode",
        "Switch to the adapter's next transaction mode",
        "toggle_transaction_mode",
        is_available=_has_transaction_mode,
    ),
    PaletteCommand(
        "Commit Transaction",
        "Commit the open transaction",
        "commit_transaction",
        is_available=_can_commit,
    ),
    PaletteCommand(
        "Rollback Transaction",
        "Roll back the open transaction",
        "rollback_transaction",
        is_available=_can_rollback,
    ),
    # buffers
    PaletteCommand("New Buffer", "Open a new buffer", "code_editor.new_buffer"),
    PaletteCommand(
        "Close Buffer", "Close the current buffer", "code_editor.close_buffer"
    ),
    PaletteCommand(
        "Close All Buffers",
        "Close every buffer and start over with an empty one",
        "code_editor.close_all_buffers",
    ),
    PaletteCommand(
        "Next Buffer",
        "Switch to the next buffer",
        "code_editor.next_buffer",
        is_available=_has_many_buffers,
    ),
    PaletteCommand(
        "Save Query", "Save the current buffer to a file", "code_editor.save_buffer"
    ),
    PaletteCommand(
        "Open Query", "Open a file in the current buffer", "code_editor.load_buffer"
    ),
    PaletteCommand(
        "Open in External Editor",
        "Edit the current buffer in $EDITOR",
        "code_editor.launch_external_editor",
    ),
    PaletteCommand("Find", "Find text in the current buffer", "code_editor.find"),
    PaletteCommand(
        "Go To Line", "Move the cursor to a line number", "code_editor.goto_line"
    ),
    # layout
    PaletteCommand(
        "Focus Query Editor", "Move focus to the Query Editor", "focus_query_editor"
    ),
    PaletteCommand(
        "Focus Results Viewer",
        "Move focus to the Results Viewer",
        "focus_results_viewer",
    ),
    PaletteCommand(
        "Focus Data Catalog", "Move focus to the Data Catalog", "focus_data_catalog"
    ),
    PaletteCommand(
        "Toggle Data Catalog", "Show or hide the Data Catalog", "toggle_sidebar"
    ),
    PaletteCommand(
        "Toggle Full Screen",
        "Maximize the focused panel, or restore the layout",
        "toggle_full_screen",
    ),
    PaletteCommand(
        "Refresh Data Catalog",
        "Reload databases, files and S3 objects",
        "refresh_catalog",
    ),
)
