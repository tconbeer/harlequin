# Warm sessions for `hsql` — a design for `hsql --serve`

A design for a long-lived `hsql` process that holds an open connection, so that repeated
invocations pay neither the import cost nor the connection cost again.

**Revised against 2.13.0**, with [M1](./m1-hsql-technical-plan.md),
[M2](./m2-hsql-technical-plan.md) and [M3](./m3-hsql-technical-plan.md) shipped and SSH
tunnels landed; the first draft was written against 2.9.0, when M2 was still a plan. Every
number below is re-measured on this checkout — Python 3.10.15, Linux, four adapters
installed — on a machine about a third slower than the one the first draft used, so read
the ratios rather than the absolutes. §10 lists what the revision changed, for a reader
who has the first draft in their head. PR 1 has shipped; §9 says what is in it.

**Bottom line up front.** The idea works, and the win is bigger than the quarter-second it
was proposed to recover — but only if one constraint is treated as the load-bearing one:
**the client must not import anything Harlequin owns.** The client built to that rule and
shipped in PR 1 answers `select 1` in **27ms against 348ms cold**, and 20 sequential
invocations in **0.55s against 7.5s**. A client that imported `click` and nothing else
would already be near 65ms, and one that imported `harlequin.config` near 90ms (§2) — so
the difference between a good version of this feature and a mediocre one is decided by the
first few lines of the console script, not by the protocol.

The other finding is that speed is the *smaller* half. A warm process is a **session**:
temp tables, `SET`, `search_path`, and open transactions survive between invocations. That
is a genuine feature for an agent — and it is also the reason this cannot be turned on by
default, cannot be auto-started in v1, and cannot be a silent fallback. §5 is the part of
this document I would argue about; §1–§2 are mostly arithmetic.

Recommended shape, in one line:

```bash
hsql --serve prod ./warehouse.db      # foreground, holds the connection
HSQL_SESSION=prod hsql -c "select 1"  # 27ms, same bytes, same exit code
```

---

## 1. Where the 350ms actually goes

`hsql -c "select 1"` against in-memory DuckDB, best of twenty runs of the real console
script:

| Invocation | Best | Median |
| --- | --- | --- |
| `hsql -c "select 1"` | 348ms | 362ms |
| `hsql -c "select 1" -a sqlite` | 323ms | 336ms |
| `hsql --help` | 130ms | 134ms |
| `harlequin --version` | 895ms | 917ms |

Instrumenting the same work phase by phase, cumulative from process spawn:

| Phase | Cumulative | Cost |
| --- | --- | --- |
| interpreter ready | 17ms | 17ms |
| `import click` | 53ms | 36ms |
| `harlequin.config` | 87ms | 34ms |
| `harlequin.plugins` | 93ms | 6ms |
| `load_adapter("duckdb")` | 163ms | 70ms |
| `harlequin.query` (pyarrow + `textual_fastdatatable.backend`) | 266ms | 103ms |
| `adapter()` + `connect()` | 279ms | 13ms |
| the rest: `harlequin.statements`, `harlequin.layout`, and the query | 329ms | 50ms |

That last row is mostly two more imports — tree-sitter for the splitter and wcwidth for
the layout. Measured in a process that has already paid for all of them, `connect()` is
10.8ms and **`execute()` + `fetch()` + layout together are 5.0ms.**

**So the database work is 5ms of a 350ms invocation.** Everything else is getting ready to
do it. M1 already took the cheap wins — lazy entry points, the lazy
`textual-fastdatatable` `__init__`, deferred `harlequin.query` — and what is left is
genuinely irreducible on the cold path:

- **pyarrow, 103ms with the backend.** `harlequin.query` normalizes every result set
  through `create_backend()`, which is the whole reason both front ends agree about what a
  row is. Removing it means a second normalizer, which the M1 plan rejected for good
  reasons that have not changed.
- **The driver, 70ms for duckdb.** Not ours.
- **click, 36ms.** Ours to remove in principle, at the cost of hand-rolling the option
  surface that adapters plug into. Not worth it.
- **CPython itself, 17ms.** The floor for any Python process on this machine.

So: even a version of `hsql` that imported *nothing* of Harlequin's beyond click and its
config would land near 200ms, and one that also dropped click near 165ms. **There is no
sequence of import fixes that reaches 50ms.** That is the honest case for a second
process, and it is worth stating in the plan because the alternative — "keep optimizing" —
has been the right answer up to now and stops being the right answer here.

### 1.1 And `connect()` is 11ms only because DuckDB is a library

The table above understates the win, because the adapter it measures opens a file. The
in-tree number is `connect()` at 10.8ms for in-memory DuckDB, and a local file or SQLite
is the same order. Every adapter that speaks to a *server* — Postgres, MySQL, BigQuery, Trino,
Databricks, Snowflake — pays TCP setup, a TLS handshake, and an auth round trip on each
`connect()`, and for the OAuth-shaped ones a token exchange on top. Those are the
connections a human hands an agent, and they are the ones where holding the connection open
matters more than holding the imports.

This design should therefore not be sold on "350ms → 27ms against DuckDB." It should be
sold on **the invocation cost stops depending on how far away the database is**, with the
DuckDB number as the floor case rather than the typical one.

## 2. What a warm session costs instead — measured, not projected

The first draft measured a throwaway prototype. These are the **shipped** client from PR 1
— the real `main()`, the real `harlequin.hsql.client`, the real frames — against a stub
server that speaks `protocol.py` and returns fixed bytes without touching a database, so
the number is the invocation's cost rather than a query's. Same machine, twenty runs:

| | Best | Median |
| --- | --- | --- |
| warm round trip, `select 1` | **26.6ms** | 27.6ms |
| cold `hsql -c "select 1"` | 348.2ms | 361.8ms |
| **20 sequential invocations, warm** | **547ms** | — |
| **20 sequential invocations, cold** | **7520ms** | — |

The warm client's 27ms decomposes almost entirely into things that are not the feature:

| | Cumulative | Modules |
| --- | --- | --- |
| CPython boot (`python -c pass`) | 16.8ms | 33 |
| `import harlequin.hsql` (the entry point) | 17.9ms | 36 |
| `import harlequin.hsql.session` (the decision) | 18.0ms | 37 |
| `import harlequin.hsql.client` (socket, struct, protocol) | 26.1ms | 58 |
| connect, send, receive, write, exit | ~1ms | — |

**The round trip is about a millisecond.** The client is ~95% interpreter start-up, which
means the protocol can afford to be careful — framing, a handshake, a version check, a
capability exchange — without moving the number. It also means the ceiling is 17ms unless
the client stops being Python, which §7 leaves as future work rather than pretending it is
free.

