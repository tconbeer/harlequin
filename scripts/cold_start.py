"""Measure what an import costs before anyone has done any work.

Cold start is the budget the headless CLI has to live inside, and it is spent
almost entirely on imports: the modules a front end touches on its way to a
connection, not the query. This reports the wall time and the module count for
each step, so a regression points at the import that caused it.

Usage:
    uv run python scripts/cold_start.py
    uv run python scripts/cold_start.py --runs 10
    uv run python scripts/cold_start.py --json      # for CI job summaries

The numbers are informational. The blocking guards are the import-linter
contracts in pyproject.toml and tests/unit_tests/test_import_hygiene.py, which
fail identically on every machine; a wall clock on a shared CI runner does not.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

# (label, statement). Ordered cheapest to dearest, which is also roughly the
# order a headless invocation touches them.
STEPS: list[tuple[str, str]] = [
    ("interpreter only", "pass"),
    ("harlequin", "import harlequin"),
    ("harlequin.statements", "import harlequin.statements"),
    ("harlequin.catalog", "import harlequin.catalog"),
    ("harlequin.options", "import harlequin.options"),
    ("harlequin.config", "import harlequin.config"),
    ("harlequin.plugins", "import harlequin.plugins"),
    (
        "fastdatatable backend",
        "from textual_fastdatatable.backend import create_backend",
    ),
    ("harlequin.query", "import harlequin.query"),
    ("harlequin.layout", "import harlequin.layout"),
    ("harlequin.export", "import harlequin.export"),
    ("harlequin.hsql.cli", "import harlequin.hsql.cli"),
    ("harlequin_sqlite", "import harlequin_sqlite"),
    ("harlequin_duckdb", "import harlequin_duckdb"),
    ("harlequin.cli", "import harlequin.cli"),
    ("the TUI", "from harlequin import Harlequin"),
]

PROBE = """
{statement}
import sys
print(len(sys.modules), "textual" in sys.modules, sep=",")
"""


def measure(statement: str, runs: int) -> tuple[float, int, bool]:
    """Returns (best milliseconds, module count, whether Textual was imported).

    The best of N, not the mean: we want the floor the import costs, and a
    shared runner only ever adds noise on top of it.
    """
    best = float("inf")
    modules = 0
    imported_textual = False
    for _ in range(runs):
        start = time.perf_counter()
        proc = subprocess.run(
            [sys.executable, "-c", PROBE.format(statement=statement)],
            capture_output=True,
            text=True,
            check=True,
        )
        best = min(best, (time.perf_counter() - start) * 1000)
        count, textual = proc.stdout.strip().split(",")
        modules = int(count)
        imported_textual = textual == "True"
    return best, modules, imported_textual


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=int, default=5, help="iterations per step (best wins)"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit JSON instead of a table"
    )
    args = parser.parse_args()

    results = []
    for label, statement in STEPS:
        elapsed, modules, textual = measure(statement, args.runs)
        results.append(
            {
                "step": label,
                "statement": statement,
                "ms": round(elapsed, 1),
                "modules": modules,
                "textual": textual,
            }
        )

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    print(f"{'step':<24}{'ms':>8}{'modules':>10}  textual")
    print("-" * 52)
    for row in results:
        flag = "yes" if row["textual"] else "no"
        print(f"{row['step']:<24}{row['ms']:>8}{row['modules']:>10}  {flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
