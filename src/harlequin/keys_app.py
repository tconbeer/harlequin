from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from typing import Any

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.driver import Driver
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Static, Label
from textual_fastdatatable import DataTable

from harlequin.actions import HARLEQUIN_ACTIONS
from harlequin.app_base import AppBase
from harlequin.config import get_config_for_profile
from harlequin.keymap import HarlequinKeyBinding
from harlequin.plugins import load_keymap_plugins
from harlequin.copy_widgets import NoFocusLabel

INSTRUCTIONS = dedent(
    """
This is how you use this app. (Maybe delete this).
"""
).strip()


class BindingsReady(Message):
    def __init__(
        self,
        bindings: dict[str, HarlequinKeyBinding],
        table_data: list[tuple[str, str, str]],
    ) -> None:
        super().__init__()
        self.bindings = bindings
        self.table_data = table_data

class BindingEdited(Message):
    def __init__(self, updated_binding: HarlequinKeyBinding) -> None:
        super().__init__()
        self.updated_binding=updated_binding

class BindingTable(DataTable, inherit_bindings=False):
    BINDINGS = [
        Binding("enter", "select_cursor", "Edit", show=True),
        Binding("up", "cursor_up", "Cursor Up", show=False),
        Binding("down", "cursor_down", "Cursor Down", show=False),
        Binding("ctrl+up", "scroll_home", "Home", show=False),
        Binding("ctrl+down", "scroll_end", "End", show=False),
        Binding("pageup", "page_up", "Page Up", show=False),
        Binding("pagedown", "page_down", "Page Down", show=False),
        Binding("home", "scroll_home", "Home", show=False),
        Binding("end", "scroll_end", "End", show=False),
        Binding("ctrl+home", "cursor_table_start", "Home", show=False),
        Binding("ctrl+end", "cursor_table_end", "End", show=False),
    ]

    def __init__(
        self,
        *,
        id: str | None = None,  # noqa: A002
        column_labels: list[str | Text] | None = None,
        data: Any | None = None,
    ) -> None:
        super().__init__(
            id=id,
            column_labels=column_labels,
            data=data,
            cursor_type="row",
        )


class EditModal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, binding: HarlequinKeyBinding) -> None:
        super().__init__()
        self.binding = binding

    def compose(self) -> ComposeResult:
        outer = Vertical(id="outer")
        self.keys_container = Vertical()
        with outer:
            with Horizontal(classes="option_row"):
                yield NoFocusLabel("Action:")
                yield Label(format_action(self.binding.action), id="action_label")
            with self.keys_container:
                for key in self.binding.keys.split(","):
                    with Horizontal(classes="option_row"):
                        yield NoFocusLabel("Key:")
                        yield Button(label=key, classes="key")
                        yield Button(label="X", classes="key_btn")
            with Horizontal(classes="option_row"):
                yield NoFocusLabel("Key Display:")
                yield Input(
                    value=self.binding.key_display if self.binding.key_display else None,
                    placeholder="(Optional)",
                    id="key_display_input"
                )
            with Horizontal(id="button_row"):
                yield Button(label="Cancel", variant="error", id="cancel")
                yield Button(label="Submit", variant="primary", id="submit")

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def action_submit(self) -> None:
        self.post_message(BindingEdited(updated_binding=self.binding))

    @on(Button.Pressed, "#cancel")
    def cancel_modal(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#submit")
    def submit(self) -> None:
        self.action_submit()


class HarlequinKeys(AppBase):
    CSS_PATH = ["global.tcss", "keys_app.tcss"]

    def __init__(
        self,
        *,
        theme: str | None = "harlequin",
        config_path: Path | None = None,
        profile_name: str | None = None,
        driver_class: type[Driver] | None = None,
        watch_css: bool = False,
    ):
        super().__init__(
            theme=theme,
            driver_class=driver_class,
            watch_css=watch_css,
        )
        self.config_path = config_path
        self.profile_name = profile_name
        self.bindings: dict[str, HarlequinKeyBinding] | None = None
        self.unmodifed_bindings: dict[str, HarlequinKeyBinding] | None = None
        self.table: DataTable | None = None

    def on_mount(self) -> None:
        self.load_bindings()

    def compose(self) -> ComposeResult:
        self.instructions = Static(INSTRUCTIONS, id="instructions")
        self.search_input = Input(
            value=None, placeholder="Search keybindings", id="search_input"
        )
        yield self.instructions
        yield self.search_input
        yield Footer()

    def push_edit_modal(self, binding: HarlequinKeyBinding) -> None:
        if len(self.screen_stack) == 1:
            self.push_screen(EditModal(binding=binding))

    @on(BindingsReady)
    def mount_bindings_table(self, message: BindingsReady) -> None:
        self.unmodifed_bindings = self.bindings = message.bindings
        self.table = BindingTable(
            column_labels=["Action", "Keys", "Key Display"], data=message.table_data
        )
        self.mount(self.table)

    @on(DataTable.RowSelected)
    def show_edit_modal(self, message: DataTable.RowSelected) -> None:
        if self.table is None or self.bindings is None:
            return
        action_name = self.table.get_cell_at(Coordinate(message.cursor_row, 0))
        binding = self.bindings[action_name]
        self.push_edit_modal(binding=binding)

    @work(
        thread=True,
        exclusive=True,
        exit_on_error=True,
        group="binding_loaders",
        description="Loading bindings from plug-ins and config",
    )
    def load_bindings(self) -> None:
        displayed_bindings = {
            format_action(action): HarlequinKeyBinding(
                keys="", action=action, key_display=""
            )
            for action in HARLEQUIN_ACTIONS
        }

        profile, user_keymaps = get_config_for_profile(
            config_path=self.config_path, profile_name=self.profile_name
        )
        all_keymaps = load_keymap_plugins(user_defined_keymaps=user_keymaps)
        profile_keymap_names = profile.get("keymap_name")
        active_keymap_names = (
            profile_keymap_names if profile_keymap_names else ["vscode"]
        )
        for keymap_name in active_keymap_names:
            # TODO: handle errors
            keymap = all_keymaps[keymap_name]
            for binding in keymap.bindings:
                merged_action = displayed_bindings[format_action(binding.action)]
                if merged_action.keys:
                    merged_action.keys += f",{binding.keys}"
                else:
                    merged_action.keys = binding.keys
                if binding.key_display:
                    merged_action.key_display = binding.key_display

        table_data: list[tuple[str, str, str]] = []
        for formatted_name, binding in displayed_bindings.items():
            table_data.append(
                (
                    formatted_name,
                    binding.keys,
                    binding.key_display or "",
                )
            )
        self.post_message(
            BindingsReady(bindings=displayed_bindings, table_data=table_data)
        )


def format_action(action: str) -> str:
    component_name, _, action_name = action.rpartition(".")
    component_display_name = (
        f'{" ".join(w.capitalize() for w in component_name.split("_"))}: '
        if component_name
        else ""
    )
    action_display_name = " ".join(w.capitalize() for w in action_name.split("_"))
    return f"{component_display_name}{action_display_name}"