**25 modules over a bare interpreter is the whole of it**, and that is why §6's guard
counts modules rather than milliseconds: a count is the same on every machine, where a
wall clock on a shared CI runner is not. For scale, on the same interpreter `import click`
is 100 modules, `harlequin.config` 141 and `harlequin.hsql.diagnostics` 149.

### 2.1 The constraint this puts on the entry point

`import click` costs 36ms and `import json` costs 9ms. Against a 27ms invocation those are
133% and 33%. So the rule is not "the client should be lean," it is:

> **`harlequin.hsql.main()` must decide whether this invocation belongs to a session, and
> hand off to a stdlib-only module, before it imports anything that is not stdlib.**

That is the single most important line-level decision in this document, and the first
draft under-scoped it. Three things were on the entry point's path, not one, and PR 1 took
all three off:

- **click, 36ms.** `main()` opened with `import click`. It now looks at `HSQL_SESSION` and
  `--session` first — a `sys.argv` scan and an `os.environ` lookup, both free — and only
  falls through to `build_cli()` when there is no session.
- **`harlequin.hsql.diagnostics`, 68ms.** The bigger one, and invisible until measured:
  `harlequin/hsql/__init__.py` imported `ExitCode` from it at module scope, and that
  module reaches `harlequin.redact` → `harlequin.config` → msgspec. Deferred into the cold
  path, where it was already being imported anyway.
- **`typing`, 10ms.** `harlequin/__init__.py` imported it, and importing *any*
  `harlequin.*` submodule executes that file. Every annotation there is a string under PEP
  563, so the module now declares `TYPE_CHECKING = False` itself and imports nothing at
  all. The four modules on the client's path do the same.

A consequence worth stating, because it looks like a violation of a rule this repo has:
**the client writes its own stderr rather than going through `diagnostics._write()`**,
which is otherwise "the one line every message leaves through". Nothing is lost. That
function exists to redact, and the client holds no secret to redact — it reads no profile,
loads no adapter, and never sees a connection string. The server's stderr arrives already
redacted and is copied through untouched.

It also gives the guard a shape CI can hold, exactly as the "no Textual in `hsql`" rule
does: **an import-linter contract saying the client's three modules reach nothing of
Harlequin's and nothing third-party**, plus a run-time test that counts modules against a
bare interpreter. One caveat the first draft missed: import-linter cannot express "this
package's `__init__` may not import click at module scope" — a contract's source module
matches its descendants — so *that* half is a run-time test only
(`test_deciding_whether_an_invocation_is_warm_does_not_import_click`). Without both, the
first contributor who needs a config value in the client re-adds 68ms and nobody
notices.

## 3. Why a daemon, rather than the four cheaper things

### 3.1 Not "keep optimizing imports"

§1 measured the floor at ~165–200ms. Rejected on arithmetic.

### 3.2 Not a batch mode (`hsql --repl`, statements on stdin)

One process, many statements, no daemon, no socket, no security model. It is strictly
simpler and it is the right tool for a migration script.

It does not serve the caller this feature is for. An agent working through a Bash tool gets
**one command per turn** and cannot hold a pipe open across turns; a shell script that
wants to interleave `hsql` with `jq` and `git` cannot either. Batch mode solves "many
statements known up front." The daemon solves "many invocations, each decided after seeing
the last one's output" — which is what an agent loop is.

Worth noting they compose: `hsql --serve` is a batch mode whose batch arrives over a
socket.

### 3.3 Not MCP instead — but this is the closest call

M6 in the product plan is `hsql mcp`: a long-lived process an agent talks to over stdio,
running on the same M1 execution core — still the last milestone, with M4 (handoff, in
three releases) and M5 (streaming) ahead of it. **It is already a warm session.** It holds a
connection, it pays the imports once, and it needs no socket, no file permissions, no
daemon lifecycle, and no uid check, because the agent harness owns the process and the pipe
is the security boundary. If the only persona were "an agent whose harness speaks MCP,"
this document should be closed and M6 pulled forward.

Two reasons it is not:

