---
name: pr-review
description: Use before merging any non-trivial PR in this repo, or when asked to review a diff, a branch, or changes to Harlequin or hsql. A rigorous, scope-explicit review that never lets "an agent said it was clean" be the merge gate on its own.
---

# PR review (the scoped, disclaimer-first review)

Adapted from `pr-bulldog-review`. The core insight: a review is
**scoped by its prompt**. "Looks clean" means "clean within a scope I
didn't state" — and the bug hides in what wasn't looked at.

## Iron rules

1. **A clean review is never the merge gate by itself.** It's necessary, not
   sufficient. Green review + green CI still needs the operator's "is the actual
   intent preserved?" judgment. **The user makes the final merge call — surface
   the verdict and wait for an explicit "merge it."**
2. **Review with an explicit scope statement.** "Review this PR" is too broad;
   you'll pick your own lenses and miss the one that matters. Name the lens.
3. **End every review with a "Not checked" section.** It's more important than
   the findings — the findings say what you did; the disclaimer says where the
   next bug is.
4. **Apply your own read first.** The review is a second pair of eyes, not the
   first line of defense.
5. **Dispatch multiple narrow passes** rather than one broad one when the diff is
   large; each pass's prompt excludes the prior lenses.
6. **Your local run is one cell of the matrix.** CI runs 3.10–3.14 across Linux,
   macOS and Windows, and half of what breaks here breaks on a Python or an OS
   you didn't run. Say which cell you ran.

## The "Not checked" disclaimer (mandatory)

```
## Not checked
- I did not examine <area> because it's outside this PR's scope.
- I did not run <verification> ; relied on <existing thing>.
- I did not verify <invariant> across all consumers (>N callers); spot-checked M.
- I ran the suite on <Python> / <OS> only.
```

## Lenses (pick the ones the diff touches; state which)

1. **Correctness / logic** — the change does what it claims; edge cases. Adapters
   are third-party code: a raw driver exception (not a `HarlequinQueryError`)
   must surface as an error modal, never crash the app.

2. **Reuse & simplification** — is there an existing helper; is this the simplest
   shape.

3. **Upstream ownership.** Mandatory on any diff that works around a limitation
   in `textual-fastdatatable`, `textual-textarea`, `pytest-textual-snapshot`, or
   an adapter — we maintain all of them. A workaround here is a permanent tax on
   this repo for a problem whose actual home is one release away, and the next
   reader will take it for intended design. Ask: what would this change look like
   if the dependency did the right thing? If that version is smaller, the finding
   is "make it upstream" — and if the PR can't reach that repo, the finding is
   "write it up as an issue there," not "absorb it here."

4. **Cross-cutting consumers — the one that catches the subtle bugs.** Mandatory
   on any PR touching a producer: the execution core (`query.py`,
   `statements.py`, `export.py`, `layout.py`), a shared type, or a pure decision
   function. Find **every reader** and decide, for each, whether it should see the
   new behavior or the old. In particular:
   - The core has **two front ends**. New query behavior belongs in the core,
     where both reach it — not in an `app.py` worker and not in `hsql/`. A diff
     that changes what a query *does* in one front end and not the other is a
     finding unless it says why they should differ.
   - **The two commands name each other.** The IDE asks `hsql` what it takes
     (`cli.hsql_spellings()`, `cli.hsql_profile_keys()`), so `harlequin --csv`
     can point at `hsql --csv`; `hsql.diagnostics.IDE_THEMES` is a *copy* of the
     names `harlequin -t` accepts, since `hsql` may not reach
     `harlequin.colors`. A theme added without its entry there makes
     `hsql -t nord` lie, and its test is what catches that.
   - `export._deduplicate_column_names()` must stay character-for-character what
     `create_backend()` does; `--format table` and `--format csv` must agree cell
     for cell.

5. **The adapter contract — this repo's highest-stakes surface.** Every change to
   `adapter.py`, `catalog.py`, `driver.py`, or `options.py` is a public API change
   for out-of-tree adapters (`harlequin-postgres`, `harlequin-mysql`, and every
   third-party one) that CI here cannot run. For each symbol touched, decide
   whether an adapter written before this PR still satisfies it:
   - Anything added to `AbstractOption` needs a **concrete, working base
     implementation** — a subclass that predates the addition still has to answer.
   - Adapters must tolerate both subsets and supersets of their declared options,
     and must not rely on option defaults. A diff that starts passing an option
     only some adapters declare is a finding.
   - A new required method, a renamed keyword, or a narrowed return type is a
     breaking change and needs a `### Breaking Changes` changelog entry.

6. **Import hygiene.** Mandatory on any diff that adds an import to
   `harlequin/__init__.py`, an adapter-facing module, the execution core, or
   `hsql/`. The headless CLI has to reach a database without paying for the TUI.
   - **Run both guards** — they check different things. `uv run lint-imports`
     reads the *static* graph and cannot tell a deferred import from a
     module-scope one; `uv run pytest tests/unit_tests/test_import_hygiene.py`
     spawns subprocesses and is what proves a deferral actually defers. A new
     deliberate deferral needs an entry in **both** places: `ignore_imports` in
     `pyproject.toml` with a comment, and a run-time test.
   - No module-scope import in `harlequin/__init__.py` — it resolves public
     names through a PEP 562 `__getattr__`, and one eager import puts the whole
     app behind every consumer.
   - Type-only Textual imports go under `if TYPE_CHECKING:`.
   - `uv run python scripts/cold_start.py` is informational: read it if the diff
     could plausibly move start-up cost.

