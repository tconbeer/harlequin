from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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
    def to_click(self) -> Callable:
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

    def to_click(self) -> Callable:
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

    def to_click(self) -> Callable:
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

    def to_click(self) -> Callable:
        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            type=click.Choice(choices=self.choices, case_sensitive=False),
        )


class FlagOption(AbstractOption):
    def to_click(self) -> Callable:
        return click.option(
            f"--{self.name}", *self.short_decls, help=self.description, is_flag=True
        )


@dataclass
class HarlequinAdapterOption:
    """
    A single option for an adapter's configuration.

    Args:
        name (str): The canonical name for this option.
        help (str): The text shown to the user when calling
            harlequin --help at the command line.
        click_decls (list[str]): A list of declarations for this option,
            e.g., ["--port", "-p"]. If empty, will use [f"--{name}"]
        click_kwargs (dict[str, Any]): Additional kwargs to be passed
            into click.option().
    """

    name: str
    description: str
    click_decls: list[str] = field(default_factory=list)
    click_kwargs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.click_kwargs.update({"help": self.description})

    @property
    def click_option(self) -> Callable:
        decls = self.click_decls if self.click_decls else [f"--{self.name}"]
        return click.option(*decls, **self.click_kwargs)


@dataclass
class HarlequinCopyOptions:
    pass
