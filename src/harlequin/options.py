from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Generator, Iterable, Sequence

import click
import questionary
from textual.validation import ValidationResult, Validator
from textual.widget import Widget

from harlequin.colors import HARLEQUIN_QUESTIONARY_STYLE
from harlequin.copy_widgets import (
    Input,
    NoFocusLabel,
    PathInput,
    Select,
    Switch,
)


class _CustomValidator(Validator):
    def __init__(
        self,
        validator: Callable[[str], tuple[bool, str | None]] | None = None,
        failure_description: str | None = None,
    ) -> None:
        super().__init__(failure_description)
        self.validator = validator or (lambda _: (True, ""))

    def validate(self, value: str) -> ValidationResult:
        try:
            is_valid, message = self.validator(value)
        except Exception as e:
            return self.failure(str(e))

        if is_valid:
            return self.success()
        else:
            return self.failure(message or "Validation failed.")


def concatenate(first: str, second: str) -> str:
    if first == second:
        return first
    return f"{first}\n----or----\n{second}"


class AbstractOption(ABC):
    """
    The ABC for Harlequin options that are used as both command-line options and
    GUI options. Options have names and descriptions, and may have user-facing
    labels and aliased, short declarations (for CLI options).

    Subclasses define options for specific data types, like text or boolean options.
    """

    def __init__(
        self,
        name: str,
        description: str,
        *args: Any,
        label: str | None = None,
        short_decls: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Args:
            name (str): A unique name for this option. Must be a valid
                HTML/CSS id and a valid CLI option name (without the `--` prefix).
                e.g., "port", "header"
            description (str): Help text for this option.
            label (str | None): For GUI options, a human-friendly label for this option.
            short_decls (Sequence[str] | None): For CLI options, a list of short aliases
                (including the `-` prefix) for this option (e.g., ["-p"]).
        """
        # names should be valid html/css ids
        if re.match(r"[A-Za-z](\w|-)*", name):
            self.name = name
        else:
            raise ValueError(
                "An Option's name attribute must match "
                r"""r'[A-Za-z](\w|-)*' """
                "so it is a valid CLI flag and HTML/CSS id."
            )
        self.description = description
        self.label = label or name.replace("_", " ").replace("_", " ").capitalize()
        short_decls = short_decls or []
        self.short_decls = [
            decl if decl.startswith("-") else f"-{decl}" for decl in short_decls
        ]

    @abstractmethod
    def merge(self, other: AbstractOption) -> AbstractOption:
        """
        Merges two options together; used for options with the same name, to return
        a concatenated description and other merged properties.
        """
        pass

    @abstractmethod
    def to_click(self) -> Callable[[click.Command], click.Command]:
        pass

    @abstractmethod
    def to_widgets(self) -> Generator[Widget, None, None]:
        pass

    @abstractmethod
    def to_questionary(self, existing_value: Any | None = None) -> questionary.Question:
        pass


class TextOption(AbstractOption):
    """
    An option for free text input, including optional validation.
    """

    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: list[str] | None = None,
        default: str | None = None,
        placeholder: str | None = None,
        validator: Callable[[str], tuple[bool, str | None]] | None = None,
    ) -> None:
        """
        Args:
            name (str): A unique name for this option. Must be a valid
                HTML/CSS id and a valid CLI option name (without the `--` prefix).
                e.g., "port", "header"
            description (str): Help text for this option.
            label (str | None): For GUI options, a human-friendly label for this option.
            short_decls (Sequence[str] | None): For CLI options, a list of short aliases
                (including the `-` prefix) for this option (e.g., ["-p"]).
            default (str | None): The default value for this option.
            placeholder (str | None): For GUI options, placeholder text for this option.
            validator (Callable[[str], tuple[bool, str | None]] | None): A callable that
                receives the raw input as a string returns a tuple. The first item of
                the tuple is either True for valid input or False for invalid input.
                The second item is a message shown to the user if the validation fails.
        """
        super().__init__(name, description, label=label, short_decls=short_decls)
        self.validator = validator
        self.default = default
        self.placeholder = placeholder

    def merge(self, other: AbstractOption) -> AbstractOption:
        if isinstance(other, ListOption):
            return other.merge(self)

        name = self.name
        description = concatenate(self.description, other.description)
        label = self.label or other.label
        short_decls = set(self.short_decls) | set(other.short_decls)
        default = (
            self.default if self.default == getattr(other, "default", None) else None
        )
        placeholder = self.placeholder or getattr(other, "placeholder", None)

        def merge_validator(raw: str) -> tuple[bool, str | None]:
            """
            The merged validator must return true if either validator
            accepts the input; if the other Option does not have
            a validator, it accepts all inputs, so the merged validator
            must also.
            """
            if (
                self.validator is not None
                and isinstance(other, TextOption)
                and other.validator is not None
            ):
                result = self.validator(raw)
                if result[0]:
                    return result
                else:
                    return other.validator(raw)
            else:
                return True, None

        return TextOption(
            name=name,
            description=description,
            label=label,
            short_decls=list(short_decls),
            default=default,
            placeholder=placeholder,
            validator=merge_validator if self.validator is not None else None,
        )

    def to_click(self) -> Callable[[click.Command], click.Command]:
        def click_callback(
            ctx: click.Context, param: click.ParamType, value: str
        ) -> str:
            if self.validator is not None:
                try:
                    is_valid, message = self.validator(value)
                except Exception as e:
                    raise click.BadParameter(str(e)) from e
                if not is_valid:
                    raise click.BadParameter(message or "Validation failed.")
            return value

        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            callback=click_callback,
        )

    def to_widgets(self) -> Generator[Widget, None, None]:
        yield NoFocusLabel(f"{self.label}:", classes="input_label")
        yield Input(
            value=self.default or "",
            placeholder=self.placeholder or "",
            id=self.name,
            validators=[_CustomValidator(self.validator)],
        )

    def to_questionary(self, existing_value: Any | None = None) -> questionary.Question:
        def _q_validator(raw: str) -> bool | str | None:
            if self.validator is not None:
                result = self.validator(raw)
                if result[0]:
                    return True
                else:
                    return result[1]
            else:
                return True

        try:
            safe_existing_value = str(existing_value)
        except (ValueError, TypeError):
            safe_existing_value = None

        return questionary.text(
            message=self.name,
            default=(
                safe_existing_value
                if safe_existing_value is not None
                else self.default or ""
            ),
            validate=_q_validator,
            style=HARLEQUIN_QUESTIONARY_STYLE,
        )


class ListOption(AbstractOption):
    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: list[str] | None = None,
    ) -> None:
        """
        Args:
            name (str): A unique name for this option. Must be a valid
                HTML/CSS id and a valid CLI option name (without the `--` prefix).
                e.g., "port", "header"
            description (str): Help text for this option.
            label (str | None): For GUI options, a human-friendly label for this option.
            short_decls (Sequence[str] | None): For CLI options, a list of short aliases
                (including the `-` prefix) for this option (e.g., ["-p"]).
        """
        super().__init__(name, description, label=label, short_decls=short_decls)

    def merge(self, other: AbstractOption) -> ListOption:
        name = self.name
        description = concatenate(self.description, other.description)
        label = self.label or other.label
        short_decls = set(self.short_decls) | set(other.short_decls)
        return ListOption(
            name=name,
            description=description,
            label=label,
            short_decls=list(short_decls),
        )

    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            multiple=True,
        )

    def to_widgets(self) -> Generator[Widget, None, None]:
        raise NotImplementedError("No widget for ListOption.")

    def to_questionary(self, existing_value: Any | None = None) -> questionary.Question:
        if isinstance(existing_value, str):
            safe_existing_value = existing_value
        elif isinstance(existing_value, Iterable):
            safe_existing_value = " ".join(existing_value)
        else:
            safe_existing_value = None

        return questionary.text(
            message=self.name,
            instruction="Separate items by a space.",
            default=safe_existing_value if safe_existing_value is not None else "",
            style=HARLEQUIN_QUESTIONARY_STYLE,
        )


class PathOption(AbstractOption):
    """
    A text input with path validation and autocomplete features.
    """

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
        default: str | None = None,
        placeholder: str | None = None,
    ) -> None:
        """
        Args:
            name (str): A unique name for this option. Must be a valid
                HTML/CSS id and a valid CLI option name (without the `--` prefix).
                e.g., "port", "header"
            description (str): Help text for this option.
            label (str | None): For GUI options, a human-friendly label for this option.
            short_decls (Sequence[str] | None): For CLI options, a list of short aliases
                (including the `-` prefix) for this option (e.g., ["-p"]).
            exists (bool): *Validation* Set True if the path must already exist.
            file_okay (bool): *Validation* Set True if the path may be a file.
            dir_okay (bool):  *Validation* Set True if the path may be a directory.
            resolve_path (bool): For CLI Options, set True for the returned path to be
                resolved.
            path_type (type): For CLI Options, define a type for the returned path
                (usually str or pathlib.Path).
            default (str): The default path.
            placeholder (str): For GUI options, the placeholder text for the input.
        """
        super().__init__(name, description, label=label, short_decls=short_decls)
        self.exists = exists
        self.file_okay = file_okay
        self.dir_okay = dir_okay
        self.resolve_path = resolve_path
        self.path_type = path_type
        self.default = default
        self.placeholder = placeholder

    def merge(self, other: AbstractOption) -> AbstractOption:
        if isinstance(other, (TextOption, ListOption)):
            return other.merge(self)
        name = self.name
        description = concatenate(self.description, other.description)
        label = self.label or other.label
        short_decls = set(self.short_decls) | set(other.short_decls)
        default = (
            self.default if self.default == getattr(other, "default", None) else None
        )
        placeholder = self.placeholder or getattr(other, "placeholder", None)
        if isinstance(other, PathOption):
            exists = self.exists and other.exists
            file_okay = self.file_okay or other.file_okay
            dir_okay = self.dir_okay or other.dir_okay
            resolve_path = self.resolve_path or other.resolve_path
            path_type = self.path_type if self.path_type == other.path_type else str
        else:
            exists = False
            file_okay = True
            dir_okay = True
            resolve_path = False
            path_type = str
        return PathOption(
            name=name,
            description=description,
            label=label,
            short_decls=list(short_decls),
            exists=exists,
            file_okay=file_okay,
            dir_okay=dir_okay,
            resolve_path=resolve_path,
            path_type=path_type,
            default=default,
            placeholder=placeholder,
        )

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

    def to_widgets(self) -> Generator[Widget, None, None]:
        yield NoFocusLabel(f"{self.label}:", classes="input_label")
        yield PathInput(
            value=self.default or "",
            placeholder=self.placeholder or "",
            id=self.name,
            file_okay=self.file_okay,
            dir_okay=self.dir_okay,
            must_exist=self.exists,
            tab_advances_focus=True,
        )

    def to_questionary(self, existing_value: Any | None = None) -> questionary.Question:
        def _path_validator(raw_path: str) -> bool | str:
            try:
                p = Path(raw_path)
            except ValueError as e:
                return f"Not a valid path! {e}"
            if self.exists and not p.exists():
                return f"No file exists at {p}"

            if not self.file_okay and p.is_file():
                return f"{p} is a file, expected a directory."

            if not self.dir_okay and p.is_dir():
                return f"{p} is a directory, expected a file."

            return True

        try:
            safe_existing_value = str(existing_value)
        except (ValueError, TypeError):
            safe_existing_value = None

        return questionary.path(
            message=self.name,
            default=(
                safe_existing_value
                if safe_existing_value is not None
                else self.default or ""
            ),
            only_directories=not self.file_okay,
            validate=_path_validator,
            style=HARLEQUIN_QUESTIONARY_STYLE,
        )


class SelectOption(AbstractOption):
    def __init__(
        self,
        name: str,
        description: str,
        choices: Sequence[str | tuple[str, str]],
        label: str | None = None,
        short_decls: list[str] | None = None,
        default: str | None = None,
    ) -> None:
        super().__init__(name, description, label=label, short_decls=short_decls)
        self.choices = choices
        self.default = default
        """
        Args:
            name (str): A unique name for this option. Must be a valid
                HTML/CSS id and a valid CLI option name (without the `--` prefix).
                e.g., "port", "header"
            description (str): Help text for this option.
            choices (Sequence[str | tuple[str, str]]): A list of values or list of
                (label, value) pairs for the user to select from.
            label (str | None): For GUI options, a human-friendly label for this option.
            short_decls (Sequence[str] | None): For CLI options, a list of short aliases
                (including the `-` prefix) for this option (e.g., ["-p"]).
            default (str | None): The default value for this option.
        """

    def merge(self, other: AbstractOption) -> AbstractOption:
        if isinstance(other, (TextOption, PathOption, ListOption)):
            return other.merge(self)
        name = self.name
        description = concatenate(self.description, other.description)
        label = self.label or other.label
        short_decls = set(self.short_decls) | set(other.short_decls)
        choices = set(self.choices) | set(getattr(other, "choices", []))
        default = (
            self.default if self.default == getattr(other, "default", None) else None
        )
        return SelectOption(
            name=name,
            description=description,
            choices=list(choices),
            label=label,
            short_decls=list(short_decls),
            default=default,
        )

    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            type=click.Choice(choices=self._flat_choices(), case_sensitive=False),
        )

    def to_widgets(self) -> Generator[Widget, None, None]:
        choices: list[tuple[str, str]] = []
        for choice in self.choices:
            if isinstance(choice, str):
                choices.append((choice, choice))
            else:
                choices.append(choice)
        yield NoFocusLabel(f"{self.label}:", classes="select_label")
        yield Select(
            options=choices,
            id=self.name,
            value=self.default,
            allow_blank=False,
        )

    def to_questionary(self, existing_value: Any | None = None) -> questionary.Question:
        try:
            safe_existing_value = str(existing_value)
        except (ValueError, TypeError):
            safe_existing_value = None

        if safe_existing_value not in self._flat_choices():
            safe_existing_value = None

        return questionary.select(
            message=self.name,
            choices=self._flat_choices(),
            default=(
                safe_existing_value if safe_existing_value is not None else self.default
            ),
            style=HARLEQUIN_QUESTIONARY_STYLE,
        )

    def _flat_choices(self) -> list[str]:
        choices: list[str] = []
        for choice in self.choices:
            if isinstance(choice, str):
                choices.append(choice)
            else:
                choices.append(choice[0])
        return choices


class FlagOption(AbstractOption):
    """
    A boolean option, defaults to False. (Can set another default, but that only applies
    for GUI options, not CLI options, which always default to False)
    """

    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: Sequence[str] | None = None,
        default: bool = False,
    ) -> None:
        super().__init__(name, description, label=label, short_decls=short_decls)
        self.default = default

    def merge(self, other: AbstractOption) -> AbstractOption:
        if not isinstance(other, FlagOption):
            return other.merge(self)
        name = self.name
        description = concatenate(self.description, other.description)
        label = self.label or other.label
        short_decls = set(self.short_decls) | set(other.short_decls)
        default = self.default and other.default
        return FlagOption(
            name=name,
            description=description,
            label=label,
            short_decls=list(short_decls),
            default=default,
        )

    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}", *self.short_decls, help=self.description, is_flag=True
        )

    def to_widgets(self) -> Generator[Widget, None, None]:
        yield NoFocusLabel(f"{self.label}:", classes="switch_label")
        yield Switch(value=self.default, id=self.name)

    def to_questionary(self, existing_value: Any | None = None) -> questionary.Question:
        try:
            safe_existing_value = bool(existing_value)
        except (ValueError, TypeError):
            safe_existing_value = None

        return questionary.confirm(
            message=self.name,
            default=safe_existing_value if safe_existing_value is not None else False,
            style=HARLEQUIN_QUESTIONARY_STYLE,
        )


HarlequinAdapterOption = AbstractOption


class HarlequinCopyFormat:
    """
    A file format for data export that is supported by Harlequin.
    """

    name: str
    label: str
    extensions: Sequence[str]
    options: Sequence[HarlequinAdapterOption]

    def __init__(
        self,
        name: str,
        label: str | None = None,
        extensions: Sequence[str] | None = None,
        options: Sequence[HarlequinAdapterOption] | None = None,
    ) -> None:
        """
        Args:
            name (str): A unique, internal name for this format. E.g., 'csv'
            label (str | None): A user-facing name for this format. E.g., 'CSV'
            extensions (Sequence[str] | None): A seq of file extensions to associate
                with this format. Should include the leading period. e.g.,
                (".csv", ".tsv").
            options (Sequence[HarlequinAdapterOption] | None): A list of options for
                configuring copy operations for this format. e.g.,
                [FlagOption(name="Header", description="Include header row?")]
        """
        self.name = name
        self.label = label or name.replace("_", " ").replace("_", " ").capitalize()
        self.extensions = extensions or tuple()
        self.extensions = [
            ext if ext.startswith(".") else f".{ext}" for ext in self.extensions
        ]
        self.options = options or tuple()
