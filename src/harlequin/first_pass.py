"""The first pass over an invocation's arguments, before click parses them.

Both commands parse their arguments twice, and this is the first pass. It reads
`-a`, `-P` and `--config-path` off the raw argv, well enough to name the one
adapter the invocation will use, without `ep.load()`ing every installed adapter
to find out; the second pass builds the real command and attaches that adapter's
connection options to it. An invocation only ever connects with one adapter, so
for execution this is not a compromise -- it is the difference between importing
one adapter and importing every adapter the user has installed, which is ~200ms
on a machine with four of them and grows with the fifth.

This pass names the adapter and says what was asked for; it does not decide what
to do about it, because the two commands answer `--help` in opposite ways. `hsql
--help` imports nothing and renders the adapter-agnostic surface, so its help is
true for every adapter rather than for whichever one is the default; `harlequin
--help` imports everything, so nobody loses the ability to discover what an
installed adapter takes. Each reads `FirstPass` and takes its own exit.

The second pass's other half is here too: `attach_adapter_options()` puts one
adapter's connection options on an already-built command, which is the step that
makes reading only one of them enough.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping, Sequence

import click

from harlequin.config import DEFAULT_ADAPTER, Profile, load_profile
from harlequin.exception import HarlequinConfigError

if TYPE_CHECKING:
    from harlequin.adapter import HarlequinAdapter


@dataclass(frozen=True)
class FirstPass:
    """What a first look at the arguments settled, before click parses them."""

    profile: Profile
    """The profile this invocation runs under, read once and reused."""

    adapter: str | None
    """Whose connection options belong on the command; None to attach none."""

    error: HarlequinConfigError | None = None
    """Held rather than raised, so the callback can report it with an exit code."""

    wants_help: bool = False
    """Whether this invocation is going to render help rather than connect."""


def first_pass(
    argv: Sequence[str],
    installed: Sequence[str],
    *,
    program: str,
    extra_options: Sequence[click.Option] = (),
    connects: Callable[[Mapping[str, Any]], bool] | None = None,
    no_args_is_help: bool = False,
) -> FirstPass:
    """Read the profile and name the adapter, importing none of them.

    `installed` is every installed adapter's name, which the caller has usually
    already read for a `click.Choice`; a name that is not one of them is left
    for that Choice to reject, with the list.

    `extra_options` are spellings this command has that bear on the answer --
    the caller's own flags, so that a value of one is not mistaken for another
    option's. `connects` reads what the probe found and says whether this
    invocation is going to open a database at all: one that is not needs
    neither a profile nor an adapter, and pays for neither.

    A config file it cannot read is held, not raised: at this point there is no
    command and so no exit code, and the profile is wanted whether or not it
    names an adapter, so the caller's callback reports it.
    """
    probe = click.Command(
        program,
        params=[
            click.Option(["-a", "--adapter"]),
            click.Option(["-P", "--profile"]),
            click.Option(
                ["--config-path"],
                type=click.Path(path_type=Path),
                envvar="HARLEQUIN_CONFIG_PATH",
            ),
            *extra_options,
            # click's own --help and --version are eager and would exit; these
            # are the same spellings as plain flags, so the probe can see that
            # one was asked for.
            click.Option(["--help"], is_flag=True),
            click.Option(["--version"], is_flag=True),
        ],
        add_help_option=False,
    )
    # on the Context, not in the command's context_settings: those are applied
    # by make_context(), which this deliberately does not go through.
    ctx = click.Context(
        probe,
        resilient_parsing=True,
        ignore_unknown_options=True,
        allow_extra_args=True,
        allow_interspersed_args=True,
    )
    with contextlib.suppress(click.ClickException, ValueError):
        probe.parse_args(ctx, list(argv))

    wants_help = bool(ctx.params.get("help")) or (no_args_is_help and not argv)

    if connects is not None and not connects(ctx.params):
        return FirstPass(profile={}, adapter=None, wants_help=wants_help)

    profile: Profile = {}
    error: HarlequinConfigError | None = None
    try:
        # the profile, and not the keymaps beside it: keymaps are the IDE's, and
        # wanting them would mean reading every config file rather than stopping
        # at the one that defines this profile.
        profile = load_profile(
            config_path=ctx.params.get("config_path"),
            profile_name=ctx.params.get("profile"),
        )
    except HarlequinConfigError as e:
        error = e
    except OSError as e:
        error = HarlequinConfigError(str(e), title="Harlequin could not read a config.")

    name = ctx.params.get("adapter") or profile.get("adapter")
    # an invocation that names no adapter and is only going to print something
    # takes no adapter at all, so that a command whose answer does not depend on
    # one does not wait for one to import. `--version` prints the same string
    # whatever is installed; what `--help` does with this is the caller's
    # question. Both still name an adapter that was asked for by name, so that
    # `--help -a postgres` can document it and its options parse either way.
    if name is None and (wants_help or ctx.params.get("version")):
        return FirstPass(
            profile=profile, adapter=None, error=error, wants_help=wants_help
        )

    by_name = {n.lower(): n for n in installed}
    if name is None:
        name = DEFAULT_ADAPTER
    # an unknown name is left for click's Choice to reject, with the list
    return FirstPass(
        profile=profile,
        adapter=by_name.get(str(name).lower()),
        error=error,
        wants_help=wants_help,
    )


def command_spellings(cmd: click.Command) -> tuple[set[str], set[str]]:
    """Every option spelling, and every parameter name, a command already has.

    What `attach_adapter_options()` reserves against, for a command whose own
    flags are the part of it that is an API; `hsql --spec` applies the same two
    sets to the document it writes.
    """
    return (
        {opt for param in cmd.params for opt in param.opts},
        {param.name for param in cmd.params if param.name},
    )


def attach_adapter_options(
    cmd: click.Command,
    adapter_cls: type[HarlequinAdapter],
    *,
    reserved: Collection[str] = (),
    taken: Collection[str] = (),
) -> None:
    """Add one adapter's connection options to an already-built command.

    `AbstractOption.to_click()` appends straight to a `Command`'s params, so an
    adapter declares its options once, for both commands, and this only has to
    settle what happens when one of them collides with the command's own.

    Which is the caller's policy, not this function's: pass the two sets from
    `command_spellings()` to have a colliding declaration lose the colliding
    spelling -- visibly, in `--help`, rather than shadowing a documented flag --
    and pass neither to let it shadow, which is what the IDE has always done.
    """
    first = len(cmd.params)

    for option in adapter_cls.ADAPTER_OPTIONS or []:
        option.to_click()(cmd)

    if not reserved and not taken:
        return

    for param in cmd.params[first:]:
        param.opts = [opt for opt in param.opts if opt not in reserved]
        param.secondary_opts = [
            opt for opt in param.secondary_opts if opt not in reserved
        ]
    cmd.params[first:] = [
        param for param in cmd.params[first:] if param.opts and param.name not in taken
    ]
