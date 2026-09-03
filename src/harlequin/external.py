"""Running someone else's program from inside the app.

An external editor takes the terminal over: Harlequin drops out of application
mode, the child owns stdin and stdout for as long as the human takes, and what
comes back is a file. This module owns that process -- resolving the editor,
the temp file that carries the buffer, and the suspend -- and the app owns the
widgets the result is applied to, so nothing here imports Textual outside
`TYPE_CHECKING`.

The editor comes from `$VISUAL` or `$EDITOR` and from nowhere else. There is no
fallback to `vi` or `notepad`: launching an editor the user never named is the
one outcome nobody asked for. The environment needs no confirmation, either --
it is the user's own shell session, which a config file is not.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from harlequin.exception import HarlequinExternalError

if TYPE_CHECKING:
    from textual.app import App

EDITOR_ENV_VARS = ("VISUAL", "EDITOR")
"""The spellings Harlequin reads, in the order it reads them."""

ERROR_TITLE = "Harlequin could not run your editor."

EDIT_SUFFIX = ".sql"
"""What the temp file is named, so the editor picks the right syntax."""


@dataclass
class ExternalEdit:
    """What an editor did: how it exited, and the text it left behind."""

    returncode: int
    text: str | None
    """The edited buffer, or None if the editor exited non-zero.

    A non-zero exit discards the edit -- the `git commit` and `:cq` convention,
    and the only escape hatch a user has once the editor is open.
    """


def split_command(command: str) -> list[str]:
    """Splits an editor command into argv, under the platform's own quoting.

    `$EDITOR` carries flags (`code -w`) and, on Windows, quoted paths; POSIX
    rules would eat the backslashes in one.
    """
    if sys.platform == "win32":
        return [token.strip('"') for token in shlex.split(command, posix=False)]
    return shlex.split(command)


def resolve_editor() -> list[str]:
    """The user's editor as argv, from `$VISUAL` then `$EDITOR`.

    Raises HarlequinExternalError naming both spellings if neither is set.
    """
    for variable in EDITOR_ENV_VARS:
        command = os.environ.get(variable, "").strip()
        if not command:
            continue
        argv = split_command(command)
        if argv:
            return argv
    raise HarlequinExternalError(
        title=ERROR_TITLE,
        msg=(
            "Harlequin runs the editor named by the $VISUAL or $EDITOR "
            "environment variable, and neither one is set.\n\n"
            "Set one in your shell, e.g. export EDITOR=vim"
        ),
    )


def run_in_terminal(app: "App", argv: Sequence[str]) -> int:
    """Hands the terminal to argv until it exits, and returns its exit status.

    Suspending is main-thread only -- it redirects the process's streams -- and
    the headless driver cannot do it at all, so this is also the one seam a test
    replaces.
    """
    # the only Textual name this module needs at run time, and only to turn a
    # terminal that cannot be suspended into an error a caller can show.
    from textual.app import SuspendNotSupported

    try:
        with app.suspend():
            return subprocess.call(list(argv))
    except SuspendNotSupported as e:
        raise HarlequinExternalError(
            title=ERROR_TITLE,
            msg=(
                "Harlequin cannot suspend itself in this environment, so it "
                "cannot hand the terminal to another program."
            ),
        ) from e
    except OSError as e:
        raise HarlequinExternalError(
            title=ERROR_TITLE,
            msg=f"Harlequin could not start {argv[0]!r}:\n{e}",
        ) from e


def launch_external_editor(app: "App", text: str) -> ExternalEdit:
    """Round-trips text through the user's editor, using a temp file.

    The exchange is a file because editors do not read stdin; it is handed over
    as the editor's last argument, the shape `git rebase -i` uses.
    """
    argv = resolve_editor()
    try:
        # closed before the editor opens it: on Windows a child cannot open a
        # file this process still holds, and delete=True would take it with us.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=EDIT_SUFFIX,
            encoding="utf-8",
            newline="",
            delete=False,
        ) as tmp:
            tmp.write(text)
            path = Path(tmp.name)
    except OSError as e:
        raise HarlequinExternalError(
            title=ERROR_TITLE,
            msg=f"Harlequin could not write a temporary file for your editor:\n{e}",
        ) from e

    try:
        returncode = run_in_terminal(app, [*argv, str(path)])
        if returncode != 0:
            return ExternalEdit(returncode=returncode, text=None)
        # read back with universal newlines, so an editor that writes CRLF
        # does not put carriage returns in the buffer.
        edited_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise HarlequinExternalError(
            title=ERROR_TITLE,
            msg=f"Harlequin could not read back the file your editor wrote:\n{e}",
        ) from e
    finally:
        with suppress(OSError):
            path.unlink()

    return ExternalEdit(returncode=returncode, text=edited_text)
