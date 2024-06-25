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
from textual.events import Key
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label, Static
from textual_fastdatatable import DataTable

from harlequin.actions import HARLEQUIN_ACTIONS
from harlequin.app_base import AppBase
from harlequin.config import get_config_for_profile
from harlequin.copy_widgets import NoFocusLabel
from harlequin.keymap import HarlequinKeyBinding
from harlequin.plugins import load_keymap_plugins

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


class BindingKeyUpdated(Message):
    def __init__(self, source_button: Widget, key: str) -> None:
        super().__init__()
        self.source_button = source_button
        self.key = key


class EditButton(Button, inherit_bindings=False):
    BINDINGS = [Binding("enter", "press", "Edit Binding", show=True)]

    def __init__(self, key: str, classes: str | None = None):
        super().__init__(label=key, classes=classes)
        self.key = key


class RemoveButton(Button, inherit_bindings=False):
    BINDINGS = [Binding("enter", "press", "Remove Binding", show=True)]

    def __init__(self, key: str, classes: str | None = None):
        super().__init__(label="X", classes=classes)
        self.key = key


class AddButton(Button, inherit_bindings=False):
    BINDINGS = [Binding("enter", "press", "Add Binding", show=True)]


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


class InputModal(ModalScreen, inherit_bindings=False):

    def __init__(self, source_button: Widget) -> None:
        super().__init__()
        self.source_button = source_button

    def compose(self) -> ComposeResult:
        outer = Vertical(id="outer")
        with outer:
            yield NoFocusLabel("Press a key combination")

    @on(Key)
    def close_and_post_key(self, event: Key) -> None:
        event.stop()
        event.prevent_default()
        self.dismiss(result=event.key)


class EditModal(ModalScreen):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

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
                        yield EditButton(key=key).focus()
                        yield RemoveButton(key=key)
                with Horizontal(classes="option_row", id="add_row"):
                    yield NoFocusLabel("")
                    yield AddButton(label="[dim]<Add Binding>[/dim]")
            with Horizontal(classes="option_row"):
                yield NoFocusLabel("Key Display:")
                yield Input(
                    value=(
                        self.binding.key_display if self.binding.key_display else None
                    ),
                    placeholder="(Optional)",
                    id="key_display_input",
                )
            with Horizontal(id="button_row"):
                yield Button(label="Cancel", variant="error", id="cancel")
                yield Button(label="Submit", variant="primary", id="submit")
            yield Footer()

    def action_cancel(self) -> None:
        self.dismiss(result=self.binding)

    def action_submit(self) -> None:
        keys = ",".join({btn.key for btn in self.query(EditButton)})
        key_display = self.query_one("#key_display_input", expect_type=Input).value
        new_binding = HarlequinKeyBinding(
            keys=keys, action=self.binding.action, key_display=key_display
        )
        self.dismiss(result=new_binding)

    @on(Button.Pressed, "#cancel")
    def cancel_modal(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#submit")
    def submit(self) -> None:
        self.action_submit()

    @on(Button.Pressed)
    def handle_button_press(self, message: Button.Pressed) -> None:
        if isinstance(message.button, EditButton):

            def edit_button(new_key: str) -> None:
                assert isinstance(message.button, EditButton)
                message.button.label = new_key
                message.button.key = new_key

            self.app.push_screen(InputModal(source_button=message.button), edit_button)
            return

        elif isinstance(message.button, AddButton):

            def add_button(key: str) -> None:
                row = Horizontal(
                    NoFocusLabel("Key:"),
                    EditButton(key=key),
                    RemoveButton(key=key),
                    classes="option_row",
                )
                self.mount(row, before="#add_row")

            self.app.push_screen(InputModal(source_button=message.button), add_button)
            return

        elif isinstance(message.button, RemoveButton):
            row = message.button.parent
            assert isinstance(row, Horizontal)
            row.remove()
            return

    @on(BindingKeyUpdated)
    def update_button(self, message: BindingKeyUpdated) -> None:
        self.log("HERE!!")
        if isinstance(message.source_button, EditButton):
            message.source_button.label = message.key


class HarlequinKeys(AppBase):

    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]
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

    def push_edit_modal(self, binding: HarlequinKeyBinding, cursor_row: int) -> None:
        def update_binding(new_binding: HarlequinKeyBinding) -> None:
            assert self.bindings is not None
            assert self.table is not None
            k = format_action(new_binding.action)
            existing_binding = self.bindings[k]
            self.bindings[k] = new_binding
            if existing_binding.keys != new_binding.keys:
                self.table.update_cell(
                    row_index=cursor_row, column_index=1, value=new_binding.keys
                )
            if existing_binding.key_display != new_binding.key_display:
                self.table.update_cell(
                    row_index=cursor_row,
                    column_index=2,
                    value=new_binding.key_display or "",
                )

        if len(self.screen_stack) == 1:
            self.push_screen(EditModal(binding=binding), update_binding)

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
        self.push_edit_modal(binding=binding, cursor_row=message.cursor_row)

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

        table_data = format_bindings_for_table(displayed_bindings=displayed_bindings)
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


def format_bindings_for_table(
    displayed_bindings: dict[str, HarlequinKeyBinding]
) -> list[tuple[str, str, str]]:
    table_data: list[tuple[str, str, str]] = []
    for formatted_name, binding in displayed_bindings.items():
        table_data.append(
            (
                formatted_name,
                binding.keys,
                binding.key_display or "",
            )
        )
    return table_data
