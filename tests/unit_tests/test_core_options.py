"""The declarations both commands are built from.

What the option groups and the three key lists used to be -- name lists beside
the `@click.option` they described -- is now a property of the declaration, so
what is worth pinning is that the commands carry exactly what is declared, and
that a declaration cannot leave out the part that decides where an option may
be used.
"""

from __future__ import annotations

import click
import pytest

from harlequin.cli import build_cli as build_harlequin
from harlequin.core_options import (
    BY_NAME,
    CORE_OPTIONS,
    HARLEQUIN,
    HSQL,
    CoreOption,
    Group,
    On,
    attach_core_options,
    first_pass_options,
)
from harlequin.hsql.cli import bare_command


def test_an_option_hsql_takes_cannot_be_declared_without_a_group() -> None:
    """The whole point: the group travels with the option, so an option that
    hsql refuses against is impossible to write without saying how."""
    with pytest.raises(ValueError, match="group"):
        CoreOption(name="nowhere", decls=("--nowhere",), hsql=On())


def test_an_option_the_ide_alone_takes_declares_no_group() -> None:
    """The groups say when hsql reads a value, so an option hsql never reads
    has no answer -- and a group on one would put it in hsql's partition."""
    with pytest.raises(ValueError, match="group"):
        CoreOption(
            name="nowhere",
            decls=("--nowhere",),
            group=Group.PER_REQUEST,
            harlequin=On(),
        )


def test_an_option_neither_command_takes_is_not_an_option() -> None:
    with pytest.raises(ValueError, match="neither command"):
        CoreOption(name="nowhere", decls=("--nowhere",), group=Group.PER_REQUEST)


def test_hsql_carries_every_option_it_declares_and_nothing_else() -> None:
    """No adapter's options are on this command, so its parameters are the
    declarations -- in the order they are declared, which is the order
    `--help` lists them."""
    declared = [option.name for option in CORE_OPTIONS if option.hsql is not None]
    assert [param.name for param in bare_command().params] == declared


def test_the_ide_carries_every_option_it_declares() -> None:
    """`--help` renders the IDE's options from the groups in `harlequin.cli`,
    so their order here is free -- but the set is not."""
    declared = {option.name for option in CORE_OPTIONS if option.harlequin is not None}
    cmd = build_harlequin(["--version"])
    assert declared <= {param.name for param in cmd.params}


@pytest.mark.parametrize("command", [HARLEQUIN, HSQL])
def test_an_option_a_command_did_not_answer_says_what_it_needed(command: str) -> None:
    """A declaration names what only the command can fill in, so the error for
    a command that filled in nothing names the key rather than coming out of
    click as a KeyError on a keyword."""
    with pytest.raises(KeyError, match="version_option"):
        attach_core_options(click.Command("probe"), command, supplied={})


def test_the_first_pass_spells_an_option_the_way_the_command_does() -> None:
    """The pass parses argv before the command exists, so the two could read
    the same flag under different names -- and the profile a mode was not
    supposed to read would be read anyway."""
    declared = {param.name: param for param in bare_command().params}
    probed = first_pass_options(HSQL)
    assert probed
    for param in probed:
        assert param.opts == declared[param.name].opts
        assert param.envvar == declared[param.name].envvar


def test_the_ide_probes_for_no_mode_of_hsqls() -> None:
    """The modes are hsql's, and the IDE's pass has nothing to decide with
    them."""
    assert {param.name for param in first_pass_options(HARLEQUIN)} == {
        "profile",
        "config_path",
        "adapter",
    }


def test_every_name_is_declared_once() -> None:
    assert len(BY_NAME) == len(CORE_OPTIONS)