1. **Most agent harnesses give you Bash, not MCP** — and even the ones that speak MCP run
   in contexts (CI, a container's entrypoint, a Makefile, a `postCreateCommand`) where the
   available interface is a command line. The product plan's own P1 persona is defined as
   "usually inside a coding agent's Bash tool."
2. **The audience is not only agents.** A human running twenty `hsql` calls in a shell loop
   against Snowflake gets the same win, and MCP does nothing for them.

The right relationship is that **`hsql mcp` should be a client of this server, not a
parallel implementation of it.** Both are "a warm connection plus a request loop"; the only
difference is the transport and the serialization. If M6 grows its own connection lifecycle
the plan has two of them, which is the same mistake the plan already refuses to make with
the TUI and the CLI. That is an argument for building the session layer *first* and letting
MCP land on top — and it is the strongest argument in this document for doing this at all.

### 3.4 Not a connection pool

A pool would let several clients run at once. It also destroys the thing that makes a
session useful — a temp table created on connection 3 is invisible to the next request that
lands on connection 1 — and it turns "which connection am I on" into a question the caller
has to think about. One session is one connection. §4.5 handles concurrency by serializing
rather than by multiplying connections, and §7 leaves read-only pools as future work.

## 4. The design

### 4.1 One command, two roles

`hsql --serve NAME [CONN_STR] [connection options]` runs the server **in the foreground**.
Not a `start`/`stop`/`status` subcommand family, and not a self-daemonizing fork:

- Foreground is what every supervisor already knows how to run — `&`, `systemd`, a
  container entrypoint, `docker compose`, a `SessionStart` hook, `tmux`. Self-daemonizing
  processes reimplement all of that badly, and orphaned ones holding a live warehouse
  credential is exactly the failure this feature must not have.
- It keeps the mode-option shape M2 already settled on (`--catalog`, `--info`, `--spec`,
  `--config MODE`) rather than introducing the subcommand ambiguity that shape exists to
  avoid: `hsql` takes `CONN_STR` positionally, so `hsql server foo.db` would be ambiguous
  in the same way `hsql catalog` was.
- `--serve` takes the session name as its value, so one flag carries both "be a server"
  and "be *this* server."

#### Why `--serve` and `--session`, and not two nouns

The first draft of this document paired `--server NAME` with `--session NAME`, and that is
the wrong pair: in database vocabulary "server" and "session" are near-synonyms, and
neither word tells a reader which of the two processes it belongs to. The client half is
effectively pinned — `HSQL_SESSION` is the ambient spelling and nobody wants to write
`HSQL_SERVER` for a thing that is not one — so the question is only what the server's flag
is called, and the answer should be a **verb**:

> **`--serve` is a mode option; `--session` is not.** M2's grammar is that mode options say
> what an invocation *is* (`--catalog`, `--info`, `--spec`, `--config MODE`) and plain
> options modify a normal invocation. `hsql --session prod -c "select 1"` *is* an ordinary
> query invocation — it just runs somewhere warm — so `--session` is a plain option, and
> `--serve` joins the modes. The grammar already encodes the asymmetry the names need.

Considered and not taken:

| Spelling | Why not |
| --- | --- |
| **`hsqld NAME CONN_STR`** + `hsql --session NAME` | The clearest of all, on forty years of `sshd`/`dockerd`, and it frees the daemon's own knobs from any prefix. Costs a third console script to package and document, and worse discoverability than `hsql` — which the product plan already lists as a risk. **The runner-up**; take it if the daemon's flag surface ever outgrows one command. |
| `--listen NAME` | Accurate about the mechanism, unambiguous about direction, but reads network-y: someone will look for the port. Wrong prior for a design that refuses TCP on purpose (§4.7). |
| `--daemon NAME` | Unambiguous and false. This process runs in the foreground; a `--daemon` flag that does not daemonize is a lie the user finds out about when they try to background it. |
| `--host-session NAME` | "Host a session" reads well, but `-h/--host` is a connection option on the postgres, mysql and trino adapters — and **hsql's own flags win collisions** (`first_pass.attach_adapter_options`, under the spellings hsql reserves), so this would quietly take `--host` away from them. Every new flag in this design needs that check. Done, against duckdb, sqlite, postgres and mysql: none of `--serve`, `--session`, `--idle-timeout`, `--max-lifetime` or `--queue-timeout` collides with anything any of them declares. |
| `--attach NAME` | tmux/docker vocabulary, and every agent knows it — but `ATTACH` is a DuckDB statement for attaching a database. Collides with the subject matter, in a tool whose subject matter is databases. |
| `--connect NAME` | Overloaded with the connection every other part of this CLI is about. |
| No server flag: infer the role from argument shape (a CONN_STR and no SQL means serve) | This is principle 7's "never make the agent guess," inverted. |

One consequence of the rename, applied through the rest of this document: the daemon's own
knobs lose the `--server-` prefix they only carried to disambiguate — `--idle-timeout`,
`--max-lifetime`, `--queue-timeout` — since they appear only next to `--serve`. And the two
that are *client* operations are named for what the caller is addressing:
**`--session-status`** and **`--session-reset`**, both of which are things you ask a running
session, not things you configure a server with.

#### What `--serve` accepts, and what it refuses

**No, `--serve` does not accept `-c`** — and the reason generalizes into the rule that
defines the two roles. Every flag `hsql` has falls into exactly one of three groups, decided
by *when the question it answers can possibly be answered*:

| Group | Answered | Flags | `--serve` | client |
| --- | --- | --- | --- | --- |
| **connection-time** | once, when the server connects | `CONN_STR`, `-a`, `-P`, `--config-path`, `-r`/`--read-only`, every adapter option, and the five SSH options (`--ssh-host`, `--ssh-forward`, `--ssh-batch-mode`, `--ssh-allow-reuse`, `--ssh-timeout`) | **accepted** | accepted, but rejected if it differs from the server's (§4.4) |
| **per-request** | on every invocation | `-c`, `-f`, `-o`, `--format` and the shorthand flags, `-t`, `-A`, `-x`, `--no-header`, `--no-footer`, `--null-string`, `--limit`, `--display-rows`, `--result`, `--on-error`, `--stats`, `--color`, `--timeout`, and every mode: `--catalog`, `--catalog-search`, `--path`, `--config`, `--info`, `--spec`, `--skill` | **refused, exit 2** | accepted |
| **server-lifetime** | once, and only a server has one | `--idle-timeout`, `--max-lifetime`, `--queue-timeout` | **accepted** | refused, exit 2 |

The partition is exact and it is symmetric: **each side refuses the other's group.** That
turns §4.4's differing-connection-options rejection from a one-off into a special case of a
single rule, and it gives both refusals an error that names the right invocation rather than
just the wrong one — principle 7, applied to the feature's most likely first mistake:

```
hsql: -c is a per-request option, and --serve takes none.
      Start the session, then send it a query:
        hsql --serve prod ./warehouse.db
        hsql --session prod -c "select 1"
```

**The one real argument for `-c` on `--serve` is an init script** — `SET search_path`,
`CREATE TEMP TABLE`, `INSTALL`/`LOAD` — run once at connect time, which is a genuinely
useful thing for a session to do. It is already served, by the better mechanism:
`--init-path` (`-i`) is an *adapter* option on both in-tree adapters, so it is
connection-time, group 1, and comes along for free. It is also the more correct of the two,
because it runs inside `connect()`, where the adapter decides what "init" means for its
database and where a failure is a *connection* failure rather than a request failure. A
`-c` on `--serve` would be a second, worse spelling of something that works today.

#### Adapter options are allowed on `--serve`, and should still be discouraged

They have to be accepted: the server is the only process that connects, so refusing them
would mean the only way to serve anything but a local file is a profile.

But `--serve` changes their risk profile, and the docs should say so. **A server's command
line is long-lived and visible in `ps`.** A password on a one-shot invocation is exposed for
350ms; the same password on an eight-hour session is exposed for eight hours, to every
process on the box. So:

- **`-P` is the spelling the docs should show.** A profile is already "how to connect to my
  warehouse," and `hsql --serve prod -P prod` should be the normal form with the flag-soup
  version as the exception.
- **Warn when a secret-typed option is passed literally to `--serve`**, detected through
  the declarative `secret=` that shipped with #667 rather than a hand-written list of flag
  names. A warning rather than a refusal in v1, because refusing needs env interpolation
  (#898) to have shipped first so that there is a documented alternative for the container
  case. Tightening it to a refusal once #898 lands is a reasonable later call, and a cheap
  one.

#### The SSH options are connection-time, and a session is where they pay off

They landed after the first draft, and they fit the partition without an exception: a
tunnel is opened once, on the way to `connect()`, so `--ssh-host` and its four companions
are group 1. This is the case a warm session is *most* worth having — a tunnel costs an
ssh handshake on every cold invocation, and holding it open is the larger half of the win
§1.1 describes.

Two consequences the server has to carry rather than invent:

- **The tunnel is part of what the server holds open**, so §4.7's "the server is a
  credential" reads on it too, and `--max-lifetime` bounds it.
- **`--ssh-allow-reuse` is already a `CLI_ONLY_SSH_KEYS` key** — a config file may not turn
  off the check that the local port is not already someone else's listener, because config
  files are discovered in the working directory. That reasoning does not weaken on a
  server; it is the same flag, read the same way.

Clients opt in two ways, and the distinction matters:

| | Spelling | Missing server → |
| --- | --- | --- |
| **ambient** | `HSQL_SESSION=prod` in the environment | warn on stderr, run cold |
| **explicit** | `hsql --session prod -c "..."` | fail, exit 3 (connection) |

A name that could never name a session — one with a `/` or a space in it, which §4.4's
validation refuses — is the same shape of answer one step earlier: exit **2**, because a
bad name is a bad argument rather than a server that is down. Native Windows is the same
again: explicit exits 2 (it can never work here), ambient warns and runs cold.

An environment variable is a preference — the human or the harness set it once, and an
`hsql` call that still works when the server is down is the behavior that does not break a
script. A typed `--session` is an assertion, and silently running cold against an assertion
would mean silently losing the temp table the caller was counting on. Same reasoning as
`merge_profile_with_cli()`: **a value the caller actually typed carries intent that a
default does not.**

The fallback warning is not optional and not suppressible. "It got slow and
my temp tables vanished" must never be something the caller has to guess at.

### 4.2 The client is stdlib-only, and forwards argv verbatim

```
harlequin/hsql/
  __init__.py     main(): session check, then click or the client. Stdlib only.
  session.py      which session an invocation names, and where its socket is.
                  Stdlib only, and read before anything else.
  client.py       stdlib only: socket, struct, sys, os. No click, no json, no
                  harlequin beyond session.py and protocol.py.
  protocol.py     framing + the frame vocabulary, shared -- but written so that
                  client.py's half needs no imports the client cannot afford.
  server.py       accept loop, one worker, lifecycle, the uid check.
  cli.py          unchanged, plus --serve / --session
```

The first three of those shipped in PR 1. `session.py` is separate from `client.py`
because the decision has to be cheaper than the client: a cold invocation reads it, finds
no session, and pays two modules and no socket for the question.

**The server parses the flags; the client does not.** The client scans `sys.argv` for the
three things it cannot avoid knowing about —

- `--session NAME` (strip it; it is the client's own flag),
- `-f -` / `--file -` (read stdin and send the bytes, because the server has no stdin),
- nothing else —

and forwards the rest opaquely. Every other flag, including `--help`, including a
misspelling, is parsed by the server, by the same `build_cli()` the cold path uses, and
produces the same diagnostic. There is no second parser to keep in step, which is the
failure mode this design would otherwise be most likely to have. `hsql --session prod
--badflag` should print the same message and exit the same 2 as `hsql --badflag`, and it
will, because it *is* the same code.

The request carries what the server cannot know: **argv, the client's cwd, stdin bytes if
asked for, `NO_COLOR`, and whether the caller's stdout and stderr are terminals**
(the two halves of what `--color auto` reads: it checks `sys.stdout.isatty()` and
`NO_COLOR`, and on the server every stream is a buffer, so `auto` would otherwise mean
"never" however the caller's terminal looks). `TERM` is *not* carried — nothing in the
output path reads it. Deliberately *not* the
client's whole environment — the server has its own, an adapter option's `envvar` resolves
against the server's, and shipping the caller's environment across a socket is a credential
leak with no upside.

The cwd is doing more work there than it looks. **Config discovery is cwd-dependent** — home,
then the user config dir, then the cwd, later winning — so the server must resolve a
client's `-P` and `--config-path` against *the client's* directory, not its own. Otherwise
`hsql --session prod -P local` run in a project would silently read the server's
`pyproject.toml`, and a caller would get a profile they did not write. This costs nothing —
the cwd is already in the request for `-o` (§4.3) — but it has to be deliberate, because the
natural implementation resolves against the process that is running. A profile is then
subject to the same partition as typed flags: its connection-time keys go through §4.4's
comparison, its per-request keys apply normally, and `-P` needs no special case on either
side.

### 4.3 What crosses the wire is bytes, not rows

The response is three things: **stdout bytes, stderr bytes, exit code.** The server runs
`harlequin.query` and `hsql.output` exactly as the cold path does, writing into a buffer
instead of `click.open_file("-", mode="wb")`, and the client copies that buffer to its own
stdout and exits with the code.

This is the decision that makes the feature safe. It means:

- **The client cannot format anything**, so it needs no pyarrow, no layouts, no `--format`
  knowledge, and no ability to disagree with the cold path about a timestamp.
- **Byte-equivalence is testable** (§6). Every golden-format snapshot can be asserted
  against both paths.
- **`--stats`, truncation notices, and the row-cap notice need no protocol support** —
  they are already stderr bytes.

Two things need care:

- **Streaming.** The buffer means a 500-row default costs nothing, but `--limit -1` on a
  large table materializes twice: once as Arrow on the server, once as bytes. That is the
  same failure as #875 and it is not made worse here, but the frame format should be
  **chunked from the start** (many `stdout` frames, then a terminating `exit` frame) so
  that M5's streaming work has somewhere to land. Chunked framing is free now and
  impossible to retrofit without a protocol version bump.
- **`-o PATH`.** The **server** writes it, resolving a relative path against the *client's*
  cwd from the request. Not the client: `--format parquet` writes through duckdb, not
  through our byte stream, and routing it back through the socket would both double the
  memory and break `output.py`'s "one binary stream, `-o` included" invariant. This is
  also the design's clearest statement that **client and server share a filesystem** —
  which a `AF_UNIX` socket already implies.

### 4.4 Named sessions, not hashed connection identity

The tempting design derives the socket path from a hash of the connection identity, so a
client automatically finds the right server. Rejected:

- The hash would have to include connection-affecting options and exclude presentation
  ones (`--format`, `--limit`, `--color`), and that partition is not written down anywhere
  today — it would become a new thing to keep in step with every adapter's options.
- Profiles resolve differently depending on cwd, so the same command in two directories
  would hash differently, or the same hash would mean two databases.
- Hashing secrets to name a file is a bad habit even when the hash is sound.

Instead: **the name is the caller's, and the server owns the identity.** `hsql --serve
prod -P prod` connects however `-P prod` says to, and records what it resolved to. A client
that sends connection-affecting options gets one of two answers:

- identical to the server's → served;
- different → **rejected**, exit 2, naming the flag that differs and what the server has.

Not "reconnect with the new options" (that is a different session, and silently swapping
the database under a caller is the worst possible behavior), and not "ignore them"
(a client that typed `-a postgres` and got DuckDB has been lied to).

Socket path: `$XDG_RUNTIME_DIR/hsql/<name>.sock`, falling back to `$TMPDIR/hsql-<uid>/`
(or `/tmp/hsql-<uid>/`) when `XDG_RUNTIME_DIR` is unset — which is macOS, and also WSL2
without systemd, and also this container (§4.8). The fallback is the common path, not the
exotic one, and the directory is created 0700 and owned by the user either way. The name is
validated as `[A-Za-z0-9_-]{1,64}` so it cannot escape the directory.

**Not platformdirs**, which is what the rest of Harlequin uses for a user directory and
what the first draft named here. It costs ~22ms to import — about the whole of what a warm
invocation is supposed to cost — so the derivation is a dozen lines of `os.path` in
`session.py`, shared by both halves so they cannot disagree about where the socket is. It
is also the smaller question than it looks: `XDG_RUNTIME_DIR` is the only path platformdirs
would resolve differently on Linux, and the fallback branch is the one this design has to
get right anyway.

**A name that fits is not always a path that fits.** `sun_path` holds 104 bytes on
macOS (108 on Linux), the directory counts toward it, and the `TMPDIR` macOS supplies is
itself ~50 bytes — so a name well inside `[A-Za-z0-9_-]{1,64}` can still be one no socket
can be bound to. The client measures the assembled path and refuses with a message that
names the limit, rather than letting `connect()` fail with a bare errno.

**No `--session-socket PATH` in v1.** Letting a caller name the socket file directly is the
obvious escape hatch and the obvious way to end up with a socket on a mode-0777 shared
directory, or on a filesystem that cannot host one (§4.8). If it ever ships it has to
re-derive §4.7's guarantees for a path it did not choose — verify the directory's owner and
mode, and refuse anything that is not a real local filesystem — which is most of the reason
it is not in v1.

### 4.5 One request at a time

Measured, on this checkout: **two threads calling `execute()` on one DuckDB connection
fail** —

```
HarlequinQueryError('Invalid Input Error: Attempting to execute an
unsuccessful or closed pending query result')
```

— and this is not a DuckDB quirk. The adapter contract says nothing about thread-safety,
and the TUI has never needed it: its database work runs in `@work(thread=True,
exclusive=True)` workers, which is to say *one at a time, and cancel the previous one*.
`hsql --serve` inherits that invariant rather than inventing a new one.

So: **a single worker thread, and a request queue.** A second client waits. Waiting is
bounded by `--queue-timeout` (default: the same value as the request's own `--timeout`,
which shipped in M2), and a client that times out in the queue exits 4 with a message that
says it never reached the database — which is a different fact from a query that ran too long, and the
caller needs to be able to tell them apart.

The accept loop stays responsive while a query runs, so `--session-status` and cancellation
(§4.6) are answerable mid-query. That is the whole reason the queue is in the server rather
than implicit in a single-threaded accept loop.

### 4.6 Cancellation, and a real gotcha in the contract

`Ctrl-C` on a client must stop the query, not orphan it. The client catches `SIGINT`, opens
a **second** connection to the socket (the first is busy carrying the response), sends
`cancel` with its request id, and exits 130.

The server calls `connection.cancel()` when the adapter implements it. When it does not —
`cancel()` is an optional method, and `HarlequinAdapter.IMPLEMENTS_CANCEL` says whether it is real — the honest
behavior is to detach the client, exit 130, and **say on stderr that the query is still
running on the server**, because it is.

The gotcha, found while measuring: DuckDB's cursor reports an interrupt by returning `None`
from `fetchall()`, not by raising:

```python
def fetchall(self) -> AutoBackendType | None:
    try:
        result = self.relation.to_arrow_table()
    except duckdb.InterruptException:
        return None
```

In the TUI that is fine, because the user pressed the button and the app knows. **In server
mode a cancelled query and an empty result are the same bytes**, so the server must
attribute cancellation from its own bookkeeping — "I called `cancel()` on request 7, and
request 7 came back empty" — rather than from anything the adapter says. Worth writing down
because the alternative is a caller who sees `(0 rows)` and exit 0 for a query that was
killed.

**This is not a new mechanism, and PR 4 should not invent one.** `--timeout` shipped in M2
and has exactly this problem: `harlequin/hsql/timeout.py` cancels, then reads its own
deadline between results and attributes the empty result itself, and
`diagnostics.report_timeout()` is the line that says so. The server's bookkeeping is the
same shape, one level up — "I called `cancel()` on request 7" instead of "the deadline
passed" — and it should read like it.

(A cleaner long-term fix is a distinguished return or exception on the contract, which is
an adapter-ecosystem change of the same shape as M2's five additive members. Out of scope
here; noted in §7.)

### 4.7 Security: the server is a credential, held open

This is the part that deserves the most conservatism, because a running server is **a live,
authenticated database connection that anything able to write its socket can use.** There
is no password on the wire and there does not need to be — the socket *is* the credential.

- **`AF_UNIX` only.** No TCP, not even on loopback, not behind a token. Loopback TCP is
  reachable by every process and every container sharing the namespace, and a token file
  is a second secret to leak.
- **The socket lives in a 0700 directory** owned by the user, and the socket itself is
  created 0600 (bind, then `chmod`, or `umask` around the bind — file permissions on a UDS
  are honored on Linux and macOS but not on every kernel, which is why the *directory* mode
  is the real control).
- **The server refuses a request whose argv names `-f -` and carries no stdin
  section.** The client's argv scan is a scan, not a parse, and its set of
  boolean short flags is hsql's own — a boolean short an *adapter* declares is
  not in it, so `-zf -` for such a `-z` would forward no stdin. One check on the
  server turns every miss of that shape, including ones nobody has thought of,
  into an exit 2 rather than a silently empty script. The server never reads its
  own stdin.
- **Both halves `lstat` the directory before trusting it.** `XDG_RUNTIME_DIR` is
  guaranteed by the system; the `TMPDIR` fallback is not, and on a shared host another
  user can create `/tmp/hsql-<uid>/` first and put a listener at `prod.sock`. What a
  client sends is argv, the cwd and piped stdin, and argv can carry a `CONN_STR` with a
  password in it, so the client refuses a directory it does not own privately — and the
  server does the same, since `os.mkdir(mode=0o700)` does nothing to a directory that
  already exists.
- **`SO_PEERCRED` / `LOCAL_PEERCRED` uid check on accept.** Reject and log any peer whose
  uid is not the server's. Belt and braces over the directory mode, and the thing that
  makes a shared `/tmp` misconfiguration fail closed.
- **`--max-lifetime`** (default 8h) and **`--idle-timeout`** (default 30m,
  `0` to disable). A credential held forever because someone opened a tmux tab in March is
  the predictable bad outcome; these two make the default outcome "it goes away."
- **No secrets in `--session-status`.** It reports the adapter, the session name, the
  redacted connection identity, uptime, request count, and current state — through
  `harlequin.redact` and the declarative `secret=` that shipped with #667, not through a
  hand-written allowlist.
- **An SSH tunnel is part of the credential.** A session opened with `--ssh-host` holds an
  `ssh` process and a local listener for as long as it runs, and `--max-lifetime` is what
  bounds that too.

### 4.8 Windows — and WSL2, which is not the same question

`socket.AF_UNIX` is **not available in CPython on Windows**, through 3.14. The alternatives
are a named pipe with a hand-built DACL, or loopback TCP with a token — the first is real
work with a security model that has to be got right on a platform none of the maintainers
run daily, and the second is the thing §4.7 rejects.

**v1 is POSIX-only.** `hsql --serve` on native Windows exits 2 with a message saying so, and
`HSQL_SESSION` on native Windows warns once and runs cold — which is exactly the
ambient-fallback behavior §4.1 already specifies, so nothing special is needed on the client
side. A named pipe transport is a clean later addition behind the same `protocol.py`, and
pretending otherwise in v1 would mean shipping a weaker security model to every platform to
accommodate one.

**WSL2 gets the feature, because WSL2 is Linux.** It runs a real kernel in a VM, its Python
is a Linux CPython with `AF_UNIX` present, and a server and client both inside it need
nothing special — no branch, no fallback, no separate transport. That matters more than it
sounds: the Windows users most likely to want a warm session for an agent are already
working inside WSL, so "POSIX-only" costs much less than the phrase implies, and the docs
should say *native Windows* rather than *Windows* everywhere so nobody in WSL concludes the
feature is not for them.

Two WSL-specific notes, both of which §4.4's path derivation already handles:

- **Do not put the socket on a DrvFs/9p mount.** Binding a unix socket under `/mnt/c` does
  not work. The socket lives in the runtime dir, so this is right by construction — but it
  is one of the reasons §4.4 keeps `--session-socket PATH` out of v1.
- **`XDG_RUNTIME_DIR` is often unset under WSL2**, since systemd is opt-in there. That is
  exactly the fallback branch §4.4 specifies (a 0700 `hsql-<uid>` directory under
  `TMPDIR`), and it is not hypothetical — it is unset in the container these
  measurements were taken in, too. The fallback is the common path, not the exotic one, and
  should be tested as such.

What does **not** work, and cannot be made to: a native-Windows `hsql.exe` reaching a
session running inside WSL2. That is not a socket-routing problem to solve — they are two
different Python installations on two different filesystems, and the Windows one has no
`AF_UNIX` to offer regardless. Stay on one side of the boundary.

### 4.9 Lifecycle, and the four ways this goes wrong

| Failure | Detection | Behavior |
| --- | --- | --- |
| **Stale socket** (server died, file remains) | `connect()` → `ECONNREFUSED` | client reports it and falls back or fails per §4.1; the **server** unlinks a stale file when it starts, because a running one also refuses between its `bind()` and its `listen()`, and on the BSDs whenever its backlog is full — a client that deleted the file on one refusal would take a live session away from every caller after it |
| **Version skew** (server 2.13, client 2.14) | server sends its version in the handshake; the client compares against a literal in `protocol.py` | refuse, exit 2, "restart the session" — never serve, because output bytes are the API and two versions may not agree on them |
| **Server busy shutting down** | server stops accepting before it closes the connection | client sees a clean refusal, not a truncated frame |
| **Client dies mid-query** | `EPIPE` on write | server finishes or cancels the request, drops the response, keeps running |

The version check is stricter than it needs to be on purpose. `hsql`'s output format is
documented as frozen, but "frozen" has always meant "across releases we intend"; a server
from a different release serving bytes a caller attributes to the installed version is a
category of bug nobody will diagnose quickly.

**A literal, and not `importlib.metadata.version("harlequin")`**, which costs ~43ms —
nearly twice the whole warm invocation. That makes it the second version `uv version`
cannot reach, beside the plugin manifest, so `scripts/bump_plugin_version.py` became
`scripts/bump_versions.py` and writes both; a test pins each to the installed version, so a
release that forgot fails in the release PR rather than in a user's shell.

### 4.10 Observability

`hsql --session prod --session-status` returns JSON on stdout:

```json
{"session":"prod","pid":8123,"version":"2.13.0","adapter":"duckdb",
 "connection":"/home/ted/warehouse.db","uptime_s":412,"requests":37,
 "state":"idle","queued":0,"transaction_mode":null,"ssh":null,
 "idle_timeout_s":1800,"expires_in_s":26988}
```

Answerable while a query is running (§4.5), which is what makes "is it hung or is it slow"
a question with an answer. `transaction_mode` is on it because §5 says it has to be.

## 5. The thing that isn't speed: a session is state

Everything above is mechanism. This section is the actual product decision.

A cold `hsql` invocation is **hermetic**: fresh process, fresh connection, no memory of the
last one. Every existing `hsql` behavior, doc sentence, and test assumes that. A warm
session is the opposite, and the difference is observable in ways a caller will not expect:

- **Temp tables persist.** `create temp table t as select ...` in one invocation is
  queryable by the next. This is the single most useful thing about the feature, and it is
  the one nobody will read the docs to discover.
- **Session settings persist.** `SET`, `search_path`, `SET TIME ZONE`, DuckDB `PRAGMA`s,
  installed extensions. A `SET` in invocation 3 changes the results of invocation 12.
- **In-memory databases persist.** `hsql --serve scratch :memory:` is a scratch warehouse
  that survives between calls — genuinely good, genuinely a semantic change.
- **Transactions persist, and this is the sharp edge.** A caller who runs `BEGIN` and then
  exits has left an open transaction holding locks, and every subsequent request in that
  session runs inside it. In a cold invocation the process exit rolled it back. Nothing
  rolls it back now.

Three responses, and I would take all three:

1. **Never on by default, never auto-started, never a silent fallback.** All three are
   already in §4.1. This is the reason for them: the feature changes semantics, so it must
   be something a caller asked for.
2. **The server reports transaction state whenever it is not the default** — on stderr
   after the request, and in `--session-status`. `HarlequinConnection.transaction_mode`
   already exists on the contract, so this costs nothing and turns the worst failure mode
   into a visible one. A session sitting in an open transaction is a thing the caller
   should be told about every single time, not once.
3. **`hsql --session prod --session-reset`** rolls back, closes, and reconnects, without
   restarting the process (so the imports are still warm). This is the escape hatch for
   "the agent left the session in a weird state," and it is a much better answer than
   asking someone to find and kill a pid.

What I would **not** do is reset between requests. It would make the server semantically
identical to the cold path, which sounds safe until you notice it also throws away the
half of the win that is `connect()` — and against a warehouse, that is the larger half.

### 5.1 Documenting it as a session, not as a cache

The docs framing follows from this: **`hsql --serve` is not "hsql, but faster." It is "a
database session you can send commands to."** The speed is a consequence. A caller who
holds the second model in their head will predict the temp-table behavior correctly; a
caller who holds the first will file a bug.

## 6. Testing: byte-equivalence, but only where it is true

The risk this feature carries is not that it fails, it is that it **drifts** — that in some
corner (a NULL, a `-t` footer, an error's exit code, a truncation notice) the warm path and
the cold path produce different bytes, and a caller who set `HSQL_SESSION` in their
environment months ago gets a different answer than the docs describe.

**Byte-equivalence is the guard for that, and it is a claim about one invocation, not about
a test.** Two invocations against a session are *supposed* to diverge from two cold ones —
that is §5, and it is the feature. A parametrization that ran the whole suite both ways and
demanded identical bytes would fail on every test that invokes `hsql` twice, and the
tempting fix — quietly exempting those — would put the divergences nobody has thought about
in the same bucket as the ones we designed. So the guard has to be stated with its scope on
it:

> **Equivalence holds per invocation, from a fresh session.** Given the same starting state,
> one `hsql` invocation produces the same bytes and the same exit code whether it ran cold
> or warm. Nothing is claimed about what the *next* invocation sees.

That precondition has to be manufactured, because the cold path gets it for free (a new
process every time) and the warm path does not. **Each test gets a fresh session** — one
server per xdist worker, `--session-reset` between tests — which is cheap, since a reset is
a reconnect and not a re-import. Pleasingly, it makes the test suite the primary consumer of
the escape hatch §5 proposes for humans, so the thing that rescues a wedged session is
exercised hundreds of times per run rather than never.

Three kinds of test, then, not one:

1. **Equivalence** — every single-invocation functional test, run twice, asserting identical
   stdout bytes, identical stderr bytes, identical exit code. The golden-format snapshots
   (`test_golden_formats.py`) come along for free, since they are single-file syrupy
   snapshots written in binary and already assert on bytes. This is the drift guard, and it
   is the suite that runs on every PR if only one can.
2. **Divergence, marked and asserted** — a multi-invocation test cannot be in group 1, and a
   marker (`@pytest.mark.session_divergent`, registered in `[tool.pytest.ini_options]`
   beside `online`, `use_cache` and `py12`) takes it out. The marker's cost is that it does
   not merely opt out: **the test must assert what the warm behavior *is***, not just that it
   differs. A temp table created by invocation 1 is `no such table` cold and a result set
   warm, and both belong in the assertions. Otherwise the exemption list becomes the place
   bugs live, which is exactly what the exemption exists to prevent.
3. **Session semantics** — tests that only exist on the warm path, because they are the
   feature: state surviving between invocations, an open transaction being reported, a reset
   clearing it, two clients queueing, a session refusing a client whose connection options
   differ.

The marker earns its keep twice over, because **the set of tests carrying it is an
executable version of §5.** §5's list of what statefulness changes is prose today; the
marker turns it into an enumerated one that cannot silently grow. A contributor who
introduces a new divergence has to mark a test, and the marker is the review trigger that
asks whether §5 should have mentioned it.

Alongside those:

- **An import-linter contract**, next to the existing `hsql does not reach the TUI` one,
  saying the client's three modules may not import `click` or anything of Harlequin's
  outside themselves. Its other half is *not* expressible there — a contract's source
  module matches its descendants, so "`harlequin.hsql.__init__` may not import click at
  module scope" cannot be written — and is a run-time test instead. Both shipped in PR 1.
- **A cold-start benchmark for the client**, next to the existing one for `hsql`
  (`scripts/cold_start.py`), plus a run-time test that counts the client's modules against
  a bare interpreter — a count, not a wall clock, because the count is what a shared CI
  runner can hold. Both shipped in PR 1.
- **Deterministic lifecycle tests**, not timing ones: stale socket, wrong uid (skipped
  unless the test runner can drop privileges), version skew, a client killed mid-query, two
  clients queued, a cancel that lands, a cancel against an adapter that cannot cancel.

One trap worth naming, since the suite runs under `-n auto`: **a session-scoped server
fixture is cross-test contamination waiting to happen.** A test that runs `SET` or leaves a
transaction open changes the result of an unrelated test that lands on the same worker
later, and it will present as a flake with no obvious cause. The per-test reset above is
what prevents it, and it is the reason the fixture must not be session-scoped for
convenience.

## 7. What's cut, and why

- **Auto-start.** A client that spawns a server when it does not find one is the obvious
  ergonomic win and the obvious way to end up with orphan processes holding warehouse
  credentials that nobody remembers starting. It is also the feature that is *easy to add
  later and impossible to remove*, once scripts depend on it. Ship explicit-only, watch how
  people actually set sessions up, then decide. If it does land, it should be
  `--session-autostart` and it should inherit the idle timeout, not the max lifetime.
- **Windows.** §4.8.
- **TCP, remote servers, containers-talking-to-hosts.** `AF_UNIX` and a shared filesystem
  are assumptions of §4.3's `-o` handling as well as §4.7's security model. Removing them
  is a different product.
- **Connection pooling / parallel requests.** §3.4. A read-only pool is a plausible later
  addition, but only for `--read-only` sessions, where "which connection" is unobservable.
- **Multiple databases per server.** One session, one connection. Two databases is two
  `--serve` processes, and they cost 200MB of RSS each at most, which is not the
  constraint here.
- **A session for the TUI.** `harlequin --session prod` sounds appealing and is not: the
  IDE holds its own connection for its whole run, so it has nothing to gain, and it would
  put the TUI's evolving surface on the frozen protocol.
- **A distinguished cancellation signal on the adapter contract** (§4.6). Real, and an
  ecosystem change; it belongs with M2's additive members, not here. The server's own
  bookkeeping is a correct workaround in the meantime.
- **A compiled client.** 16ms of the 27ms is CPython boot, so a Rust or Go client would
  land near 2ms. It also means platform wheels for a project that ships pure Python, and
  it optimizes the half of the budget that is already smallest. Revisit only if someone is
  running thousands of invocations.

## 8. Risks

- **A second execution path is a second product.** The mitigation is structural, not
  disciplinary: the server calls `build_cli()` and the same `query`/`output` code, and §6's
  equivalence suite fails if the bytes diverge. If the server ever grows its own
  formatting or its own flag handling, that is the signal the layering went wrong — the
  same signal the product plan already names for MCP.
- **The credential-held-open surface.** §4.7 is the mitigation and it is not complete:
  anything running as the same uid can use the session. That is the same trust boundary as
  the user's `~/.ssh` and their Harlequin config, and it should be stated in the docs in
  those terms rather than implied.
- **Statefulness surprising someone.** §5. The transaction reporting is the mitigation I'd
  least want cut.
- **It overlaps M6.** §3.3 argues that is a reason to build this first and land MCP on top.
  If M6 is imminent, the sequencing question is real and should be settled before PR 1 —
  building both connection lifecycles would be the expensive mistake.
- **Idle servers as an operational nuisance.** Idle timeout defaults to 30m for this
  reason. Expect the first bug report to be "my session died between calls"; the answer is
  `--idle-timeout 0`, and the docs should say so before the report arrives.
- **Nobody uses it.** The honest risk. The feature is only reachable by someone who set it
  up deliberately, which §5 says it must be. Mitigations are the docs framing, a
  `SessionStart`-hook example, and the fallback warning — which is the one place a caller
  who *should* be using a session will be told it exists.

## 9. Shipping sequence

Six PRs, in dependency order. The first two are the ones with the interesting decisions in
them; everything after is additive and independently revertible.

1. **The entry-point split and the framing. Shipped.** `main()` decides whether an
   invocation belongs to a session before it imports anything that is not stdlib;
   `session.py` (the decision, and the socket path), `client.py` (stdlib only) and
   `protocol.py` (chunked frames, handshake, version). No server yet — the client's only
   reachable behavior is the fallback path, which is testable on its own, and the rest is
   pinned against a stub server built on `protocol.py`. Ships the import-linter contract,
   the run-time guards, and the client's steps in `scripts/cold_start.py`.

   Two things moved out of it, both deliberately. **`--session` is not yet a click
   option**: the client intercepts it before click, so it works, but a flag `--help` and
   `--spec` advertise while no server can answer it would be worse than one they do not
   mention yet. And declaring it raises a question PR 2 has to answer anyway — a click
   parameter's name becomes a *profile* key, and `session` must not be one, because the
   decision is made before any config file is read. That is a `CLI_ONLY_SSH_KEYS`-shaped
   refusal, and it belongs with the flag. Until then no adapter reserves the spelling
   either; none of the four checked declares it (§4.1).
2. **The server: accept loop, one worker, the queue, the uid check, socket lifecycle.**
   `hsql --serve NAME`, the `--session` click option and its profile-key refusal,
   `HSQL_SESSION`, and the §4.1 flag partition on both sides. Ships §6's equivalence suite
   with it, not after it — this is the PR where equivalence is cheap to establish and
   expensive to retrofit.
   **`--session-reset` lands here too**, ahead of the rest of the lifecycle work: §6's
   fixture needs it to give each test a fresh session, so it is a dependency of the suite
   rather than a nicety, and the suite is the thing that proves it works.
3. **Identity and rejection.** The server's recorded connection identity, the
   differing-options rejection, client-side profile resolution against the client's cwd
   (§4.2), `--session-status`. One question this PR settles that the first draft did not
   ask: **whether `HARLEQUIN_CONFIG_PATH` travels with the request.** It is the caller's
   intent in the same way `--config-path` is, and it is the one environment variable that
   changes which config file a run reads — but forwarding it widens
   `protocol.FORWARDED_ENV_VARS` past "what `--color auto` needs", which is the line PR 1
   drew. Decide it here, with the rest of config resolution.
4. **Cancellation.** `SIGINT` → cancel frame → `connection.cancel()`, the
   `IMPLEMENTS_CANCEL = False` path, and the DuckDB `fetchall() -> None` attribution.
5. **Lifecycle and state hygiene.** `--idle-timeout`, `--max-lifetime`, transaction-mode
   reporting, and the secret-on-a-server-command-line warning (§4.1).
6. **Docs.** The "Headless & Agents" topic gains a session section written per §5.1, plus a
   `SessionStart`-hook example and an `hsql --help` mention. Not optional and not last in
   spirit — a feature that must be deliberately adopted is a feature that lives or dies by
   its docs.

M2 has shipped, and M3 with it. M4 is in flight in three releases and touches `cli.py`
elsewhere (`--open`, the hooks, query history), so sequencing against it is a scheduling
question rather than a technical one. The one real ordering constraint is still §3.3's:
**settle the relationship with M6 first.**

---

## 10. What this revision changed

For a reader who has the first draft in their head. Nothing about the shape of the design
moved; the changes are the arithmetic, the flags that shipped in between, and three things
the first draft got wrong about the entry point.

- **Re-measured on 2.13.0** (§1, §2). The machine is slower than the first draft's, so the
  absolutes moved and the ratios did not: 348ms cold, 27ms warm, and the database work is
  5ms of the 348.
- **§2 is the shipped client, not a prototype.** PR 1's numbers replace the throwaway's.
- **The entry point had three imports on it, not one** (§2.1). click was the known one;
  `harlequin.hsql.diagnostics` (68ms, via `redact` → `config` → msgspec) and `typing`
  (10ms, via `harlequin/__init__.py`) were not. Together they were more than twice the
  invocation they sat on.
- **One import-linter contract, not two** (§2.1, §6). The second is not expressible; it is
  a run-time test.
- **The socket path is derived with stdlib, not platformdirs** (§4.4), which costs ~22ms.
- **The version literal lives in `protocol.py`, and a release script writes it** (§4.9).
- **M2's flags are no longer conditional** — `--read-only`, `--timeout`, the mode options
  and declarative `secret=` all shipped, and `--timeout`'s implementation in
  `hsql/timeout.py` is the precedent PR 4's cancellation attribution should follow (§4.6).
- **The SSH options are in the partition** (§4.1), as connection-time, and the tunnel is
  part of what §4.7 calls a credential held open.
- **`--session` waits for PR 2 to become a click option** (§9), and PR 3 has one more
  question to answer about `HARLEQUIN_CONFIG_PATH`.
