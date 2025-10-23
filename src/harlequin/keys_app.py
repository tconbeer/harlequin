from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence, Tuple, Union

from platformdirs import user_config_path
from rich.panel import Panel
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
from textual.validation import ValidationResult, Validator
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, Label, LoadingIndicator, Static
from textual_fastdatatable import DataTable

from harlequin.actions import HARLEQUIN_ACTIONS
from harlequin.app_base import AppBase
from harlequin.colors import YELLOW
from harlequin.config import (
    ConfigFile,
    get_config_for_profile,
    get_highest_priority_existing_config_file,
)
from harlequin.copy_widgets import NoFocusLabel, PathInput
from harlequin.exception import HarlequinError, pretty_error_message
from harlequin.keymap import HarlequinKeyBinding
from harlequin.plugins import load_keymap_plugins


class ActiveKeymapFound(Message):
    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.keymap_names = names


class PluginKeymapsFound(Message):
    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.keymap_names = names


class BindingsReady(Message):
    def __init__(
        self,
        bindings: dict[str, HarlequinKeyBinding],
        table_data: list[tuple[str, str, str]],
    ) -> None:
        super().__init__()
        self.bindings = bindings
        self.table_data = table_data


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


class KeymapNameValidator(Validator):
    def __init__(
        self,
        plugin_names: Sequence[str],
    ) -> None:
        super().__init__(
            failure_description="Cannot use the name of an existing keymap plug-in"
        )
        self.plugin_names = set(plugin_names)

    def validate(self, value: str) -> ValidationResult:
        if value in self.plugin_names:
            return self.failure()
        else:
            return self.success()


class QuitModal(ModalScreen[Tuple[bool, Union[Path, None], Union[str, None]]]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up,left", "focus_previous", "focus_previous", show=False),
        Binding("down,right", "focus_next", "focus_next", show=False),
    ]

    def __init__(
        self, config_path: Path | None, keymap_name: str | None, plugin_names: list[str]
    ) -> None:
        super().__init__()
        self.config_path = (
            config_path
            or get_highest_priority_existing_config_file()
            or user_config_path(appname="harlequin", appauthor=False) / "config.toml"
        )
        self.keymap_name = keymap_name
        self.plugin_names = plugin_names
        self.path_input_valid = False
        self.name_input_valid = False

    def compose(self) -> ComposeResult:
        self.path_input = PathInput(
            id="path_input",
            value=str(self.config_path),
            dir_okay=False,
            tab_advances_focus=True,
        )
        self.path_validation_label = Label(
            "", id="path_validation_label", classes="validation-label"
        )
        name_validator = KeymapNameValidator(plugin_names=self.plugin_names)
        validation_result = name_validator.validate(self.keymap_name or "")
        self.name_input = Input(
            id="name_input",
            value=self.keymap_name if validation_result.is_valid else None,
            placeholder="Enter a name for this keymap",
            validators=[name_validator],
        )
        self.name_validation_label = Label(
            "", id="name_validation_label", classes="validation-label"
        )
        self.save_button = Button(label="Save + Quit", variant="primary", id="submit")
        with Vertical(id="outer"):
            with Horizontal(classes="option_row"):
                yield NoFocusLabel("Config Path:")
                with Vertical():
                    yield self.path_input
                    yield self.path_validation_label
            with Horizontal(classes="option_row"):
                yield NoFocusLabel("Keymap Name:")
                with Vertical():
                    yield self.name_input
                    yield self.name_validation_label
            with Horizontal(id="button_row"):
                yield Button(label="Keep Editing", id="cancel")
                yield Button(label="Discard + Quit", variant="error", id="discard")
                yield self.save_button
        if validation_result.is_valid:
            self.save_button.focus()
        else:
            self.name_input.focus()

    @on(Input.Changed, "#path_input")
    def validate_path(self, event: Input.Changed) -> None:
        if event.validation_result:
            if event.validation_result.is_valid:
                self.path_validation_label.update("")
                self.path_input_valid = True
            else:
                self.path_validation_label.update(
                    " ".join(event.validation_result.failure_descriptions)
                )
                self.path_input_valid = False

    @on(Input.Changed, "#name_input")
    def validate_name(self, event: Input.Changed) -> None:
        if event.validation_result:
            if event.validation_result.is_valid:
                self.name_validation_label.update("")
                self.name_input_valid = True
            else:
                self.name_validation_label.update(
                    " ".join(event.validation_result.failure_descriptions)
                )
                self.name_input_valid = False

    @on(Input.Submitted)
    def save_from_input(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#submit")
    def save_from_button(self) -> None:
        self.action_save()

    @on(Button.Pressed, "#discard")
    def discard(self) -> None:
        self.dismiss((True, None, None))

    @on(Button.Pressed, "#cancel")
    def cancel(self) -> None:
        self.action_cancel()

    def action_save(self) -> None:
        if self.path_input_valid and self.name_input_valid:
            self.dismiss((True, Path(self.path_input.value), self.name_input.value))

    def action_cancel(self) -> None:
        self.dismiss((False, None, None))


class InputModal(ModalScreen[str], inherit_bindings=False):
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
        Binding("up,left", "focus_previous", "focus_previous", show=False),
        Binding("down,right", "focus_next", "focus_next", show=False),
    ]

    def __init__(self, binding: HarlequinKeyBinding) -> None:
        super().__init__()
        self.binding = binding

    def compose(self) -> ComposeResult:
        outer = Vertical(id="outer")
        with outer:
            yield Static(
                (
                    f"[b]Action: {format_action(self.binding.action)}[/b]\n\n"
                    "[dim][i]Use the buttons below to replace, remove, or add "
                    "bindings for this action.[/i]\n"
                    "[b]Enter[/b]: Select Button; [b]Tab[/b]: Next Button[/dim]"
                ),
                id="instructions",
            )
            for key in [key for key in self.binding.keys.split(",") if key]:
                with Horizontal(classes="option_row"):
                    yield NoFocusLabel("Key:")
                    yield EditButton(key=key).focus()
                    yield RemoveButton(key=key)
            with Horizontal(classes="option_row", id="add_row"):
                yield NoFocusLabel("")
                yield AddButton(label="Add Key")
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

    def action_cancel(self) -> None:
        self.dismiss(result=self.binding)

    def action_submit(self) -> None:
        keys = ",".join(sorted({btn.key for btn in self.query(EditButton)}))
        key_display: str | None = self.query_one(
            "#key_display_input", expect_type=Input
        ).value
        if not key_display:
            key_display = None
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

            def edit_button(new_key: str | None) -> None:
                if new_key is None:
                    return
                assert isinstance(message.button, EditButton)
                message.button.label = new_key
                message.button.key = new_key

            self.app.push_screen(InputModal(source_button=message.button), edit_button)
            return

        elif isinstance(message.button, AddButton):

            def add_button(key: str | None) -> None:
                if key is None:
                    return
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


