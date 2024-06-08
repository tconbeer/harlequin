from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import tomlkit
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static


class VerticalSuppressClicks(Vertical):
    def on_click(self, message: events.Click) -> None:
        message.stop()


class HarlequinDebugInfo:
    def __init__(
        self,
        active_profile: str | None,
        config_path: str | Path | None,
        theme: str,
        keymap_names: Sequence[str],
        all_keymaps: Sequence[str],
        config: dict[str, Any],
    ) -> None:
        self.active_profile = active_profile
        self.config_path = config_path
        self.theme = theme
        self.keymap_names = keymap_names
        self.all_keymaps = all_keymaps
        self.config = config

    def parse_info(self) -> list[tuple[str, object]]:
        try:
            config_toml = tomlkit.dumps(self.config).rstrip()
        except Exception:
            config_toml = str(self.config)
        return [
            (
                "Harlequin Details",
                [
                    ("Active Profile", f"`{self.active_profile}`"),
                    ("Config File Location", f"`{self.config_path}`"),
                    ("Theme", f"`{self.theme}`"),
                    ("Active Keymaps", f"`{self.keymap_names}`"),
                    ("All Keymaps", f"`{self.all_keymaps}`"),
                    ("Full Config", f"```toml\n{config_toml}\n```"),
                ],
            )
        ]


class AdapterDebugInfo:
    def __init__(
        self,
        adapter_options: Any,
        adapter_type: str,
        adapter_details: str,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        self.adapter_options = adapter_options
        self.adapter_type = adapter_type
        self.adapter_details = adapter_details

    def parse_info(self) -> list[tuple[str, object]]:
        type_markdown = f"`{self.adapter_type}`" if self.adapter_type else ""
        details_markdown = f"`{self.adapter_details}`"
        if self.adapter_options:
            table = ["| Flag(s) | Value |", "|---|---|"]
            for opt in self.adapter_options:
                flags = f"--{opt.name}"
                if getattr(opt, "short_decls", []):
                    flags += " " + " ".join(opt.short_decls)
                value = getattr(opt, "default", None)
                table.append(f"| `{flags}` | `{value}` |")
            options_markdown = "\n".join(table)
        else:
            options_markdown = "No adapter options defined."
        return [
            (
                "Adapter Details",
                [
                    ("Type", type_markdown),
                    ("Details", details_markdown),
                    ("Adapter Options", options_markdown),
                ],
            ),
        ]


class DebugInfoScreen(ModalScreen):
    def __init__(
        self,
        harlequin_details: list[tuple[str, object]],
        adapter_details: list[tuple[str, object]],
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.harlequin_details = harlequin_details
        self.adapter_details = adapter_details

    header_text = """
        Details about the current Harlequin session, 
        environment, and adapter.
    """.split()

    def compose(self) -> ComposeResult:
        from textual.widgets import Collapsible

        class FocusableCollapsible(Collapsible):
            can_focus = True

            def on_focus(self) -> None:
                self.add_class("focused")

            def on_blur(self) -> None:
                self.remove_class("focused")

        with VerticalSuppressClicks(id="modal_outer"):
            yield Static(" ".join(self.header_text), id="modal_header")
            with VerticalScroll(id="debug_info_scroll"):
                for title, content in self.harlequin_details:
                    if title == "Harlequin Details" and isinstance(content, list):
                        with FocusableCollapsible(
                            title=title,
                            collapsed=True,
                            id="collapsible-harlequin-details",
                        ):
                            for sub_title, sub_content in content:
                                if sub_title == "Full Config":
                                    with FocusableCollapsible(
                                        title=sub_title,
                                        collapsed=True,
                                        id="collapsible-full-config",
                                    ):
                                        yield Markdown(sub_content)
                                else:
                                    yield Markdown(f"### {sub_title}\n{sub_content}")
                for title, content in self.adapter_details:
                    if title == "Adapter Details" and isinstance(content, list):
                        with FocusableCollapsible(
                            title=title,
                            collapsed=True,
                            id="collapsible-adapter-details",
                        ):
                            for sub_title, sub_content in content:
                                if sub_title == "Details":
                                    with FocusableCollapsible(
                                        title=sub_title,
                                        collapsed=True,
                                        id="collapsible-client-adapter-details",
                                    ):
                                        yield Markdown(sub_content)
                                elif sub_title == "Adapter Options":
                                    with FocusableCollapsible(
                                        title=sub_title,
                                        collapsed=True,
                                        id="collapsible-adapter-options",
                                    ):
                                        yield Markdown(sub_content)
                                else:
                                    yield Markdown(f"### {sub_title}\n{sub_content}")
            yield Static(
                (
                    "Tab/Shift Tab to move focus, "
                    "Enter to expand/collapse, "
                    "Esc to close."
                ),
                id="modal_footer",
            )

    def on_mount(self) -> None:
        container = self.query_one("#modal_outer")
        container.border_title = "Debug Information"
        container.scroll_home()
        self.set_focus(self.query_one("#collapsible-harlequin-details"))

    def move_focus(self, direction: int) -> None:
        focusables = [
            self.query_one("#collapsible-harlequin-details"),
            self.query_one("#collapsible-full-config"),
            self.query_one("#collapsible-adapter-details"),
            self.query_one("#collapsible-client-adapter-details"),
            self.query_one("#collapsible-adapter-options"),
        ]
        current = self.focused
        try:
            idx = focusables.index(current)
        except ValueError:
            idx = 0
        next_idx = (idx + direction) % len(focusables)
        self.set_focus(focusables[next_idx])

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "esc"):
            self.app.pop_screen()
            event.stop()
        elif event.key == "pageup":
            container = self.query_one("#modal_outer")
            container.scroll_page_up()
            event.stop()
        elif event.key == "pagedown":
            container = self.query_one("#modal_outer")
            container.scroll_page_down()
            event.stop()

    def on_click(self) -> None:
        self.app.pop_screen()