7. **stdout and stderr.** stdout belongs to query output. Diagnostics, plugin
   load failures and `pretty_print_warning` go to stderr, or they contaminate a
   pipe. In `hsql/`, nothing writes to stderr but `diagnostics.py` and nothing
   writes to stdout but `output.py`; a `print()` or a `click.echo()` anywhere else
   in that package is a finding.

8. **Secrets.** `secret=True` on an option is how an adapter says a value must
   never be printed back, and `harlequin.redact` is the only place that acts on
   it. Any new path that prints a profile, a connection string, a config file, or
   a driver exception — `--info`, `--config show`, `--spec`, the generated schema,
   the debug screen, every stderr message — must route through `redact_profile()`,
   `redact_conn_str()` or `redact_text()`. A new output surface that prints
   user-supplied connection values and doesn't is the finding this lens exists
   for.

9. **App and threading** (any diff in `app.py` or `components/`) — all database
   work happens in `@work(thread=True, ...)` workers that **never mutate
   widgets**; workers `post_message(...)` and `@on(...)` handlers own state
   changes. Anything the editor holds that a user expects to survive a tab switch
   must live in `EditorState`, or it silently belongs to whichever buffer was
   loaded last. A new user-bindable behavior needs an entry in
   `HARLEQUIN_ACTIONS`, or it can't be bound or shown in the help screen.

10. **Limits, config and caches** — the invariants that are easy to get subtly
    wrong:
    - There are **two kinds of limit**: `RowLimit` is hard (`cursor.set_limit()`,
      so fewer rows leave the database, and the true total becomes unknowable);
      `viewer_max_rows` and `LayoutOptions.max_rows` are soft caps over rows
      already fetched. `-1` means unlimited everywhere; `0` means zero rows,
      except the viewer's cap, where it has always meant unlimited.
    - Config merges so the **nearest file wins per profile**, not per top-level
      table, and a CLI value beats the profile **only if the user actually typed
      it** (`merge_profile_with_cli`).
    - Bump `CACHE_VERSION` in `editor_cache.py` / `catalog_cache.py` when the
      pickled shape changes.
    - `schemas/config-v1.json` and `docs/generated/hsql-reference.md` are
      generated build artifacts; if the diff changes their generator, they must be
      regenerated and committed (their tests regenerate and compare).

11. **Test honesty** — a test that asserts a value without exercising it is
    **hollow**. Drive it through `pilot` or the CLI, then assert. Test names lie;
    read the assertions.
    - **A snapshot mismatch is not automatically a failure** — what matters is
      whether the test passes, and several tests deliberately skip their snapshot
      assertion. But an *accepted* diff in
      `tests/unit_tests/__snapshots__/test_golden_formats/` is a change to
      Harlequin's output contract: read it byte for byte and say so in the review.
    - Snapshots are committed from **Python 3.10** and take two runs. A PR whose
      snapshots were regenerated on 3.12 has clobbered the baseline
      (`tests/conftest.py::pytest_configure` refuses the obvious version of this,
      not every version).
    - New async tests need `@pytest.mark.asyncio` and should await
      `wait_for_workers(app)` rather than sleeping.

12. **Changelog and docs** — every PR should consider a `CHANGELOG.md` entry under
    `[Unreleased]`, referencing the issue it closes. Only NOTABLE, user-facing
    changes; one or two sentences; no implementation details. Never hand-edit a
    released section or `version` in `pyproject.toml`. User-facing docs live in
    `tconbeer/harlequin-web` — a PR that adds docs prose *here* is usually a
    finding.

## Verification

`make check` is the full loop (sync, ruff format + fix, pytest on 3.10 and the
py12 slice, mypy, lint-imports) — run it, and say in the review whether you did.
Targeted runs when the diff is narrow:

```bash
uv run pytest -m "not online"                          # the standard run
uv run lint-imports                                    # static contracts
uv run pytest tests/unit_tests/test_import_hygiene.py  # what actually defers
uv run mypy                                            # strict; src/ and tests/
```

On a fresh Linux container, `localedef -i en_US -f UTF-8 en_US.UTF-8` before the
first run — an image with only `C`/`POSIX` errors every test in setup, and that
is not a failure to debug.

## Anti-patterns

- "Review returned clean, so merge." No — necessary, not sufficient.
- "One review covering everything." It goes shallow. Two narrow prompts beat one
  broad one.
- "The tests are named well, so they're fine." Names lie; only assertions pin.
- "`lint-imports` passed, so the import is deferred." It reads the static graph.
  Only the subprocess tests prove a deferral.
- "The snapshots changed, so I regenerated them." Read the diff first; a golden
  format snapshot is the output contract.
- "I worked around it in `harlequin.query`." Check whether the fix belongs in the
  dependency we also maintain.
