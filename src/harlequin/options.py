from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

import click


class AbstractOption(ABC):
    def __init__(
        self,
        name: str,
        description: str,
        *args: Any,
        label: str | None = None,
        short_decls: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        if short_decls is None:
            short_decls = []
        self.name = name
        self.description = description
        self.label = label
        self.short_decls = short_decls

    @abstractmethod
    def to_click(self) -> Callable[[click.Command], click.Command]:
        pass


class TextOption(AbstractOption):
    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: list[str] | None = None,
        validator: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(name, description, label=label, short_decls=short_decls)
        self.validator = validator

    def to_click(self) -> Callable[[click.Command], click.Command]:
        def click_callback(
            ctx: click.Context, param: click.ParamType, value: str
        ) -> str:
            if self.validator is not None:
                try:
                    value = self.validator(value)
                except Exception as e:
                    raise click.BadParameter(str(e)) from e
            return value

        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            callback=click_callback,
        )


class ListOption(AbstractOption):
    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: list[str] | None = None,
    ) -> None:
        super().__init__(name, description, label=label, short_decls=short_decls)

    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            multiple=True,
        )


class PathOption(AbstractOption):
    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: list[str] | None = None,
        exists: bool = False,
        file_okay: bool = True,
        dir_okay: bool = True,
        resolve_path: bool = False,
        path_type: type | None = Path,
    ) -> None:
        super().__init__(name, description, label=label, short_decls=short_decls)
        self.exists = exists
        self.file_okay = file_okay
        self.dir_okay = dir_okay
        self.resolve_path = resolve_path
        self.path_type = path_type

    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            type=click.Path(
                exists=self.exists,
                file_okay=self.file_okay,
                dir_okay=self.dir_okay,
                resolve_path=self.resolve_path,
                path_type=self.path_type,
            ),
        )


class SelectOption(AbstractOption):
    def __init__(
        self,
        name: str,
        description: str,
        choices: list[str],
        label: str | None = None,
        short_decls: list[str] | None = None,
    ) -> None:
        super().__init__(name, description, label=label, short_decls=short_decls)
        self.choices = choices

    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            type=click.Choice(choices=self.choices, case_sensitive=False),
        )


class FlagOption(AbstractOption):
    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}", *self.short_decls, help=self.description, is_flag=True
        )


HarlequinAdapterOption = AbstractOption
HarlequinCopyOption = AbstractOption
