from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

import tomlkit
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Markdown, Static

from harlequin.config import Config, Profile


class WidgetType(Enum):
    COLLAPSIBLE = "collapsible"
    MARKDOWN = "markdown"
    STATIC = "static"


class DebugWidget:
    def __init__(
        self,
        widget_type: WidgetType,
        title: str,
        content: Optional[Union[str, List["DebugWidget"]]] = None,
        collapsed: bool = True,
        id: Optional[str] = None,  # noqa: A002
    ):
        self.widget_type = widget_type
        self.title = title
        self.content = content
        self.collapsed = collapsed
        self.id = id


class VerticalSuppressClicks(Vertical):
    def on_click(self, message: events.Click) -> None:
        message.stop()


class HarlequinDebugInfo:
    def __init__(
        self,
        all_keymaps: Sequence[str],
        config: Config,
        config_path: str | Path | None,
        keymap_names: Sequence[str] | None = None,
        theme: str | None = None,
        active_profile_name: str | None = None,
        active_profile_config: Profile | None = None,
    ) -> None:
        self.all_keymaps = all_keymaps
        self.config = config
        self.config_path = config_path
        self.keymap_names = keymap_names
        self.theme = theme
        self.active_profile_name = active_profile_name
        self.active_profile_config = active_profile_config or {}

    def parse_info(self) -> List[DebugWidget]:
        try:
            config_toml = tomlkit.dumps(self.config).rstrip()
        except Exception:
            config_toml = str(self.config)
        try:
            profile_toml = tomlkit.dumps(self.active_profile_config).rstrip()
        except Exception:
            profile_toml = str(self.active_profile_config)
        details = [
            DebugWidget(
                widget_type=WidgetType.MARKDOWN,
                title="Active Profile",
                content=f"`{self.active_profile_name}`",
            ),
            DebugWidget(
                widget_type=WidgetType.MARKDOWN,
                title="Config File Location",
                content=f"`{self.config_path}`",
            ),
            DebugWidget(
                widget_type=WidgetType.MARKDOWN,
                title="Theme",
                content=f"`{self.theme}`",
            ),
            DebugWidget(
                widget_type=WidgetType.MARKDOWN,
                title="Active Keymaps",
                content=f"`{self.keymap_names}`",
            ),
            DebugWidget(
                widget_type=WidgetType.MARKDOWN,
                title="All Keymaps",
                content=f"`{self.all_keymaps}`",
            ),
            DebugWidget(
                widget_type=WidgetType.COLLAPSIBLE,
                title="Active Profile Config",
                content=[
                    DebugWidget(
                        widget_type=WidgetType.MARKDOWN,
                        title="",
                        content=f"```toml\n{profile_toml}\n```",
                    )
                ],
                collapsed=True,
                id="collapsible-active-profile-config",
            ),
            DebugWidget(
                widget_type=WidgetType.COLLAPSIBLE,
                title="Full Config",
                content=[
                    DebugWidget(
                        widget_type=WidgetType.MARKDOWN,
                        title="",
                        content=f"```toml\n{config_toml}\n```",
                    )
                ],
                collapsed=True,
                id="collapsible-full-config",
            ),
        ]
        return [
            DebugWidget(
                widget_type=WidgetType.COLLAPSIBLE,
                title="Harlequin Details",
                content=details,
                collapsed=True,
                id="collapsible-harlequin-details",
            )
        ]


class AdapterDebugInfo:
    def __init__(
        self,
        adapter_options: Any,
        adapter_type: str,
        adapter_details: Optional[str],
        adapter_driver_details: Optional[str],
        name: str | None = None,
        id: str | None = None,  # noqa: A002
    ) -> None:
        self.adapter_options = adapter_options
        self.adapter_type = adapter_type
        self.adapter_details = adapter_details
        self.adapter_driver_details = adapter_driver_details

    def parse_info(self) -> List[DebugWidget]:
        type_markdown = f"`{self.adapter_type}`" if self.adapter_type else ""
        details_markdown = str(self.adapter_details)
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
        details = [
            DebugWidget(
                widget_type=WidgetType.MARKDOWN, title="Type", content=type_markdown
            ),
            DebugWidget(
                widget_type=WidgetType.COLLAPSIBLE,
                title="Details",
                content=[
                    DebugWidget(
                        widget_type=WidgetType.MARKDOWN,
                        title="Adapter",
                        content=details_markdown,
                    ),
                    DebugWidget(
                        widget_type=WidgetType.MARKDOWN,
                        title="Driver",
                        content=self.adapter_driver_details,
                    ),
                ],
                collapsed=True,
                id="collapsible-adapter-details-section",
            ),
            DebugWidget(
                widget_type=WidgetType.COLLAPSIBLE,
                title="Adapter Options",
                content=[
                    DebugWidget(
                        widget_type=WidgetType.MARKDOWN,
                        title="",
                        content=options_markdown,
                    )
                ],
                collapsed=True,
                id="collapsible-adapter-options",
            ),
        ]
        return [
            DebugWidget(
                widget_type=WidgetType.COLLAPSIBLE,
                title="Adapter Details",
                content=details,
                collapsed=True,
                id="collapsible-adapter-details",
            )
        ]


class DebugInfoScreen(ModalScreen):
    def __init__(
        self,
        harlequin_details: List[DebugWidget],
        adapter_details: List[DebugWidget],
        name: str | None = None,
        id: str | None = None,  # noqa: A002
    ) -> None:
        super().__init__(name, id)
        self.harlequin_details = harlequin_details
        self.adapter_details = adapter_details

    header_text = """
        Details about the current Harlequin session, 
        environment, and adapter.
    """.split()

    def compose(self) -> ComposeResult:
        class FocusableCollapsible(Collapsible):
            can_focus = True

            def on_focus(self) -> None:
                self.add_class("focused")

            def on_blur(self) -> None:
                self.remove_class("focused")

        def render_widget(widget: DebugWidget) -> ComposeResult:
            if widget.widget_type == WidgetType.COLLAPSIBLE:
                with FocusableCollapsible(
                    title=widget.title,
                    collapsed=widget.collapsed,
                    id=widget.id,
                ):
                    if isinstance(widget.content, list):
                        for child in widget.content:
                            yield from render_widget(child)
            elif widget.widget_type == WidgetType.MARKDOWN:
                if widget.title:
                    yield Markdown(f"### {widget.title}\n{widget.content}")
                else:
                    yield Markdown(f"{widget.content}")
            elif widget.widget_type == WidgetType.STATIC:
                yield Static(str(widget.content), id=widget.id)

        with VerticalSuppressClicks(id="modal_outer"):
            yield Static(" ".join(self.header_text), id="modal_header")
            with VerticalScroll(id="debug_info_scroll"):
                for info in self.harlequin_details:
                    yield from render_widget(info)
                for info in self.adapter_details:
                    yield from render_widget(info)
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
            idx = focusables.index(current)  # type: ignore[arg-type]
        except ValueError:
            idx = 0
        next_idx = (idx + direction) % len(focusables)
        self.set_focus(focusables[next_idx])

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
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
