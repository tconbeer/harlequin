from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Generator,
    Iterable,
    Sequence,
)

import click

if TYPE_CHECKING:
    import questionary
    from textual.widget import Widget

# Declaring an option must stay cheap: every adapter imports this module, and
# `questionary` (130ms) and Textual (150ms, plus 264ms for the themes that
# `harlequin.colors` pulls in) are only needed to *render* one. `to_widgets()`
# and `to_questionary()` import what they need when they are called.


def concatenate(first: str, second: str) -> str:
    if first == second:
        return first
    return f"{first}\n----or----\n{second}"


def _derived_type_name(cls: type) -> str:
    """A type name for an option class that never declared one.

    `MyCoolOption` becomes `mycool`. Only reached by a subclass that predates
    `option_type`, which is the case `to_dict()` exists to keep working.
    """
    name = cls.__name__
    stem = name[: -len("Option")] if name.endswith("Option") else name
    return (stem or name).lower()


class AbstractOption(ABC):
    """
    The ABC for Harlequin options that are used as both command-line options and
    GUI options. Options have names and descriptions, and may have user-facing
    labels and aliased, short declarations (for CLI options).

    Subclasses define options for specific data types, like text or boolean options.
    """

    option_type: ClassVar[str] = ""
    """The name `to_dict()` reports for this kind of option. e.g., "text".

    A class attribute, so that a subclass of one of the types below inherits
    the right answer and a new type only has to set it. A subclass that never
    sets it is reported under its own class name, which is true rather than
    useful -- and better than claiming a type it is not.
    """

    secret: bool = False
    """Whether this option's value must never be printed. e.g., a password.

    Core cannot enumerate what is sensitive -- `--service-account-key`,
    `--token`, `--tls-key`, whatever the next adapter invents -- so each
    adapter declares its own once and every consumer gets it free:
    `to_dict()` reports it, which is what teaches an agent not to construct
    `hsql --password hunter2`; `to_questionary()` stops echoing the input; and
    `harlequin.redact` masks the value wherever a profile is reported.

    A class attribute *and* a keyword, so a third-party subclass that has never
    passed it answers False rather than raising.
    """

    def __init__(
        self,
        name: str,
        description: str,
        *args: Any,
        label: str | None = None,
        short_decls: Sequence[str] | None = None,
        secret: bool = False,
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
            secret (bool): Set True if this option's value must never be printed
                back -- a password, a token, a key. See the class attribute.
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
        self.secret = bool(secret)

    def to_dict(self) -> dict[str, Any]:
        """
        This option as plain data, for the consumers that read an option rather
        than render one: `hsql --spec`, the config schema, the debug screen.

        Concrete rather than abstract, because third-party adapters subclass
        this: a subclass that predates the method still has to answer, which is
        what the `getattr` is for. The keys are the same whatever the type --
        one that does not apply is null rather than missing.
        """
        return {
            "name": self.name,
            "type": self.option_type or _derived_type_name(type(self)),
            "label": self.label,
            "description": self.description,
            "short_decls": list(self.short_decls),
            "default": getattr(self, "default", None),
            "choices": None,
            "multiple": False,
            # `getattr` for the same reason the rest of this method exists: a
            # subclass that predates the attribute inherits it from the class,
            # unless it also predates this class -- and a value that reports no
            # answer is one a consumer would have to guess about
            "secret": bool(getattr(self, "secret", False)),
        }

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

    option_type = "text"

    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: list[str] | None = None,
        default: str | None = None,
        placeholder: str | None = None,
        validator: Callable[[str], tuple[bool, str | None]] | None = None,
        secret: bool = False,
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
            secret (bool): Set True if this option's value must never be printed
                back -- a password, a token, a key.
        """
        super().__init__(
            name, description, label=label, short_decls=short_decls, secret=secret
        )
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
            # either half saying so is enough: an option two adapters spell the
            # same way, one of them a password, is a password
            secret=self.secret or getattr(other, "secret", False),
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
        from harlequin.copy_widgets import CustomValidator, Input, NoFocusLabel

        yield NoFocusLabel(f"{self.label}:", classes="input_label")
        yield Input(
            value=self.default or "",
            placeholder=self.placeholder or "",
            id=self.name,
            validators=[CustomValidator(self.validator)],
        )

    def to_questionary(self, existing_value: Any | None = None) -> questionary.Question:
        import questionary

        from harlequin.colors import HARLEQUIN_QUESTIONARY_STYLE

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

        # a prompt that echoes a password writes it into a terminal, a
        # screen share and a scrollback buffer at once. `password` is
        # `text` that does not echo, so the wizard is the same wizard
        ask = questionary.password if self.secret else questionary.text
        return ask(
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
    option_type = "list"

    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: list[str] | None = None,
        secret: bool = False,
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
            secret (bool): Set True if this option's value must never be printed
                back -- a password, a token, a key.
        """
        super().__init__(
            name, description, label=label, short_decls=short_decls, secret=secret
        )

    def to_dict(self) -> dict[str, Any]:
        """Repeatable, which is the one thing that separates it from a text
        option -- `--extension httpfs --extension spatial`, and a list in a
        profile."""
        return {**super().to_dict(), "multiple": True}

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
            secret=self.secret or getattr(other, "secret", False),
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
        import questionary

        from harlequin.colors import HARLEQUIN_QUESTIONARY_STYLE

        if isinstance(existing_value, str):
            safe_existing_value = existing_value
        elif isinstance(existing_value, Iterable):
            safe_existing_value = " ".join(existing_value)
        else:
            safe_existing_value = None

        ask = questionary.password if self.secret else questionary.text
        return ask(
            message=self.name,
            instruction="Separate items by a space.",
            default=safe_existing_value if safe_existing_value is not None else "",
            style=HARLEQUIN_QUESTIONARY_STYLE,
        )


class PathOption(AbstractOption):
    """
    A text input with path validation and autocomplete features.
    """

    option_type = "path"

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
        secret: bool = False,
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
            secret (bool): Set True if this option's value must never be printed
                back -- a password, a token, a key.
        """
        super().__init__(
            name, description, label=label, short_decls=short_decls, secret=secret
        )
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
            secret=self.secret or getattr(other, "secret", False),
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
        from harlequin.copy_widgets import NoFocusLabel, PathInput

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
        import questionary

        from harlequin.colors import HARLEQUIN_QUESTIONARY_STYLE

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

        if self.secret:
            # completion is worth losing here: a path an adapter declared
            # secret is one whose characters must not reach the terminal,
            # and `path` has no way to take input it does not echo
            return questionary.password(
                message=self.name,
                default=(
                    safe_existing_value
                    if safe_existing_value is not None
                    else self.default or ""
                ),
                validate=_path_validator,
                style=HARLEQUIN_QUESTIONARY_STYLE,
            )

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
    option_type = "select"

    def __init__(
        self,
        name: str,
        description: str,
        choices: Sequence[str | tuple[str, str]],
        label: str | None = None,
        short_decls: list[str] | None = None,
        default: str | None = None,
        secret: bool = False,
    ) -> None:
        super().__init__(
            name, description, label=label, short_decls=short_decls, secret=secret
        )
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
            secret (bool): Set True if this option's value must never be printed
                back -- a password, a token, a key.
        """

    def to_dict(self) -> dict[str, Any]:
        """The values, flattened.

        A choice may be declared as a `(value, label)` pair for a GUI to render,
        and a pair is not something a caller can type. `_flat_choices()` is what
        `to_click()` passes to `click.Choice`, so this reports what the command
        line will actually accept.
        """
        return {**super().to_dict(), "choices": self._flat_choices()}

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
            secret=self.secret or getattr(other, "secret", False),
        )

    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}",
            *self.short_decls,
            help=self.description,
            type=click.Choice(choices=self._flat_choices(), case_sensitive=False),
        )

    def to_widgets(self) -> Generator[Widget, None, None]:
        from harlequin.copy_widgets import NoFocusLabel, Select

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
        import questionary

        from harlequin.colors import HARLEQUIN_QUESTIONARY_STYLE

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

    option_type = "flag"

    def __init__(
        self,
        name: str,
        description: str,
        label: str | None = None,
        short_decls: Sequence[str] | None = None,
        default: bool = False,
        secret: bool = False,
    ) -> None:
        super().__init__(
            name, description, label=label, short_decls=short_decls, secret=secret
        )
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
            secret=self.secret or getattr(other, "secret", False),
        )

    def to_click(self) -> Callable[[click.Command], click.Command]:
        return click.option(
            f"--{self.name}", *self.short_decls, help=self.description, is_flag=True
        )

    def to_widgets(self) -> Generator[Widget, None, None]:
        from harlequin.copy_widgets import NoFocusLabel, Switch

        yield NoFocusLabel(f"{self.label}:", classes="switch_label")
        yield Switch(value=self.default, id=self.name)

    def to_questionary(self, existing_value: Any | None = None) -> questionary.Question:
        import questionary

        from harlequin.colors import HARLEQUIN_QUESTIONARY_STYLE

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