class HarlequinKeys(AppBase):
    BINDINGS = [Binding("ctrl+q", "quit", "Quit")]
    CSS_PATH = ["global.tcss", "keys_app.tcss"]

    def __init__(
        self,
        *,
        theme: str | None = "harlequin",
        config_path: Path | None = None,
        profile_name: str | None = None,
        keymap_name: list[str] | None = None,
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
        self.plugin_keymap_names: list[str] | None = None
        self.active_keymap_names = keymap_name if keymap_name else None
        self.bindings: dict[str, HarlequinKeyBinding] | None = None
        self.unmodifed_bindings: dict[str, HarlequinKeyBinding] | None = None
        self.table: DataTable | None = None

    def on_mount(self) -> None:
        self.load_bindings()

    def compose(self) -> ComposeResult:
        self.loading_indicator: LoadingIndicator | None = LoadingIndicator()
        yield self.loading_indicator
        # yield self.search_input
        yield Footer(show_command_palette=False)

    def push_edit_modal(self, binding: HarlequinKeyBinding, cursor_row: int) -> None:
        def update_binding(new_binding: HarlequinKeyBinding | None) -> None:
            if new_binding is None:
                return
            assert self.bindings is not None
            assert self.table is not None
            k = format_action(new_binding.action)
            existing_binding = self.bindings[k]
            self.bindings[k] = new_binding
            if existing_binding.keys != new_binding.keys:
                self.table.update_cell(
                    row_index=cursor_row,
                    column_index=1,
                    value=new_binding.keys,
                    update_width=True,
                )
            if existing_binding.key_display != new_binding.key_display:
                self.table.update_cell(
                    row_index=cursor_row,
                    column_index=2,
                    value=new_binding.key_display or "",
                    update_width=True,
                )

        if len(self.screen_stack) == 1:
            self.push_screen(EditModal(binding=binding), update_binding)

    @on(PluginKeymapsFound)
    def register_plugin_keymap_names(self, message: PluginKeymapsFound) -> None:
        self.plugin_keymap_names = message.keymap_names

    @on(ActiveKeymapFound)
    def update_keymap_name(self, message: ActiveKeymapFound) -> None:
        self.active_keymap_names = message.keymap_names

    @on(BindingsReady)
    def mount_bindings_table(self, message: BindingsReady) -> None:
        self.unmodifed_bindings = message.bindings
        self.bindings = self.unmodifed_bindings.copy()
        self.instructions = Static(
            "[i]The table below shows the active key bindings loaded from your "
            "config. Edit the key bindings, then quit to save them to a keymap.[/i]\n"
            f"[b]Loaded Keymaps: {', '.join(self.active_keymap_names or [])}[/b]",
            id="instructions",
        )
        self.table = BindingTable(
            column_labels=["Action", "Keys", "Key Display"], data=message.table_data
        )
        if self.loading_indicator is not None:
            self.loading_indicator.remove()
            self.loading_indicator = None
        self.mount(self.instructions)
        self.mount(self.table)
        self.table.focus()

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

        try:
            profile, user_keymaps = get_config_for_profile(
                config_path=self.config_path, profile_name=self.profile_name
            )
        except HarlequinError as e:
            self.exit(return_code=2, message=pretty_error_message(e))
        all_keymaps = load_keymap_plugins(user_defined_keymaps=user_keymaps)
        plugin_keymap_names = [
            keymap.name
            for keymap in all_keymaps.values()
            if keymap.name not in [k.name for k in user_keymaps]
        ]
        self.post_message(PluginKeymapsFound(names=plugin_keymap_names))
        profile_keymap_names = profile.get("keymap_name")
        active_keymap_names = (
            self.active_keymap_names or profile_keymap_names or ["vscode"]
        )
        if self.active_keymap_names is None:
            self.post_message(ActiveKeymapFound(names=active_keymap_names))
        for keymap_name in active_keymap_names:
            keymap = all_keymaps.get(keymap_name)
            if keymap is None:
                continue
            for binding in keymap.bindings:
                merged_action = displayed_bindings[format_action(binding.action)]
                deduped_keys = (
                    set(merged_action.keys.split(","))
                    .union(set(binding.keys.split(",")))
                    .difference(set([""]))
                )
                merged_action.keys = ",".join(sorted(deduped_keys))
                if binding.key_display:
                    merged_action.key_display = binding.key_display

        table_data = format_bindings_for_table(displayed_bindings=displayed_bindings)
        self.post_message(
            BindingsReady(bindings=displayed_bindings, table_data=table_data)
        )

    async def action_quit(self) -> None:
        if (
            (not self.active_keymap_names)
            or self.bindings is None
            or self.unmodifed_bindings is None
            or self.plugin_keymap_names is None
        ):
            # app hasn't finished loading yet, just quit!
            await super().action_quit()
            return  # for mypy
        modified_bindings = [
            new_binding
            for new_binding, old_binding in zip(
                self.bindings.values(), self.unmodifed_bindings.values(), strict=False
            )
            if new_binding != old_binding
        ]
        if not modified_bindings:
            await super().action_quit()
            return  # for mypy

        def maybe_save(
            screen_data: tuple[bool, Path | None, str | None] | None,
        ) -> None:
            if screen_data is None:
                return
            do_quit, config_path, keymap_name = screen_data
            if not do_quit:
                return
            if not config_path or not keymap_name:
                # the user pressed "Discard" instead of "Save"
                self.app.exit()
                return  # for mypy
            config_file = ConfigFile(path=config_path)
            keymaps = config_file.relevant_config.get("keymaps", {})
            assert self.bindings is not None
            keymaps.update(
                {
                    keymap_name: [
                        b.to_dict()
                        for b in self.bindings.values()
                        if b.keys or b.key_display
                    ]
                }
            )
            config_file.update({"keymaps": keymaps})
            config_file.write()
            self.app.exit(
                message=Panel.fit(
                    (
                        f"Keymap {keymap_name} written to file at {config_path}\n"
                        "To use this keymap, invoke harlequin with this option:\n"
                        f"harlequin --keymap-name {keymap_name}\n"
                        "Or add this line to a profile in your TOML config file:\n"
                        f"keymap_name=['{keymap_name}']"
                    ),
                    title="Keymap successfully created",
                    title_align="left",
                    border_style=YELLOW,
                )
            )

        self.push_screen(
            QuitModal(
                config_path=self.config_path,
                keymap_name=self.active_keymap_names[-1],
                plugin_names=self.plugin_keymap_names,
            ),
            maybe_save,
        )


def format_action(action: str) -> str:
    component_name, _, action_name = action.rpartition(".")
    component_display_name = (
        f"{' '.join(w.capitalize() for w in component_name.split('_'))}: "
        if component_name
        else ""
    )
    action_display_name = " ".join(w.capitalize() for w in action_name.split("_"))
    return f"{component_display_name}{action_display_name}"


def format_bindings_for_table(
    displayed_bindings: dict[str, HarlequinKeyBinding],
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
