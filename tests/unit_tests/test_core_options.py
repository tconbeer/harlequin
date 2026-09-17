"""The declarations both commands are built from: that each carries exactly
what it declares, and that none can leave out where it may be used."""

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
    """The whole point: no option hsql refuses against can omit how."""
    with pytest.raises(ValueError, match="group"):
        CoreOption(name="nowhere", decls=("--nowhere",), hsql=On())


def test_an_option_the_ide_alone_takes_declares_no_group() -> None:
    """A group on one hsql never reads would join hsql's partition."""
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
    """No adapter's options here, so the parameters are the declarations, in
    `--help`'s order."""
    declared = [option.name for option in CORE_OPTIONS if option.hsql is not None]
    assert [param.name for param in bare_command().params] == declared


def test_the_ide_carries_every_option_it_declares() -> None:
    """Its help renders from the groups in `harlequin.cli`: the order is
    free, the set is not."""
    declared = {option.name for option in CORE_OPTIONS if option.harlequin is not None}
    cmd = build_harlequin(["--version"])
    assert declared <= {param.name for param in cmd.params}


@pytest.mark.parametrize("command", [HARLEQUIN, HSQL])
def test_an_option_a_command_did_not_answer_says_what_it_needed(command: str) -> None:
    """The error names the key, not a click keyword."""
    with pytest.raises(KeyError, match="version_option"):
        attach_core_options(click.Command("probe"), command, runtime_values={})


def test_the_first_pass_spells_an_option_the_way_the_command_does() -> None:
    """Spell a flag two ways and a mode reads the profile it should not."""
    declared = {param.name: param for param in bare_command().params}
    probed = first_pass_options(HSQL)
    assert probed
    for param in probed:
        assert param.opts == declared[param.name].opts
        assert param.envvar == declared[param.name].envvar


def test_the_ide_probes_for_no_mode_of_hsqls() -> None:
    """The modes are hsql's; the IDE's pass decides nothing with them."""
    assert {param.name for param in first_pass_options(HARLEQUIN)} == {
        "profile",
        "config_path",
        "adapter",
    }


def test_every_name_is_declared_once() -> None:
    assert len(BY_NAME) == len(CORE_OPTIONS)
