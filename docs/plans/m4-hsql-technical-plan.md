# M4 Technical Plan — the handoff, and the two hooks

Implementation plan for milestone M4 of [Harlequin for agents](./harlequin-for-agents.md),
following [M1](./m1-hsql-technical-plan.md), [M2](./m2-hsql-technical-plan.md) and
[M3](./m3-hsql-technical-plan.md). M1 built the command, M2 taught it to describe itself, M3
wrote the docs and the skill an agent reads before it runs. This one is Workstream F: what
happens when the agent and the human need to hand work to each other.

**M4's scope, from the roadmap:** the skill, `hsql open`, JSONL history, "Copy CLI command",
and the external-command hook. **The skill (E1) shipped in M3** — it is in the wheel, prints
from `hsql --skill`, and installs as a plugin — so what is left is the four handoff features.
Two of those turn out to be three: the external-command hook covers two requests that share
almost nothing, and this plan splits them (§6.1).

**The issues M4 closes.** [#1102](https://github.com/tconbeer/harlequin/issues/1102), external
editor support, was opened while this plan was being written and carries a reference
implementation from Posting. [#952](https://github.com/tconbeer/harlequin/issues/952),
natural language to SQL, is closed `not planned` — "I won't be adding this to Harlequin" — and
the command hook is the answer that stays true to that: Harlequin runs the tool the user
already trusts, with the user's own credentials, and embeds no model (§8).

**Bottom line up front.** Three claims, each measured below.

1. **The handoff between the two commands is a process boundary, and everything cheap about
   it is on the far side of one.** `hsql --open` cannot put a buffer in front of a human
   in-process: `harlequin.editor_cache` imports Textual — 341 modules, +164ms over a bare
   interpreter, against a whole `hsql --version` of 118ms — and it is rewritten *whole* at
   `action_quit`, so a second writer would race a running IDE for the file that holds every
   open buffer (§1.1). `--open` execs a documented flag on the other command instead, and the
   argv it builds is the same module the TUI's "Copy CLI command" reads backwards (§3.1, §3.5).
2. **The query history both commands should share cannot be either of the things that hold it
   today.** The IDE's history is a pickle inside the catalog cache, written once, at quit — so
   a crash, a `kill`, or a container that goes away loses the session — and it lives in
   `harlequin.history`, which imports rich, which is on the headless FORBIDDEN list and costs
   +46ms on a 118ms command (§1.2, §1.3). A JSONL log in a rich-free module is what both can
   write, and one measurement settles its concurrency: 8 processes appending 200 records each,
   at 200 B, 8 KB and 200 KB per record, produced 1,600 well-formed lines every time with no
   lock at all (§3.2).
3. **The external editor and the AI hook are two features, and the terminal is why.** One
   needs the terminal and must suspend the app; the other must not have it and must not block
   the app. They differ in thread, in exchange medium, in failure mode, in what cancels them,
   and in whether the user's query leaves the machine (§6.1). And a shell command that came
   from a config file is not the same as one that came from `$EDITOR`: config files merge from
   the current working directory, so a repository you cloned can define one (§1.7). The
   environment is the user; a file is not.

---

## 1. Where things are today

Measured on this checkout — 2.12.2, the release the M2 safety work closed — with Python
3.10.15 on Linux, textual 8.2.8, textual-textarea 0.18.1. Timings are the minimum of five
subprocess runs; a bare `python -c "import sys"` is 12.7ms on this machine and is subtracted
where an import cost is given.

### 1.1 Nothing outside the IDE can put a query in front of a human

`harlequin` takes `CONN_STR` positionally and has no file argument. `-f` is not free: it is
`--show-files`, the directory the Data Catalog shows in a file tree. The only way to get a
`.sql` file into a buffer today is `code_editor.load` — the editor's Open Query action, a path
input inside a running app.

The product plan's note that "buffers already persist through `editor_cache.py`, so the
machinery is close at hand" is the one part of §10 that measurement contradicts, in both
directions that matter:

```
import harlequin.editor_cache    175.1ms   (+164.4ms)   341 modules   textual: yes
import harlequin.history          59.0ms   ( +45.9ms)   142 modules   textual: no, rich: yes
import harlequin.catalog_cache    (rich: yes, via harlequin.history)
```

`editor_cache.BufferState` holds a `textual.widgets.text_area.Selection`, so reading or
writing that cache imports the TUI. And the cache is not an append-only store: `action_quit`
writes `Cache(focus_index, buffers)` — every open buffer, all at once (`app.py:963`). A second
process appending a draft to it would be writing the file a running IDE is about to overwrite
from memory.

So the handoff cannot be "hsql writes the buffer cache." It has to be an argument to the other
command.

### 1.2 History is a pickle, written once, at quit

`History` is a `deque(maxlen=500)` of `QueryExecution` records, per connection hash, stored
inside `CatalogCache.history` and pickled to the user cache dir. It is loaded on
`CatalogCacheLoaded` and written in exactly one place: `action_quit` (`app.py:965-977`),
alongside the editor cache. Every `append_to_history()` between start-up and quit lives only
in memory.

Three consequences, all of which M4 has to deal with:

- **A session that does not exit cleanly has no history.** A crash, a `kill`, a closed
  terminal, a container reclaimed under a remote session: the queries are gone.
- **`hsql` writes nothing.** An agent can run forty queries against the human's database and
  leave no trace the human's History screen will ever show. This is Workstream B's "closes the
  loop the other way", and it is currently closed in neither direction.
- **A pickle is not a thing "both agents and humans can read with ordinary tools"** — the
  product plan's own criterion. Reading it requires importing `harlequin.history` to unpickle
  the dataclass, which imports rich.

### 1.3 `harlequin.history` is a rendering, and headless code may not import it

`QueryExecution.__rich__` builds a `Group` of a `Columns` and a `Text`: the History screen's
row, in the dataclass that holds the record. `tests/unit_tests/test_import_hygiene.py` lists
`rich` in `FORBIDDEN` for every headless import, for the reason the module docstring gives —
nothing headless renders — and it is the guard that caught rich arriving sideways through
`textual_fastdatatable` before 0.17.1 deferred it.

Measured, that guard is not ceremony: `harlequin.history` costs +46ms, which is 39% of a whole
`hsql --version` invocation:

```
hsql --version         114-119ms
harlequin --version    621-636ms
```

`harlequin.catalog_cache` imports `harlequin.history`, so it carries rich too — which means
`get_connection_hash()`, the function that decides *which* history a session gets, is also
unreachable from `hsql` today. Whatever writes the shared log has to live below both.

### 1.4 Nothing in the app knows what command line started it

"Copy CLI command" has to render an `hsql` invocation that reproduces what the human is
looking at. The app cannot: `Harlequin.__init__` receives an *instantiated* adapter, plus
`profile_name`, `connection_hash`, and the TUI's own settings. `conn_str` is consumed in
`cli.py` and handed to the adapter's constructor; no attribute on `HarlequinAdapter` carries
it — adapters store it themselves, and the contract does not require them to. `--config-path`,
`-a`, `--read-only` and any adapter options typed on the command line are gone by the time the
app exists.

The app also never calls `redact.hide_secrets_in()`. `hsql` does, on every run; the IDE does
not, so `redact_text()` in an IDE process masks nothing, because `_HIDDEN` is empty. Anything
M4 adds that prints or copies a value — a clipboard string, a log line — needs that call to
have happened.

### 1.5 Actions are a fixed registry, and an unknown name crashes the app on mount

`HARLEQUIN_ACTIONS` maps ~90 action names to `(target widget class, action method)`.
Keymaps — plugin or config — map keys to those names, and `action_bind_keymaps()` /
`bind_keys()` look each one up with `HARLEQUIN_ACTIONS[binding.action]`. `keymap.py` carries
the TODO: `# TODO: ADD VALIDATION when creating bindings from config`.

Measured, with a keymap whose only binding names `code_editor.formt`:

```
KeyError: 'code_editor.formt'
  in action_bind_keymaps, during on_mount
```

The app dies in mount with a traceback, on a typo in a config file, and `harlequin --keys` —
the tool for fixing keymaps — is a second Textual app that reads the same registry. M4 makes
action names come partly from config (one per configured command), which turns this from a
papercut into the failure mode of a new feature.

### 1.6 Nothing runs a subprocess, `suspend()` exists, and tests cannot reach it

There is no `subprocess`, `os.system` or `$EDITOR` anywhere in `src/`. Textual's
`App.suspend()` is a synchronous context manager that publishes a suspend signal, drops out of
application mode, redirects `stdout`/`stderr` back to the real ones for the duration, and
resumes with `refresh(layout=True)`. Two things about it shape §3.6:

- It is **main-thread only**, by construction: it redirects the process's streams and drives
  the driver. Nothing in a `@work(thread=True)` worker may call it.
- It is **unavailable in tests.** `can_suspend` is True on the Linux and Windows drivers and
  False on the base driver, which the headless driver inherits. Measured inside `run_test()`:

```
DRIVER: HeadlessDriver can_suspend: False
RAISED: SuspendNotSupported App.suspend is not supported in this environment.
```

So the external-editor path needs a seam a test can patch, and the `SuspendNotSupported`
branch is itself a user-visible behavior to test rather than an assertion that never fires.

### 1.7 Config files merge from the working directory, so a config file is not the user

`load_config()` reads, in priority order, an explicit `--config-path`, then `./.harlequin.toml`
and `./pyproject.toml`, then the user config dir, then home — and merges them, nearest file
wins, per profile and per keymap. A repository you clone can therefore supply a profile, a
keymap, and (today) a duckdb `init_path`, which runs SQL at connection time.

That is already a real trust surface, and it is bounded: arbitrary SQL against a database the
user chose to open. A `[commands]` table would make it arbitrary code as the user, from a file
they may never have read — and, as Ted put it, a git-synced dotfiles repo makes the same true
of a file in the user's *own* config dir. `$EDITOR`, by contrast, is the user's own shell
session. That asymmetry is the whole design of §3.8.

### 1.8 What the skill can say about handing off, and what it cannot

M3 shipped the skill with section 9, "Know when to hand off," and it currently reads: stop,
and tell the human to run `harlequin -P <profile>`. That is the best available advice for an
agent holding a draft migration, and it loses the draft: the human gets an empty editor and
the agent's SQL is in a transcript somewhere above. §3.1 is what lets that paragraph say
`hsql --open -f draft.sql -P prod` instead, and M3's §7 already names it as the paragraph M4
rewrites.

---

## 2. The obstacles, stated plainly

1. **The two commands cannot share a process.** One is a TUI that owns the terminal; the other
   must not import Textual. Every handoff feature in M4 is therefore an argv, a file, or a
   log — never an import.
2. **The store that both commands write has to be below rich.** Not `harlequin.history`, not
   `catalog_cache`, and not a pickle of a dataclass whose `__repr__` is a Rich renderable.
3. **A shared, append-only log has more than one writer, and no way to lock cheaply on every
   platform.** The design has to be atomic per record, or tolerant of a record that isn't.
4. **The app does not remember how it was started**, and half of what it would need to
   remember is secret.
5. **Running someone else's program from inside a Textual app has two incompatible shapes,**
   and picking one shape for both produces a config whose validity table is mostly errors.
6. **A config file can now ask Harlequin to execute a shell command,** and config files arrive
   from directories the user did not audit.
7. **A user-defined action name has to exist before a keymap can bind it,** and today an
   action name that does not exist is a crash rather than a message.

---

## 3. Target architecture

Six deliverables, three new modules and one new mode, all of the new modules below rich:

```
harlequin/
  invocation.py        # what a command line said; renders back to either command's argv
  query_log.py         # the append-only JSONL log, and its reader
  external.py          # running someone else's program: the two shapes, and the trust gate
  hsql/modes/history.py  # the log, as a result set, like every other mode
```

`invocation.py` and `query_log.py` are importable by `hsql` (stdlib plus `platformdirs` and
`harlequin.redact`), and `modes/history.py` is imported by the callback only when the mode is
chosen, as every mode is. `external.py` is the IDE's, and imports Textual only under
`TYPE_CHECKING`; the app owns the widgets, this module owns the process.

### 3.1 `hsql --open`, and the flag on the other side

**A mode option, not a subcommand.** M2 settled that (`hsql catalog` versus a DuckDB file
named `catalog`); `--open` needs no new rule, and reuses the SQL sources the command already
has. All three of these work, because `-c`, `-f` and stdin are already one layer:

```bash
hsql --open -f migration.sql -P prod      # the file, in the human's IDE
hsql --open -c "select * from orders …" -P prod
claude -p "write the backfill" | hsql --open -P prod
```

**The other side is a documented flag, not a private channel.** `harlequin --open PATH`
(repeatable) loads each file into a new buffer, activates the last, and **executes nothing**.
It is worth having on its own: `harlequin --open report.sql` is what a human wants too, and
`-f` was never available for it (§1.1).

**What crosses the boundary.** `--open` does not connect to a database — it is a handoff, not
a run — so what it forwards is the connection *description*: `-P`, `--config-path`, `-a` when
it was typed, `--read-only`, and `CONN_STR`. Adapter options typed on the command line are
rendered back from their declarations (`AbstractOption`, M2's `to_dict()`), with one refusal:
**an option declared `secret=True` is not forwarded.** hsql exits in a second; the IDE it
launches lives for hours, and `ps` shows an argv for as long as the process holds it. The
error names the fix, which is the fix the skill already teaches:

```
hsql: --password cannot be forwarded to the IDE. Put it in a profile and pass -P.
```

**Launching.** `[sys.executable, "-m", "harlequin", *argv]`, never a PATH lookup:
`harlequin.__main__` exists, `python -m harlequin --version` works today, and the interpreter
running `hsql` is by definition the one that has Harlequin installed. On POSIX, `os.execv`
replaces the process — no second interpreter resident under the IDE, and signals and exit
status belong to the app that owns the screen. On Windows, `os.execv` returns control to the
shell while the child still runs, so there it is `subprocess.run()` and the child's return
code.

**The terminal has to be handed over too**, and this is the part that is easy to get wrong.
After `cat q.sql | hsql --open`, fd 0 is an exhausted pipe; the IDE would start and read no
keys. So `--open` reattaches 0/1/2 from the controlling terminal (`/dev/tty`, `CONIN$`/
`CONOUT$` on Windows) whenever they are not a TTY, and refuses with a usage error when there
is no controlling terminal to reattach to — a CI job that pipes into `--open` gets a sentence,
not a hung process.

**The scratch file.** `-c` and stdin have no path, so `--open` writes one:
`<user_state_dir>/harlequin/open/YYYYmmdd-HHMMSS-<pid>.sql`, pruned of anything older than
seven days on each invocation. State, not cache: an unsaved draft is not disposable. A query
that came from `-f` is passed by its own path — the IDE copies text into a buffer and never
writes back, so the human's file is not touched either way.

### 3.2 `harlequin.query_log` — one log, two writers

One file, JSON Lines, at `<user_state_dir>/harlequin/queries.jsonl`, one object per statement:

```json
{"v":1,"at":"2026-08-31T22:41:07.123456+00:00","program":"hsql","connection":"9f86d0…",
 "profile":"prod","adapter":"postgres","sql":"select count(*) from orders","status":"ok",
 "rows":1,"truncated":false,"elapsed_ms":34.1,"error":null}
```

- **`at` is UTC, ISO-8601.** The History screen renders local; a log two machines may read
  does not store a naive timestamp.
- **`sql` goes through `redact.redact_text()`** before it is written. A query can carry a
  credential — `attach 'postgres://user:pw@host'`, `create user … password '…'` — and the
  product plan's §12 lists query history as a leak surface that sits outside the declaration
  mechanism. It sits outside the *declaration*, not outside `redact`: `redact_text()` masks
  every value this process was told to hide. Which means the IDE has to start calling
  `hide_secrets_in()` (§1.4) — one line in `cli.py`, and the debug screen gets more honest for
  free.
- **`connection` is the same id the caches key on**, so the History screen can show this
  database's queries and an agent can `grep` for them. `get_connection_hash()` moves out of
  `catalog_cache` into `query_log` (it is a hash of a dict; it never needed rich), and
  `catalog_cache` imports it from there.
- **`status`** is `ok`, `error` or `canceled` — the third because M2 established that a
  cancelled cursor is indistinguishable from an empty result unless the caller attributes it.

**Concurrency: one `os.write()` per record, on an `O_APPEND` descriptor, and no lock.**
Measured on this container, 8 concurrent processes × 200 records:

| record size | lines written | expected | malformed |
| --- | --- | --- | --- |
| ~200 B | 1600 | 1600 | 0 |
| ~8 KB | 1600 | 1600 | 0 |
| ~200 KB | 1600 | 1600 | 0 |

An `O_APPEND` write on Linux holds the inode lock for the whole write; the PIPE_BUF limit
people remember is about pipes. This is not guaranteed by POSIX for every filesystem, and
Windows' CRT implements `O_APPEND` as seek-then-write, so the **reader tolerates a bad line**:
a record that will not parse is skipped, not raised. That is one `try/except` and it makes the
format robust on an NFS home directory as well.

**Failure never fails a query.** An unwritable log disables logging for the process; `hsql`
says so once on stderr through `diagnostics` (never stdout — §"stdout belongs to query
output"), the IDE notifies once. **Rotation** is size-based and checked once per process, on
first write: over 32 MiB, rename to `queries.jsonl.1`, replacing any existing one. Two
processes rotating at once lose nothing — both then append to a new file.

**Opt-out** is a profile key, `history = false`, read by both commands. Nothing else reports
the log's path: `--info` describes the installation, not the user's data, and the mode below
is how a caller reads the records without ever needing to know where they are.

### 3.3 `hsql --history`, the mode that reads the log

A mode option, beside `--catalog` and `--catalog-search`, and it costs almost nothing to build
because of what M2 settled: **a listing is a result set**, so the whole output layer is already
written. `--history` builds an Arrow table from the log's records and hands it to the emitters
every other mode uses — which means `--format json`, `--jsonl`, `--csv`, `-x`, `-o PATH`,
`--stats` and the layout flags all work on it the day it lands, and none of them is a line of
new code.

```bash
hsql --history --limit 20                 # the last 20, as a table
hsql --history -P prod --json             # this profile's, for an agent
hsql --history -x --limit 1               # the last one, vertically
```

- **It connects to nothing.** It is the only mode that reads a profile, builds an adapter, and
  never opens a connection: the adapter is constructed solely to ask it for `connection_id`,
  which is what filters the log to one database. A history mode that woke a warehouse to list
  queries would be its own kind of joke.
- **Scope follows the invocation.** `-P`, `-a` or a `CONN_STR` narrows to that connection;
  with none of them, the mode reports every connection, because "I typed `hsql --history` and
  got someone else's idea of the default database" is the surprising answer. The `profile` and
  `adapter` columns are what tell them apart.
- **Newest first**, which is what every shell means by history, and what makes `--limit` mean
  "the most recent N" rather than "the oldest N". `--limit` is honored here rather than refused
  as it is under `--catalog`: the store has a row count, so a hard limit on it is exact.
  Default 500 — the size of the deque the History screen has always kept — and `-1` for all.
- **Columns:** `at`, `program`, `profile`, `adapter`, `status`, `rows`, `elapsed_ms`, `sql`.
- **`sql` is folded to one line, in every format.** Two invariants force it and one fact
  makes it harmless: `layout.py` pads by terminal cells and has no concept of a cell that
  spans rows, and `--format table` and `--format csv` agree cell for cell, pinned by the
  golden-format snapshots (M1 §5) — so a column that is verbatim in JSON and folded in a
  table is not on offer. Folding loses formatting, not meaning: `" ".join(sql.split())` is
  still the query, and still runnable.
- **The read is a bounded tail**, not a parse of the file: seek to the end, read backwards in
  chunks until N matching records or the head of the file, skipping any line that will not
  parse (§3.2). `hsql --history --limit 20` costs the same on a 30 MB log as on a 30 KB one.

This is the mode the skill teaches for B7 — an agent joining a task mid-stream reads what the
human has been running, in the format it wants, on Windows as well as anywhere else, with no
`jq` and no path to know.

### 3.4 The History screen reads the log

`History` stops being a pickled deque and becomes a bounded tail of the log:
`History.tail(connection=…, n=500)` reads the last ~1 MiB, splits, parses newest-first, skips
what will not parse, and stops at 500 records for this connection. `QueryExecution` keeps its
`__rich__`; it just gets its rows from a file rather than from `CatalogCache`.

Three things follow, and they are the point of the milestone:

- **A crashed session keeps its history**, because the record was written when the query ran.
- **`hsql`'s queries appear in the human's History screen**, which is B7 — "as an agent
  joining a task mid-stream, I read the human's recent query history" — working in both
  directions from one file.
- `CatalogCache.history` is removed and `CACHE_VERSION` goes to 3. A **one-time migration**
  runs when the old cache is present and the log holds nothing for that connection: the
  pickled records are appended with their own timestamps and `"program":"harlequin"`. Fifteen
  lines, and it is also the test that proves the writer accepts historical timestamps.

### 3.5 "Copy CLI command", and the module both directions share

`harlequin.invocation` holds what a command line said:

```python
@dataclass(frozen=True)
class Invocation:
    adapter: str | None          # only when it was typed
    conn_str: tuple[str, ...]
    profile: str | None
    config_path: Path | None
    read_only: bool
    adapter_options: Mapping[str, Any]   # typed, not defaulted, not from the profile

    def to_hsql_argv(self, sql: str, *, limit: int | None) -> list[str]: ...
    def to_harlequin_argv(self, open_paths: Sequence[Path]) -> list[str]: ...
```

Both commands build one — `harlequin/cli.py` from its own `explicitly_set` set, `hsql/cli.py`
from its own — and each renders the *other* command's argv. §3.1's handoff is
`to_harlequin_argv()`; the TUI action is `to_hsql_argv()`. One module, two pure functions, no
subprocess and no widgets in the test.

The action, `copy_cli_command`, renders the queries that **would run** —
`_get_selected_queries()`, the same call `Ctrl+Enter` makes, so the copied command is what the
human is looking at — and the Run Query Bar's limit when the box is checked:

```
hsql -P prod -c 'select * from orders where day = current_date' --limit 500
```

Quoting is `shlex.quote` on POSIX and `subprocess.list2cmdline` on Windows. It goes through
`_copy_to_clipboard()`, so it also goes out over OSC 52 and works across ssh. **No format
flag**: `table` is the default and is what the human is looking at; an agent handed the string
adds `--csv` because the skill tells it to (§6.9). A secret in a typed adapter option is
masked rather than copied, with a notification saying so — the same rule as §3.1, for the same
reason, since a clipboard outlives the app too.

Shipped **unbound**: every obvious key is taken, and `harlequin --keys` plus the docs are how
a user picks one. The action is in the registry, so it is bindable and listed the day it
lands.

### 3.6 The external editor (#1102)

The small one, and it can ship first because it needs nothing else in this milestone.

```
capture the buffer  ->  temp file  ->  suspend the app  ->  run the editor  ->
read the file back  ->  one undoable edit  ->  delete the temp file
```

- **Resolution order: config `editor`, then `$VISUAL`, then `$EDITOR`, then refuse.** No
  built-in fallback to `vi` or `notepad`: launching an editor the user never named is the one
  outcome nobody asked for. The refusal names both spellings.
- **`$EDITOR` and `$VISUAL` need no confirmation.** The environment is the user's own shell
  session (Ted's call, and the reason). A config-defined `editor` is a config-defined shell
  command and goes through §3.8 like any other.
- **The exchange is a file, because editors do not read stdin.** A
  `NamedTemporaryFile(suffix=".sql")` written from the active buffer, handed to the editor as
  its last argument — the `git rebase -i` shape that #767 identified and that Posting
  implements.
- **`with app.suspend():`, on the main thread.** Wrapped so `SuspendNotSupported` and `OSError`
  become an error modal rather than a traceback, and behind
  `harlequin.external.run_in_terminal(app, argv)` — one seam, because §1.6 measured that no
  test can enter the real thing.
- **A non-zero exit discards the edit**, with a notification naming the status. That is the
  `git commit`/`:cq` convention, and it is the only escape hatch a user has once the editor is
  open.
- The result is applied by assigning `CodeEditor.text`, which in textual-textarea 0.18.1
  checkpoints undo history and calls `replace()` over the whole document — so the round trip
  is one `Ctrl+Z` away, and the buffer's selection and scroll are the editor's to restore.

The action is `code_editor.edit_externally`, on `CodeEditor`, and #1102's third checkbox —
"post-external-editor action", e.g. format or run on return — is **not** in M4 (§7): the
buffer is back in the editor and the existing keys already do those things.

### 3.7 The command hook (#952)

The other shape: a **filter**, not a takeover.

```toml
[commands.ai]
command = "claude -p 'Rewrite this SQL. Output SQL only, no code fences.'"
description = "Rewrite with Claude"     # what --keys and the footer call it
stdin = "buffer"                        # buffer | selection | none
output = "replace"                      # replace | insert | new-buffer | none
timeout = 120
```

- **It runs on a worker thread** — `@work(thread=True, exclusive=True, group="external_commands",
  exit_on_error=False)` — so the app stays alive, the catalog keeps loading, and a query the
  user already started keeps running. Nothing in it touches a widget: it posts
  `ExternalCommandFinished(name, stdout, stderr, code)` and an `@on` handler applies the
  result, exactly as the query workers do.
- **stdin in, stdout out.** The buffer (or the selection) goes in; stdout replaces the buffer
  as one undoable edit, or is inserted at the cursor, or opens a new buffer. **Empty stdout is
  never applied**: a model that returned nothing must not blank a human's query. Non-zero exit
  → error modal carrying stderr, buffer untouched. stderr on a successful run → a
  notification, because plenty of tools log there.
- **The context is a profile name, not a payload.** The child is handed `HARLEQUIN_PROFILE`,
  `HARLEQUIN_ADAPTER` and `HARLEQUIN_CONN_STR` in its environment, and that is the whole of
  the "and optionally the catalog" the product plan wanted: a hook that needs the schema runs
  `hsql -P "$HARLEQUIN_PROFILE" --catalog` or `--catalog-search`, which is the command M2 built
  and M3 documented, against the same profile, with the same redaction. Marshalling a catalog
  onto stdin would be a second, worse copy of a thing this project already ships — and it
  would pay for a walk the user may not need.
- **`timeout` bounds it** (default 120s): `terminate()`, two seconds of grace, `kill()`. While
  a hook is running the Cancel Query binding stops it too — to a user, "stop" is one idea, and
  a second cancel key for a second kind of work is a worse answer than one key that stops
  whatever is running.
- **No `shell = true`.** A pipeline goes in a script and the script goes in `command`; a shell
  string would mean two quoting rules (POSIX and `cmd.exe`) for a feature whose whole value is
  running something the user already has. `command` takes a list, or a string that `shlex`
  splits.
- **`hsql` runs no hooks, ever.** A headless command that executes shell commands out of a
  config file it found in `$CWD` is a supply-chain hazard in CI. `hsql` reads the table for
  `--config validate` and ignores it otherwise.

### 3.8 Trust: what a config file may make Harlequin execute

Two rules, and neither is a preference the config file can set for itself.

**Rule 1 — provenance.** `[commands]` and a top-level `editor` are honored only from a config
file the user named or keeps: an explicit `--config-path`, the user config dir, or home. A
definition found in `./.harlequin.toml` or `./pyproject.toml` is **ignored**, with one
notification naming the file and the key. Merging already tracks which file supplied each key
(`Provenance`, M2 §3.6), so this is a check at the point of use, not a second pass.

**Rule 2 — consent, per definition.** Before a command runs for the first time, a modal shows
the exact argv, the config file it came from, and three choices — Run once / Always allow /
Cancel — with **Cancel focused**. "Always allow" appends a record to
`<user_state_dir>/harlequin/trusted_commands.json`:

```json
{"name":"ai","source":"/home/ted/.config/harlequin/config.toml",
 "command":"claude -p 'Rewrite this SQL…'","fingerprint":"sha256:…","trusted_at":"2026-09-02T…"}
```

The fingerprint covers the name, the resolved argv and the source path, so **editing the
command re-prompts**. The store is in state, not in config, deliberately: a synced config file
that could carry its own approval would defeat the gate it is supposed to pass. The plaintext
command sits beside the hash so the file can be audited by the person it protects, and revoking
is deleting a line.

**What is not gated:** `$EDITOR`/`$VISUAL` (§3.6), and `hsql --open` launching `harlequin`
(a known entry point in the same interpreter, not a user string).

**What is never automatic:** a hook runs only from a key the user pressed. No hook on start-up,
on connect, on error, or on quit — that is how a config-defined command becomes a startup
payload, and there is no version of it M4 wants.

### 3.9 Actions can come from config, so the registry becomes a function

`HARLEQUIN_ACTIONS` stays as the static base; `build_actions(command_names)` returns it plus
one `command.<name>` entry per configured hook, targeting `CodeEditor` with
`run_command('<name>')` and the hook's `description`. `app.py`, `keys_app.py` and the keymap
loader all take the built registry — `keys_app` already loads config to find keymaps, so it
can list a user's own commands in the binding editor beside the built-in actions.

And the crash in §1.5 gets fixed on the way, because it is now reachable by typo *and* by a
command that was renamed: an unknown action name is **skipped with one notification naming the
keymap and the action**, not a `KeyError` in mount. A keymap with one bad line should cost the
user that line, not the application.

---

## 4. Sequencing

Two releases. **The handoff first**: it is additive, needs no new trust machinery, and every
piece of it is a pure function plus a file. **The hooks second**, in dependency order, with
the trust gate landing with its first consumer rather than after it.

Numbering assumes M3's remaining work releases as 2.13.

### Release A — the handoff (2.13)

**PR 1 — `harlequin.invocation`, and `harlequin --open`.** The dataclass, both argv renderers,
and the IDE flag that loads files into buffers without executing them. `cli.py` builds an
`Invocation` and hands it to the app; the IDE starts calling `hide_secrets_in()`. Closes the
half of F1 the IDE owns, and is useful on its own (`harlequin --open report.sql`).

**PR 2 — `hsql --open`.** The mode option, the secret refusal, the scratch file and its
pruning, the exec/subprocess split, and the `/dev/tty` reattach. Rewrites the skill's section
9 (`hsql --skill` ships from the wheel, so this is the same PR). Closes F1.

**PR 3 — `harlequin.query_log`.** The record, the writer, the tolerant reader, rotation,
`get_connection_hash()`'s move out of `catalog_cache`, `history = false`, and both commands
writing. Guard: `hsql`'s import set still contains no rich, and `hsql --version` is still
~120ms.

**PR 4 — `hsql --history`.** The mode, the bounded tail, the connection filter, the folded
`sql` column, and `--limit` as "the most recent N". Nothing new in the output layer — the
listing goes through the emitters `--catalog` already uses. Adds the mode to the skill, beside
the hand-off paragraph PR 2 rewrote. Closes B7's headless half.

**PR 5 — The History screen reads the log.** `History.tail()`, `CatalogCache.history` removed,
`CACHE_VERSION` to 3, and the one-time migration. This is the PR where an agent's queries
first show up in a human's History screen, and where the human's show up under `--history`.

**PR 6 — Copy CLI command.** The action, the quoting, the masking, the registry entry. Closes
F2.

**PR 7 — Docs** (`harlequin-web`): the handoff section in "Headless & Agents", `--open` on both
command pages, and the query log — the mode that reads it, its shape, and the path, for the
reader who wants to back it up or delete it.

### Release B — the hooks (2.14)

**PR 8 — The external editor.** `$EDITOR`/`$VISUAL` only, `harlequin.external.run_in_terminal()`,
the suspend wrapper and its unsupported-environment path, temp-file exchange, non-zero-exit
discard, and the `code_editor.edit_externally` action. Closes
[#1102](https://github.com/tconbeer/harlequin/issues/1102) for the request as filed.

**PR 9 — The trust gate.** Provenance rule, consent modal, trust store, and the config
`editor` key as its first consumer. No hooks yet — the gate ships with something small enough
to review it against.

**PR 10 — `[commands]` in config, and actions from config.** The `Config` member, the schema
regeneration (`scripts/write_config_schema.py`, and the pinned artifact), `build_actions()`,
the keymap-validation fix from §1.5, and `--config validate`'s coverage of the new table.
Nothing executes yet; the keys bind to an action that reports "not implemented" in exactly one
commit, or PR 10 and PR 11 land together if that reads badly in review.

**PR 11 — Running a hook.** The worker, stdin/stdout, the four `output` modes, the empty-output
rule, timeout and cancel, error surfacing. Answers
[#952](https://github.com/tconbeer/harlequin/issues/952).

**PR 12 — Docs** (`harlequin-web`): "External editor" and "Bring your own AI" pages, the trust
model stated plainly, and a worked `claude -p` config with the prompt that keeps code fences
out of the buffer.

**Ordering rationale.** Same as M1 and M2: a contract or a gate lands with the first consumer
that needs it. Across releases, the features that only add a file or an argument go before the
ones that execute a user's string.

---

## 5. Testing

**Unit, and most of the milestone is unit-testable.**

- `Invocation.to_hsql_argv()` / `to_harlequin_argv()`: profile vs conn_str, typed vs defaulted
  options, the secret refusal, and quoting on both platforms (parametrized over
  `shlex.quote`/`list2cmdline` rather than skipped on one).
- `query_log`: record round trip; a malformed line between two good ones; a truncated last
  line (the crash case); the tail reader's connection filter and its 500-record bound;
  rotation; `redact_text()` applied to `sql`; an unwritable directory disabling logging without
  raising.
- The **concurrent append** measurement becomes a real test — 4 processes × 100 records at
  8 KB, asserting every line parses — marked so it can be deselected on a slow runner.
- The pickle migration, including timestamps that predate the log.
- `--history`: the connection filter with two connections interleaved in one file; `--limit`
  taking from the newest end; the folded `sql` column matching between `--format table` and
  `--format csv`, asserted the way the golden-format snapshots assert it rather than against a
  copy of the folding rule; and an empty log printing a header and no rows rather than nothing.
- A guard that `--history` opens no connection: the mode runs against a profile whose adapter
  would raise on `connect()`, and still prints rows.
- Trust: fingerprint stability across a whitespace change (it should change — the argv is what
  runs), and the provenance rule (a `[commands]` table in `./pyproject.toml` is ignored; the
  same table under `--config-path` is not).
- `build_actions()`: a command name that collides with a built-in action, and a keymap naming
  an action nothing defines.

**Functional (pilot + snapshot).**

- `harlequin --open a.sql b.sql`: two new buffers, the second active, **nothing executed** —
  assert on the absence of a `QuerySubmitted`, not only on the snapshot.
- The copy action: assert the string, through the editor's clipboard rather than the system's.
- The hook worker against a **hermetic child** — `[sys.executable, "-c", …]`, never an external
  binary — covering success, empty stdout, non-zero exit with stderr, and a timeout that kills
  a child that ignores `terminate()`.
- The consent modal: cancel leaves the buffer alone and runs nothing; "always allow" writes one
  record and the second invocation does not prompt.
- The editor round trip with `run_in_terminal` patched, plus the `SuspendNotSupported` path
  asserted as a notification — §1.6 measured that the real suspend cannot be reached in
  `run_test()`, so the seam is the test surface.

**Import hygiene.** `harlequin.invocation` and `harlequin.query_log` join `HEADLESS_IMPORTS`.
A run that logs must not import rich: that is the assertion that keeps §1.3's finding from
coming back as a 46ms regression on every `hsql -c`.

**Not tested, and said out loud:** no test runs a real editor or a real model; nothing asserts
on `/dev/tty` reattachment (CI has no controlling terminal) — the decision function is tested,
the two `dup2` calls are not.

---

## 6. Decisions

### 6.1 The external editor and the AI hook are two features

The product plan folds them together — "same shape as the existing external-editor request" —
and the shape is where they differ:

| | External editor | Command hook |
| --- | --- | --- |
| The terminal | needs it; the app suspends | must not have it; the app stays live |
| Thread | main, blocking (suspend redirects the process's streams) | worker |
| Exchange | a temp file, edited in place | stdin → stdout |
| Duration | as long as the human takes | seconds, and needs a bound |
| Cancel | quitting the editor | a timeout, and a key |
| Failure | non-zero exit means "discard" | non-zero exit is an error to show |
| Config | one key; `$EDITOR` is usually all of it | a named command with a prompt, per task |
| Trust | the environment is the user | a file is not |
| Privacy | the query stays on the machine | the query may leave it |

What they share is four lines: build an argv, start a process, read a result, apply it as one
undoable edit. Unifying them means a single config table with an `interactive` switch, a
`{file}` placeholder whose presence silently changes the exchange, and a validity table where
most combinations are refused — a worse config than two tables, to save four lines. They also
close two different issues, for two different users, and the editor can ship a release
earlier because it needs no trust store.

### 6.2 `hsql --open`, not `hsql open`

M2's rule, unchanged: `CONN_STR` is positional, so a subcommand named `open` would need a rule
to distinguish it from a DuckDB file named `open`. `--open` needs none, and it composes with
`-c`, `-f` and stdin, which a subcommand taking its own path argument would have duplicated.

### 6.3 The handoff execs; it does not import, and it does not write the buffer cache

Measured in §1.1: the cache is Textual (+164ms and 341 modules) and is rewritten whole at
quit, so a second writer races the IDE. An in-process import of `harlequin.cli` would break
the one contract `hsql` is defined by. `python -m harlequin` in the same interpreter is the
only option that is both correct and cheap, and it is cheap only relative to what it launches
— a 620ms `harlequin --version` is the floor for anything that opens the IDE.

### 6.4 One log file, not one per connection

A per-connection file would make the History screen's read trivial and everything else worse:
an agent asking "what has been run against this warehouse today" would have to know the
connection hash to find the file, and `tail -f` on "what is happening" would have no target.
One file with a `connection` field is `grep`-able, `jq`-able, and orderable by time across
databases, which is what "readable with ordinary tools" means.

### 6.5 `--history` is a mode, and the log's path is not in `--info`

The tempting cut is to ship the log and let `tail` and `jq` be the reader: the format is
already the most readable one there is. It is a mode (Ted's call), and three things say so.

**A mode is nearly free, because a listing is a result set.** M2 §3.3 built that road for
`--catalog`: hand the emitters an Arrow table and every format, every layout flag and `-o`
follow. `--history` is a reader plus a table; it is not a second output stack.

**The pipeline is not portable and not self-describing.** It needs `jq`, a path, a filter by
connection, and a shell that has `tail` — which Windows does not. "Read the human's recent
queries" is a thing the skill should be able to teach in one line that works everywhere.

**And `--info` is the wrong place for the path.** `--info` reports on the installation —
versions, platform, config files, adapter capabilities. A pointer into the user's own query
history is a different kind of fact, and putting it there invites a reader to go parse the file
by hand when a mode can hand them rows. The path stays in the docs, where a person who wants to
back the file up or delete it will look.

### 6.6 The hook's context is a profile name, not a catalog

Harlequin already ships the best catalog-for-agents interface it has (`--catalog`,
`--catalog-search`, `--info`), documented in M3 and taught by the skill. A hook that gets
`HARLEQUIN_PROFILE` can call it; a hook that gets 40 KB of marshalled catalog on stdin pays
for a walk it may not need and gets a second, worse format for the same data.

### 6.7 The trust store is not the config file

If "always allow" wrote back into the config, a synced config could ship its own approval and
the gate would protect nothing. State dir, fingerprinted by argv and source, plaintext beside
the hash so the person it protects can audit it.

### 6.8 `$EDITOR` is trusted; a config file is not

Ted's call, and the line generalizes: the environment is the user's own session, and a config
file is a document that arrives from a directory (`$CWD`) or a sync (dotfiles) that the user
may not have read. That is also why the provenance rule (§3.8) is separate from the consent
prompt — one keeps a stranger's file from ever asking, the other keeps the user's own file
honest.

### 6.9 The copied command carries no `--csv`

The product plan's example is `hsql -P prod -c '…' --csv`. The human copying it is looking at
a table, and the agent receiving it knows from the skill to pick a format on purpose (§5 of
`SKILL.md`). A format flag in the copied string is a guess about the recipient; the profile,
the connection and the SQL are not.

---

## 7. Explicitly not in M4

- **`hsql mcp`** — M6, unchanged.
- **Streaming, `--offset`, and the memory work in #875** — M5.
- **A post-editor action** (#1102's third checkbox: format or run on return). The buffer is
  back in the editor and `code_editor.format` and `code_editor.run_query` are already bound.
- **Hooks anywhere but the Query Editor.** A hook over a *result set* — "explain this table" —
  is a real idea and a different feature: it needs a serialization of the result, a size
  policy, and an answer for where the output goes.
- **Code-fence stripping on hook output.** The prompt is the config author's, and the docs give
  one that works. A heuristic here would be Harlequin guessing at model output.
- **`shell = true`, and placeholders beyond none.** §3.7.
- **Action-name validation in `hsql --config validate`.** It would be genuinely useful — an
  agent editing a keymap would learn about a typo without starting a TUI — but the registry
  lives in `harlequin.actions`, which imports `harlequin.components` and therefore Textual.
  Doing it properly means splitting the *names* into a leaf module and leaving the widget
  targets behind, which is a refactor to make on purpose, with the second consumer in hand.
- **A `--open` that starts a query running.** It would collapse the review step F1 exists for.
- **`--history --since 2h`, and searching history.** Both are obvious the moment the mode
  exists, and both are additions to a mode and a format that already work — `--limit` and the
  reader's newest-first order cover the case the milestone is for. Worth doing next, not now.
- **Per-hook cancel keys, hook chaining, and hooks that write config.** Each is a feature
  request that will read as obvious once one hook works, and none of them is M4's.

---

## 8. Corrections to the product plan

Recorded here; applied to `harlequin-for-agents.md` in the same PR as this document.

- **§11's M4 row still lists the skill**, which shipped in M3 (`hsql --skill`, the packaged
  `SKILL.md`, and the marketplace entry). The row is `hsql --open`, the query log, "Copy CLI
  command", the external editor, and command hooks.
- **§10's `hsql open query.sql` is a subcommand, and M2 settled on mode options.** The spelling
  is `hsql --open`, reading its SQL from `-c`, `-f` or stdin like every other run (§6.2).
- **§10's "buffers already persist through `editor_cache.py`, so the machinery is close at
  hand" is measurably wrong in the direction that matters.** That cache imports Textual and is
  rewritten whole at quit; it is the one mechanism the handoff cannot use (§1.1).
- **§10 treats the external editor and the AI hook as one feature.** They differ in the
  terminal, the thread, the exchange, the failure mode and the trust model, and #1102 now
  exists as its own issue with its own reference implementation (§6.1).
- **§10 does not mention consent, and a config-defined shell command needs it.** Config files
  merge from `$CWD`, so a cloned repository can define one; a synced dotfiles repo can do the
  same in the user's own config dir. §3.8 is the missing paragraph.
- **§6's `hsql history --json -n 20` is the right feature under two wrong assumptions.** The
  spelling is `hsql --history --json --limit 20` — a mode option, and `--limit` rather than a
  new `-n`, because both commands already mean "the rows that leave the store" by it. And what
  it reads is not the pickle §6 proposes to "expose": that one is written once, at quit, behind
  a module headless code may not import (§1.2, §1.3). The log is the store; the mode is the
  reader (§3.3).
- **§12's risk list says query history "sits outside that mechanism" and needs its own
  handling.** It needs one line of handling: `redact_text()` on the way into the log, plus the
  `hide_secrets_in()` call the IDE has never made (§1.4, §3.2).
- **§9's skill section 9 is rewritten by this milestone**, as M3 §7 predicted: the handoff
  advice becomes `hsql --open -f draft.sql -P prod` rather than "tell them to run `harlequin`".
- **#952 is closed as `not planned`**, with "I won't be adding this to Harlequin. But shortly I
  am releasing `hsql`." §10's "#952 answered without embedding an LLM" is still the right
  answer; the changelog entry should reference #952 for the hook and #1102 for the editor,
  and #952 can be reopened or left closed with a comment pointing at the hook.
- **§13's success criteria should gain one line**: a query run by `hsql` appears in the human's
  History screen, and a draft written by an agent opens in the human's editor without being
  run. That is what Workstream F is for, and neither is measurable today.
