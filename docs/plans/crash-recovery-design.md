# Crash recovery and bug reporting

The design behind [#687](https://github.com/tconbeer/harlequin/issues/687): never dump a raw
traceback at a user, preserve their work when something goes wrong, and make the report they send us
worth reading.

## The problem

An uncaught exception on Harlequin's message pump falls straight through `AppBase._handle_exception`
(`app_base.py:68`) into Textual's `_fatal_error()`, which renders a `rich.traceback.Traceback`
**with `show_locals=True`** and prints it to stderr. That is intimidating, it tells the user nothing
about what to do next, and it puts local variables — connection strings, tokens, query results — on
the terminal. Replacing it is a privacy fix as much as a UX one.

`hsql` has the same hole from the other end. `main()` (`hsql/__init__.py`) catches `ClickException`,
`Abort` and `KeyboardInterrupt`; anything else escapes to Python's own handler, which prints a
traceback and exits 1 — and 1 is `ExitCode.QUERY`, so a bug in Harlequin is indistinguishable from
"the database rejected the SQL." A caller scripting against hsql's exit codes cannot tell the two
apart.

Three problems compound it:

- **The user's work is lost.** `write_editor_cache` is called from exactly one place,
  `Harlequin.action_quit()` (`app.py:970`). A crash, a `kill -9`, a closed terminal or a dropped ssh
  session loses every open buffer — as does the `self.exit(return_code=2)` path.
- **The reports we get are thin.** `bug_report.md` asks for version, adapter, shell, terminal, OS,
  locale and install method as free text, so most reports arrive missing half of it.
- **A crash at startup could repeat forever.** [#745](https://github.com/tconbeer/harlequin/issues/745)
  crashed *inside* `EditorCollection.on_mount` while replaying cached buffers. Saving buffers on
  crash without a guard turns a one-time crash into a crash loop, which is worse than what we have
  now.

Outcome: a crash exits cleanly with a short panel naming a crash report file; the user's buffers
survive and come back on the next launch; a crash *while restoring* those buffers cannot repeat; and
the report drops into an issue template built to receive it.

## The two ideas that carry it

**A per-process recovery file, separate from the shared cache.** Checkpoints go to
`recovery-<pid>.pickle`, never to `cache-1.pickle`. Two Harlequins on one machine keep today's
comprehensible "last clean quit wins" semantics instead of thrashing a shared file every minute, and
a checkpoint can never overwrite what another process wrote when it quit cleanly.

**Rename before replay.** `os.replace` the recovery file to `<name>.replayed` *before* unpickling and
replaying it. If unpickling or replaying crashes, the next start finds nothing to replay. That is the
whole crash-loop guard — one line, no session sentinel, no PID-liveness check, and correct across
concurrent processes by construction.

**Where the crash message goes.** Textual's `panic()` calls `_close_messages_no_wait()`, so the
message pump is gone the moment we handle the exception: an in-app modal is unreachable from
`_handle_exception`, and attempting one re-enters an app that has already proven it is broken. The
message is a rich `Panel` at `_exit_renderables[0]`, printed to stderr after the terminal is
restored — the same mechanism `AppBase.__init__` already uses for a bad theme.

## `editor_cache.py`

```python
CHECKPOINT_INTERVAL_SECONDS = 60.0
RECOVERY_STALE_SECONDS = 3 * CHECKPOINT_INTERVAL_SECONDS

def write_cache(cache: Cache) -> bool          # now atomic, and never raises
def get_recovery_file() -> Path                # cache dir / f"recovery-{os.getpid()}.pickle"
def write_recovery(cache: Cache) -> bool
def clear_recovery() -> None
def adopt_recovery() -> tuple[Cache | None, Path | None]
```

`CACHE_VERSION` stays `1` — the pickled shape does not change.

- **Atomic write.** Both writers go through `_write_pickle(cache, path)`: a temp file in the same
  directory (carrying the pid, so a killed process leaves at most one bounded stray),
  `flush()` + `os.fsync()`, then `os.replace()`. Same-directory means a same-volume rename, atomic on
  POSIX and on Windows. The `fsync` is what stops a power loss leaving a zero-length file.
- **Never raises.** Returns `False` on `OSError` / `pickle.PicklingError`. Both callers run where an
  exception would itself take the app down. On Windows `os.replace` raises `PermissionError` when
  another process holds the destination open, so a checkpoint has to be allowed to skip silently.
- **`adopt_recovery()`** globs `recovered-*.pickle` (written by the crash handler) newest first, then
  `recovery-*.pickle` whose mtime is older than `RECOVERY_STALE_SECONDS` — that age test is how a
  live sibling process's file is left alone, without a PID-liveness check that would be wrong on
  Windows and wrong under PID reuse. It renames the chosen file to `.replayed` **before** unpickling
  it, and returns `(cache, replayed_path)` so the app can say where the buffers came from.
  Everything wrapped → `(None, None)`.

Derive `get_recovery_file()` from the same `user_cache_dir` call as `get_cache_file()`, so
`tests/functional_tests/test_cache.py:43`'s existing monkeypatch covers the new files for free.

## Checkpointing (`app.py`)

- `on_mount`: `self.set_interval(CHECKPOINT_INTERVAL_SECONDS, self._checkpoint_editor_cache)`.
- `_checkpoint_editor_cache()`:
  - bail when `getattr(self, "editor_collection", None)` is None or not mounted — the collection is
    created in `compose()`, not `__init__`;
  - build `Cache(focus_index=..., buffers=...)` on the message pump (the `buffers` property queries
    widgets — `code_editor.py:294` calls `_save_loaded_buffer()` first);
  - **bail when every buffer is blank.** A startup crash gives `buffers == []`, and writing that over
    a good recovery file destroys exactly the work this feature exists to save;
  - bail when `cache == self._last_checkpointed_cache`. `Cache`/`BufferState` are dataclasses and
    `Selection` is a NamedTuple, so `==` is an exact content comparison — no dirty flag to keep in
    sync across tab swaps;
  - `write_recovery(cache)`, synchronously. Pickling a few SQL buffers is microseconds; a thread
    worker would add ordering hazards (two checkpoints racing; `exclusive=True` cancelling one
    mid-flight while its thread still completes the write) for no measured win.
- `action_quit()` keeps its `write_editor_cache(...)` and then calls `clear_recovery()`.

## Restoring (`components/code_editor.py`)

`EditorCollection.__init__` keeps `self.startup_cache = load_cache()` and adds
`self.recovered_cache, self.recovered_from = adopt_recovery()`. **Two separate module-level names**,
not folded into `load_cache()`, so `tests/functional_tests/conftest.py`'s `no_use_buffer_cache`
fixture stays the one seam.

`on_mount` prefers `recovered_cache` over `startup_cache` when present — a recovered session started
from the cache, so it is strictly newer — and then notifies: *"Recovered buffers from a session that
ended unexpectedly."* The shared `cache-1.pickle` is left untouched, so if the recovered content is
itself the problem, the last clean-quit state is still on disk.

## `environment.py` (new, headless)

Facts about the running installation, shared by `hsql --info`, the crash report, and (later) the
Debug Info screen. No textual, no rich; `adapter_facts` defers `harlequin.plugins` into its body as
`info._adapters` already does.

```python
def harlequin_version() -> str
def python_facts() -> dict[str, str]        # version, implementation, executable
def platform_facts() -> dict[str, str]      # system, release, machine
def terminal_facts() -> dict[str, Any]      # term, term_program(+version), colorterm, shell,
                                            # wsl_distro, ssh (bool), locale, size
def install_facts() -> dict[str, Any]       # installer, in_venv
def adapter_facts(only: str | None = None) -> dict[str, dict[str, Any]]
def runtime_report() -> dict[str, Any]
```

- `terminal_facts()` reports `ssh: True/False` and **never the value of `SSH_CONNECTION`**, which
  holds IP addresses. `size` from `shutil.get_terminal_size()`.
- `install_facts()` reads `distribution("harlequin").read_text("INSTALLER")` (pip/uv/pipx write it)
  and `sys.prefix != sys.base_prefix` — that answers the bug template's install-method checkbox
  automatically.
- `adapter_facts` is `info._adapters` + `info._capabilities` moved verbatim, minus the
  `diagnostics.note()` call, which a shared module cannot make.

`hsql/modes/info.py` then calls these instead of building the facts itself, and keeps its stderr note
by walking `adapter_facts()`'s result for entries with an `error`. **Acceptance criterion:
`hsql --info`'s JSON is byte-identical.** `terminal_facts` and `install_facts` are *not* added to
that document — it is an agent-facing contract with a generated reference pinned by
`test_cli_reference.py`, and `TERM` means nothing to a non-interactive program.

Add `harlequin.environment` and `harlequin.crash` to the `source_modules` of the "adapter API is
reachable without the TUI" import-linter contract, and to `HEADLESS_IMPORTS` in
`tests/unit_tests/test_import_hygiene.py`. Both have to stay headless anyway, because `hsql` imports
them.

## `crash.py` (new, headless)

```python
CRASH_REPORT_KEEP = 10
ISSUE_URL = "https://github.com/tconbeer/harlequin/issues/new?template=crash_report.md"

def get_crash_report_dir() -> Path
def root_cause(error: BaseException) -> BaseException
def build_crash_report(error: BaseException, context: Mapping[str, Any],
                       program: str = "harlequin") -> str
def write_crash_report(report: str) -> Path | None
def crash_message(report_path: Path | None, error: BaseException, saved: bool,
                  program: str = "harlequin") -> str
```

- **Location.** `platformdirs.user_log_path(appname="harlequin", appauthor=False)` —
  `~/Library/Logs/harlequin` on macOS, `~/.local/state/harlequin/log` on Linux,
  `%LOCALAPPDATA%\harlequin\Logs` on Windows. Not the cache dir: a cache is what a user is told to
  delete when things go wrong, and this is the file we are asking them to keep. Match `config.py`'s
  `appauthor=False` — `editor_cache`/`catalog_cache` omit it and so nest twice on Windows; don't copy
  that.
- **`root_cause`** unwraps a Textual `WorkerFailed` via `getattr(error, "error", None)` rather than
  importing it, so this module stays headless. It matters: `database_tree.py:498` (`_loader`) and
  `code_editor.py:88` (`read_symbols`) are the two `@work` decorators in `src/` that omit
  `exit_on_error=False`, so they *do* reach `_handle_exception`, wrapped. A report naming
  `WorkerFailed` is a useless report.
- **Content**, in order: a header telling the user to review the file before sharing and linking
  `ISSUE_URL`; `runtime_report()`; the `context` the caller supplied; `traceback.format_exception`
  for the root cause and, when different, the wrapper — **no `show_locals`**; and last, under a
  heading that says what it is, the SQL in the active buffer.
- **Plain text, not markdown**, written as `crash-{utc:%Y%m%dT%H%M%SZ}-{pid}.log`. The user pastes
  this into a fenced block in an issue, and a markdown report's own ` ``` ` fences would break out of
  it. Sections are delimited with plain `=== SECTION ===` rules for the same reason.
- **The active buffer's SQL is included deliberately.** It is usually what is needed to reproduce a
  TUI bug, the file is local, and the user chooses whether to paste it. The tradeoff is real — SQL
  holds table names and literals — so it goes last, under an explicit heading, and both the panel and
  the issue template say to review before sharing. Only the *active* buffer; the rest live in the
  recovery file.
- **Redaction.** The whole assembled string goes through `redact.redact_text()` once at the end — one
  choke point, matching `hsql/diagnostics.py::_write`'s discipline.
- **Pruning.** Newest `CRASH_REPORT_KEEP` kept. Distinct filenames per crash, so a loop cannot
  destroy the evidence. `write_crash_report` returns `None` and raises nothing when the directory is
  unwritable.

`exception.py` gains `class HarlequinCrashError(HarlequinError): pass`, so the panel is
`pretty_error_message(HarlequinCrashError(crash_message(...), title="Harlequin crashed."))` — no new
rendering code, rich stays lazily imported, and it looks like every other Harlequin panel.

## The handler (`app_base.py`)

Two hooks with working base implementations, so `keys_app.py` gets crash reports too:

```python
def _save_work_on_crash(self) -> bool:
    """Persist anything the user would lose. False if there was nothing to save."""
    return False


def _crash_context(self) -> dict[str, Any]:
    """What this app was doing, for the crash report."""
    return {}


def _handle_exception(self, error: Exception) -> None:
    if self._exit or self._crash_handled:
        return
    self._crash_handled = True

    saved = False
    try:
        saved = self._save_work_on_crash()
    except BaseException:
        pass

    report_path = None
    try:
        report = build_crash_report(error, self._crash_context())
        report_path = write_crash_report(report)
    except BaseException:
        pass

    try:
        self.bell()
        self._return_code = 1
        # run_test re-raises from these
        if self._exception is None:
            self._exception = error
            self._exception_event.set()
        message = crash_message(report_path, error, saved)
        panel = HarlequinCrashError(message, title="Harlequin crashed.")
        self.panic(pretty_error_message(panel))
    except BaseException:
        # last resort: the user gets *something*
        super()._handle_exception(error)
        return

    # textual run --dev, i.e. `make serve`: append the full traceback at index 1
    if "debug" in self.features:
        super()._handle_exception(error)
```

`self._crash_handled = False` is set at the **top** of `AppBase.__init__`, before the theme
`try/except` that can call `self.exit()`. Order matters: save work first (most valuable, most likely
to fail), report second, render last. Every stage is independently wrapped — **the crash handler must
not be able to raise**, since it runs inside an `except` block, and raising there is how a bug-fix
change becomes the bug.

`panic()` puts the friendly panel at index 0, which is exactly what `_print_error_renderables` shows
outside debug mode; in debug mode `super()` appends the traceback and all renderables print. One code
path, both audiences. A second exception returns immediately.

**`Harlequin` overrides both hooks:**

- `_save_work_on_crash()` builds the same guarded `Cache` as the checkpoint, falling back to
  `self._last_checkpointed_cache` if building it raises (plausible — the crash may be *in* the
  editor, and `buffers` calls `_save_loaded_buffer()`), writes it, and renames it to
  `recovered-<utc>.pickle` so the next start adopts it regardless of age. Separately, in its own
  `try`, calls `update_catalog_cache(...)`: query history is user work too. Returns whether anything
  was written, so the message does not claim a save that did not happen.
- `_crash_context()` returns adapter class + module + distribution version, adapter entry-point name,
  profile name, keymaps, theme, whether a connection was open, buffer count, `str(self.size)`, and
  the recovery path. All cheap, all wrapped. It deliberately does **not** call `adapter_facts()` —
  importing every installed adapter mid-crash is slow and a fresh place to crash.

`Harlequin.__init__` gains `adapter_name: str | None = None`; `cli.py` already computes it at line
492 but passes only the instance.

### Textual internals this leans on

`textual==8.2.8`, pinned exactly. Each use site gets a comment naming what it is for, and each has a
test that fails loudly rather than silently reverting us to raw tracebacks:

| Internal | Why | Tripwire |
|---|---|---|
| `self._exit` | existing guard for exceptions after `exit()` | unchanged |
| `self._return_code = 1` | so `cli.py` can forward it | test asserts `app.return_code == 1` |
| `self._exception` / `_exception_event` | **`run_test` re-raises from these.** Not setting them turns every crashing functional test silently green | the crash test is written as `with pytest.raises(RuntimeError)` |
| `self.panic(...)` | appends to `_exit_renderables`, closes the pump | test asserts our panel is at index 0 and no traceback is |
| `"debug" in self.features` | `parse_features(os.getenv("TEXTUAL",""))` | test parametrizes over `features` |

Deliberately untouched: `_fatal_error`, `_print_error_renderables`, `_exit_renderables` (written only
through `panic()`), `_close_messages_no_wait` (called only through `panic()`).

## `cli.py`

- **Arm redaction.** Immediately after `config = merge_profile_with_cli(...)` (~line 509) and
  *before* the `config.pop(...)` cascade strips `conn_str` and the adapter options:
  `hide_secrets_in(config, adapter_cls.ADAPTER_OPTIONS)`. Today that function is called from exactly
  one place — `hsql/cli.py:447` — so in an IDE process `redact_text()` masks nothing. Without this,
  the crash report is not safe to paste.
- **Forward the exit code.** `tui.run()` → `ctx.exit(tui.return_code or 0)`, and the same for
  `app.run()` in the keys-app callback. This also closes a live bug: `self.exit(return_code=2, ...)`
  on a bad theme, a bad `viewer_max_rows` or a failed connection currently exits **0**.
- Pass `adapter_name=adapter_name` to `Harlequin(...)`.

## hsql

hsql gets the same treatment at its own boundary, reusing `crash.py` (which is headless, so the
`hsql does not reach the TUI` contract holds).

**A new exit code.** `diagnostics.ExitCode` gains:

```python
CRASH = 70
"""hsql hit a bug in itself. `sysexits.h`'s EX_SOFTWARE."""
```

70 rather than the next free small integer: `sysexits.h` reserves it for an internal software error,
and it cannot be confused with the 1–4 hsql already documents or with 130.

**`exit_code_for()` does not change.** It maps *caught* failures, and an unrecognized error caught
while running a query genuinely is a query failure — that is what `QUERY` means. `CRASH` belongs only
at the boundary where nothing else caught the error at all.

**`main()` (`hsql/__init__.py`) gains a final handler**, after the `ClickException` and
`Abort`/`KeyboardInterrupt` clauses:

```python
except BaseException as e:
    report_path = write_crash_report(build_crash_report(e, _crash_context(argv), program="hsql"))
    diagnostics.report_crash(report_path)
    sys.exit(ExitCode.CRASH)
```

- `_crash_context(argv)` carries the argv, the resolved subcommand or mode, and the adapter and
  profile names if the run got far enough to know them. **argv passes through
  `redact.redact_conn_str()` before `redact_text()`** — a crash during parsing happens before
  `hide_secrets_in()` runs at `hsql/cli.py:447`, so the registered-secret set is empty and the
  span-masking is the only thing that catches a DSN typed on the command line.
- `diagnostics.report_crash(path)` is the one new stderr line, going through `_write()` like
  everything else on that stream: what happened, where the report is, and the issue link. Plain text,
  no panel — hsql writes no box drawing.
- No buffer section: hsql has no buffers. The `-c` / `-f` SQL is already in the redacted argv.

**Not in scope for hsql**, and worth its own pass later: the broad `except Exception` handlers inside
`hsql/cli.py` route an internal bug through `exit_code_for()` and so still report it as `QUERY`. The
`main()` boundary is the clean win; narrowing those handlers is a separate audit.

## The crash report issue template

New `.github/ISSUE_TEMPLATE/crash_report.md`, which the crash message and `crash.ISSUE_URL` point at.
It is deliberately *not* `bug_report.md` with a paste box bolted on: the whole environment block
`bug_report.md` asks for as free text is already in the report, so this template asks for the file
and the two things the file cannot know — what the user was doing, and whether they can reproduce it.

```markdown
---
name: Crash Report
about: Harlequin or hsql exited unexpectedly and wrote a crash report.
title: 'Crash: '
labels: 'bug'
assignees: ''

---
**Before proceeding, please acknowledge**:
- [ ] I have searched Issues and Discussions in this repo for this error.

**Paste your crash report below.**

Harlequin and `hsql` write a crash report and print its path when they exit
unexpectedly. If you still have that message on screen, the path is in it.
Otherwise, the reports are here:

| OS | Location |
|---|---|
| macOS | `~/Library/Logs/harlequin/` |
| Linux | `~/.local/state/harlequin/log/` |
| Windows | `%LOCALAPPDATA%\harlequin\Logs\` |

The newest `crash-*.log` is the one you want.

> [!IMPORTANT]
> Please read the report before pasting it. It contains your configuration
> (with passwords masked) and the SQL that was in your active buffer.

<details>
<summary>Crash report</summary>

```
PASTE YOUR CRASH REPORT HERE
```

</details>

**What were you doing when it crashed?**
Steps to reproduce it, if you know them.

**Anything else?**
Screenshots, or anything the report doesn't cover.

**Contributing**
Are you interested in contributing a fix?
- [ ] Yes
- [ ] Maybe
- [ ] No
```

`bug_report.md` gains one line under its acknowledgements pointing here: *"Did Harlequin exit with an
error and print a crash report path? Use the Crash Report template instead."*

## Ordered commits

Each is independently green under `make check`.

1. `write_cache` atomic and non-raising. `editor_cache.py` only; no behavior change visible to the app.
2. Recovery file + rename-before-replay. `editor_cache.py`, `code_editor.py`, `app.py`'s
   `clear_recovery`, conftest patches. Nothing writes a recovery file yet, so this is provably inert
   on the happy path.
3. The checkpoint timer — commit 2's machinery gets a producer.
4. `environment.py` + the `info.py` refactor. Acceptance: `hsql --info` byte-identical. Import-linter
   contract and `HEADLESS_IMPORTS` in the same commit.
5. `crash.py`, `HarlequinCrashError`, `AppBase._handle_exception`, `Harlequin`'s two hooks,
   `hide_secrets_in`, `ctx.exit(return_code)`, and the `test_cli.py` fixture fix.
6. `ExitCode.CRASH`, `diagnostics.report_crash`, the `main()` handler, hsql's `_crash_context`.
7. `.github/ISSUE_TEMPLATE/crash_report.md` and the `bug_report.md` pointer.

## Test infrastructure — the landmine

`run_test`'s re-raise path goes through `_handle_exception`, so **every failing functional test in
the suite would write a crash report into the developer's real `user_log_dir`.** Add to
`tests/conftest.py`, autouse and session-wide with no marker opt-out, in the spirit of the existing
`no_discovered_config`:

```python
@pytest.fixture(autouse=True)
def crash_reports_go_to_tmp(monkeypatch, tmp_path) -> Path:
    """Every crash a test causes writes here, not into the developer's log dir."""
    report_dir = tmp_path / "crash-reports"
    monkeypatch.setattr("harlequin.crash.get_crash_report_dir", lambda: report_dir)
    return report_dir
```

Extend `tests/functional_tests/conftest.py::no_use_buffer_cache` in the same commit as the
checkpoint timer: patch `harlequin.components.code_editor.adopt_recovery` → `lambda: (None, None)`
and `harlequin.app.write_recovery` / `clear_recovery` → no-ops. Keep calling the existing
`harlequin.app.write_editor_cache` name so the current patch keeps working.

`tests/unit_tests/test_cli.py::mock_harlequin` is `MagicMock(spec=Harlequin)`, so `tui.return_code`
is a MagicMock and `ctx.exit(mock)` makes `res.exit_code` a MagicMock — **about fifteen existing
assertions break.** Set `mock_harlequin.return_value.return_code = 0` in the fixture, and add a test
that `return_code = 1` produces `res.exit_code == 1`.

## Tests to add

**`tests/unit_tests/test_editor_cache.py`** (new): no `*.tmp` left behind; a `pickle.dump` that
raises leaves the previous cache byte-identical and returns `False`; an unwritable directory returns
`False`; `adopt_recovery` renames to `.replayed` *before* returning (the crash-loop guarantee); it
ignores a fresh `recovery-<pid>.pickle` and adopts a stale one; it always adopts `recovered-*`.

**`tests/unit_tests/test_crash.py`** (new): the report names the exception type, message, version and
frames; **it prints no secret** — `hide_secrets_in({"conn_str": ["postgres://u:hunter2-and-more@h/db"]})`,
raise an error quoting the DSN, assert the secret is absent and `********` present, mirroring
`test_the_debug_screen_prints_no_secret`; the report contains no ` ``` ` fence, so it pastes into one;
`root_cause` unwraps a `WorkerFailed`; `write_crash_report` prunes to `CRASH_REPORT_KEEP` and returns
`None` on `PermissionError`.

**`tests/functional_tests/test_crash.py`** (new), injecting through `_handle_exception` directly:

```python
with pytest.raises(RuntimeError):
    async with app.run_test() as pilot:
        ...
        app._handle_exception(RuntimeError("boom"))
        await pilot.pause()
```

Assert `app.return_code == 1`; one crash file in the redirected dir; a recovery file holding the
buffer text (`@pytest.mark.use_cache`); `"boom"` and the report path in the rendered
`_exit_renderables[0]`; `"Traceback (most recent call last)"` **not** in it. Plus: two exceptions in a
row produce one report and no recursion; a `write_crash_report` that raises still exits 1 with a
panel; `Harlequin(...)` constructed but never run returns `False` from `_save_work_on_crash` without
raising (crash before mount); a blank-buffer checkpoint leaves a good recovery file unchanged; and
the crash loop — plant a `recovered-*.pickle`, make `action_new_buffer` raise, run and crash, then run
a second app and assert one empty buffer and the poisoned file now `.replayed`.

**`tests/unit_tests/test_hsql.py`**: a callback that raises exits `ExitCode.CRASH` (70) and not
`QUERY`; the stderr line names the report path; a DSN passed as `CONN_STR` does not appear in a
report written before `hide_secrets_in` has run.

**No snapshot churn.** Nothing renders in-app except the recovery toast, which is a standard Textual
notification; the crash message reaches stderr after the app is gone.

## Verification

```bash
localedef -i en_US -f UTF-8 en_US.UTF-8   # fresh container only
make check                                 # ruff, pytest 3.10 + 3.12, mypy
uv run lint-imports
uv run pytest tests/unit_tests/test_import_hygiene.py tests/unit_tests/test_crash.py \
              tests/unit_tests/test_editor_cache.py
uv run pytest tests/functional_tests/test_crash.py tests/functional_tests/test_cache.py -n0
```

By hand:

1. `uv run harlequin f1.db`, type SQL, wait past the checkpoint interval, `kill -9`, restart — the
   SQL is back and the toast says where it came from.
2. Temporarily raise from a key handler and run **without** dev mode (`uv run harlequin f1.db`, not
   `make serve`): the panel prints instead of a traceback, `echo $?` is 1, and the report at the
   named path holds the traceback. Repeat with a profile carrying a password and confirm it is masked
   and that no local variables appear.
3. Temporarily raise from `EditorCollection.on_mount`, crash, remove the raise, restart twice: the
   second start is clean and the poisoned file is `.replayed`.
4. `make serve` and crash: the full traceback still prints for developers.
5. Temporarily raise from an hsql mode: `echo $?` is 70, the stderr line names the report, and a DSN
   on the command line is masked in it.
6. Paste a real report into the crash report template's fenced block on a scratch issue and confirm
   it renders as one block.

## Known risks

- **Partial-replay convergence.** If a crash happens partway through replaying recovered buffers,
  `_save_work_on_crash` may write a new `recovered-*.pickle` holding the subset that loaded. The next
  start replays fewer buffers, so it converges and terminates — and salvages what it can — but it can
  take more than one restart.
- **Behavior change: both commands now exit non-zero where they exited 0.** `harlequin` returns 1 on a
  crash and 2 on a config or connection error; `hsql` returns 70 where it previously let Python exit
  1. Correct, but user-visible, so both get a changelog line.
- **The report names `sys.executable`, the config path and `SHELL`**, all of which reveal a username —
  and all of which `hsql --info` already prints, so this is consistent rather than new.

## Deliberately out of scope

- **`_handle_worker_error` swallows unknown worker errors**
  ([#1117](https://github.com/tconbeer/harlequin/issues/1117)). `app.py:576` branches on two worker
  names with no `else`, so any other worker error vanishes silently. The fix needs its own triage of
  which workers can fail benignly, so it is filed separately.
- **Debug Info screen work** — environment rows and a "copy a bug report" binding. Cheap once
  `environment.py` exists, but it is the only piece with snapshot churn: a visible version or Python
  row bakes `2.12.2` and `3.10.14` into `Debug Info Screen.svg`, which then fails on every CI Python
  and after every release. If picked up, put the rows in a **collapsed** `Collapsible` (collapsed
  children never reach the SVG) and back them with a non-snapshot unit test. Drive-by finding for
  whoever does: `DebugInfoScreen.move_focus()` queries `#collapsible-client-adapter-details`, which
  `AdapterDebugInfo.parse_info()` never produces — dead code with a latent `NoMatches`.
- **Narrowing hsql's broad `except Exception` handlers** so an internal bug caught inside `cli.py`
  reports `CRASH` rather than `QUERY`.
- A prefilled GitHub issue URL (the ~8KB URL cap cannot carry a traceback) and converting the
  templates into YAML issue forms.
- A `harlequin --debug` flag: the report already holds the full traceback, and `TEXTUAL=debug` /
  `make serve` already prints it inline.
