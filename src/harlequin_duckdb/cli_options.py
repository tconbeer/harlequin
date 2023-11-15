from pathlib import Path

from harlequin.options import (
    FlagOption,
    ListOption,
    PathOption,
    TextOption,
)

init = PathOption(
    name="init-path",
    description=(
        "The path to an initialization script. On startup, Harlequin will execute "
        "the commands in the script against the attached database."
    ),
    short_decls=["-i", "-init"],
    exists=False,
    file_okay=True,
    dir_okay=False,
    resolve_path=True,
    path_type=Path,
)

no_init = FlagOption(
    name="no-init",
    description="Start Harlequin without executing the initialization script.",
)

read_only = FlagOption(
    name="read-only",
    short_decls=["-readonly", "-r"],
    description="Open the database file in read-only mode.",
)

unsigned = FlagOption(
    name="allow-unsigned-extensions",
    description="Allow loading unsigned extensions",
    short_decls=["-u", "-unsigned"],
)

extensions = ListOption(
    name="extension",
    description=(
        "Install and load the named DuckDB extension when starting "
        "Harlequin. To install multiple extensions, repeat this option."
    ),
    short_decls=["-e"],
)

force_extensions = FlagOption(
    name="force-install-extensions",
    description="Force install all extensions passed with -e.",
)

custom_extension_repo = TextOption(
    name="custom-extension-repo",
    description=(
        "A value to pass to DuckDB's custom_extension_repository variable. "
        "Will be set before installing any extensions that are passed using -e."
    ),
)

md_token = TextOption(
    name="md_token",
    description=(
        "MotherDuck Token. Pass your MotherDuck service token in this option, or "
        "set the `motherduck_token` environment variable."
    ),
)

md_saas = FlagOption(
    name="md_saas",
    description="Run MotherDuck in SaaS mode (no local privileges).",
)

DUCKDB_OPTIONS = [
    init,
    no_init,
    read_only,
    unsigned,
    extensions,
    force_extensions,
    custom_extension_repo,
    md_token,
    md_saas,
]
