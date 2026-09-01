# Warm sessions for `hsql` — a design for `hsql --serve`

A design for a long-lived `hsql` process that holds an open connection, so that repeated
invocations pay neither the import cost nor the connection cost again. Written against
2.9.0 (M1 shipped, M2 planned in [the M2 technical plan](./m2-hsql-technical-plan.md)),
and measured on this checkout: Python 3.11.15, Linux, four adapters installed.

**Bottom line up front.** The idea works, and the win is bigger than the 250ms it was
proposed to recover — but only if one constraint is treated as the load-bearing one:
**the client must not import anything Harlequin owns.** A prototype built to that rule
answers `select 1` in **20ms against 250ms cold**, and 20 sequential queries in **0.46s
against 5.4s**. A client that imports `click` and nothing else would already be at 55ms,
and one that imports `harlequin.config` at 70ms — so the difference between a good version
of this feature and a mediocre one is decided by the first three lines of the console
script, not by the protocol.

The other finding is that speed is the *smaller* half. A warm process is a **session**:
temp tables, `SET`, `search_path`, and open transactions survive between invocations. That
is a genuine feature for an agent — and it is also the reason this cannot be turned on by
default, cannot be auto-started in v1, and cannot be a silent fallback. §5 is the part of
this document I would argue about; §1–§2 are mostly arithmetic.

Recommended shape, in one line:

```bash
hsql --serve prod ./warehouse.db      # foreground, holds the connection
HSQL_SESSION=prod hsql -c "select 1"  # 20ms, same bytes, same exit code
```

---

## 1. Where the 250ms actually goes

`hsql -c "select 1"` against in-memory DuckDB, best of twelve runs of the real console
script:

| Invocation | Best | Median |
| --- | --- | --- |
| `hsql -c "select 1"` | 243ms | 250ms |
| `hsql -c "select 1" -a sqlite` | 225ms | 237ms |
| `hsql --help` | 91ms | 93ms |
| `harlequin --version` | 733ms | 755ms |

Instrumenting the same work phase by phase, cumulative from process spawn:

| Phase | Cumulative | Cost |
| --- | --- | --- |
| interpreter ready | 12ms | 12ms |
| `import click` | 39ms | 27ms |
| `harlequin.config` | 54ms | 15ms |
| `harlequin.plugins` | 72ms | 18ms |
| `load_adapter("duckdb")` | 115ms | 43ms |
| `harlequin.query` (pyarrow + `textual_fastdatatable.backend`) | 182ms | 67ms |
| `adapter()` | 182ms | 0ms |
| `connect()` | 193ms | 11ms |
| `execute()` + `fetch()` | 195ms | 2ms |
| layout | 196ms | 1ms |

**The database work is 2ms of a 250ms invocation.** Everything else is getting ready to do
it. M1 already took the cheap wins — lazy entry points, the lazy `textual-fastdatatable`
`__init__`, deferred `harlequin.query` — and what is left is genuinely irreducible on the
cold path:

- **pyarrow, 67ms with the backend.** `harlequin.query` normalizes every result set
  through `create_backend()`, which is the whole reason both front ends agree about what a
  row is. Removing it means a second normalizer, which the M1 plan rejected for good
  reasons that have not changed.
- **The driver, 43ms for duckdb.** Not ours.
- **click, 27ms.** Ours to remove in principle, at the cost of hand-rolling the option
  surface that adapters plug into. Not worth it.
- **CPython itself, 12ms.** The floor for any Python process on this machine.

So: even a version of `hsql` that imported *nothing* of Harlequin's beyond click and its
config would land near 150ms, and one that also dropped click near 120ms. **There is no
sequence of import fixes that reaches 50ms.** That is the honest case for a second
process, and it is worth stating in the plan because the alternative — "keep optimizing" —
has been the right answer up to now and stops being the right answer here.

### 1.1 And `connect()` is 11ms only because DuckDB is a library

The table above understates the win, because the adapter it measures opens a file. The
in-tree numbers are `connect()` at 11ms for in-memory DuckDB, 14ms for a DuckDB file, 2.6ms
for SQLite. Every adapter that speaks to a *server* — Postgres, MySQL, BigQuery, Trino,
Databricks, Snowflake — pays TCP setup, a TLS handshake, and an auth round trip on each
`connect()`, and for the OAuth-shaped ones a token exchange on top. Those are the
connections a human hands an agent, and they are the ones where holding the connection open
matters more than holding the imports.

This design should therefore not be sold on "250ms → 20ms against DuckDB." It should be
sold on **the invocation cost stops depending on how far away the database is**, with the
DuckDB number as the floor case rather than the typical one.

## 2. What a warm session costs instead — measured, not projected

I built a throwaway server (a `AF_UNIX` socket, one pre-connected DuckDB connection, the
real `harlequin.query` core and the real `hsql.output` writer) and a stdlib-only client
that sends SQL and writes back whatever bytes it gets. Same machine, same twenty runs:

| | Best | Median |
| --- | --- | --- |
| warm round trip, `select 1` | **20.0ms** | 21.4ms |
| warm round trip, 1000 rows | 22.0ms | 23.3ms |
| cold `hsql -c "select 1"` | 253.7ms | 263.2ms |
| **20 sequential queries, warm** | **459ms** | — |
| **20 sequential queries, cold** | **5386ms** | — |

The warm client's 20ms decomposes almost entirely into things that are not the feature:

| | Cost |
| --- | --- |
| CPython boot (`python -c pass`) | 12ms |
| `import socket` | +6ms |
| `import struct` | +1ms |
| connect, send, receive, write, exit | ~1ms |

**The round trip is about a millisecond.** The client is 95% interpreter start-up, which
means the protocol can afford to be careful — framing, a handshake, a version check, a
capability exchange — without moving the number. It also means the ceiling is 12ms unless
the client stops being Python, which §7 leaves as future work rather than pretending it is
free.

### 2.1 The constraint this puts on the entry point

`import click` costs 27ms and `import json` costs 3.5ms. On a 20ms budget those are 135%
and 18% respectively. So the rule is not "the client should be lean," it is:

> **`harlequin.hsql.main()` must decide whether this invocation belongs to a session, and
> hand off to a stdlib-only module, before it imports click.**

Today `main()` opens with `import click`. The change is to look at `HSQL_SESSION` /
`--session` first — a `sys.argv` scan and an `os.environ` lookup, both free — and only fall
through to `build_cli()` when there is no session. That is a five-line change to
`__init__.py` and it is the single most important line-level decision in this document.

It also gives the guard a shape CI can hold, exactly as the "no Textual in `hsql`" rule
does: **an import-linter contract saying the client module's graph contains nothing from
`harlequin` and nothing from `click`**, plus a test that runs the client with
`-X importtime` and fails if the module count crosses a threshold. Otherwise the first
contributor who needs a config value in the client re-adds 15ms and nobody notices.

## 3. Why a daemon, rather than the four cheaper things

### 3.1 Not "keep optimizing imports"

§1 measured the floor at ~120–150ms. Rejected on arithmetic.

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
running on the same M1 execution core. **It is already a warm session.** It holds a
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
| `--host-session NAME` | "Host a session" reads well, but `-h/--host` is a connection option on the postgres, mysql and trino adapters — and **hsql's own flags win collisions** (`_attach_adapter_options`), so this would quietly take `--host` away from them. Every new flag in this design needs that check; `--serve`, `--session`, `--idle-timeout` and `--max-lifetime` pass it against the in-tree adapters, and should be checked against the ecosystem before PR 1. |
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
| **connection-time** | once, when the server connects | `CONN_STR`, `-a`, `-P`, `--config-path`, every adapter option, M2's `--read-only` | **accepted** | accepted, but rejected if it differs from the server's (§4.4) |
| **per-request** | on every invocation | `-c`, `-f`, `-o`, `--format` and the shorthand flags, `-t`, `-A`, `--no-header`, `--no-footer`, `--null-string`, `--limit`, `--display-rows`, `--result`, `--on-error`, `--stats`, `--color`, M2's mode options and `--timeout` | **refused, exit 2** | accepted |
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
250ms; the same password on an eight-hour session is exposed for eight hours, to every
process on the box. So:

- **`-P` is the spelling the docs should show.** A profile is already "how to connect to my
  warehouse," and `hsql --serve prod -P prod` should be the normal form with the flag-soup
  version as the exception.
- **Warn when a secret-typed option is passed literally to `--serve`**, detected through
  M2's declarative secret type (#667) rather than a hand-written list of flag names. A
  warning rather than a refusal in v1, because refusing needs env interpolation (#898) to
  have shipped first so that there is a documented alternative for the container case.
  Tightening it to a refusal once #898 lands is a reasonable later call, and a cheap one.

Clients opt in two ways, and the distinction matters:

| | Spelling | Missing server → |
| --- | --- | --- |
| **ambient** | `HSQL_SESSION=prod` in the environment | warn on stderr, run cold |
| **explicit** | `hsql --session prod -c "..."` | fail, exit 3 (connection) |

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
  __init__.py     main(): session check, then click or the client. No new imports.
  client.py       stdlib only: socket, struct, sys, os. No click, no json, no harlequin.
  protocol.py     framing + the frame vocabulary, shared -- but written so that
                  client.py's half needs no imports the client cannot afford.
  server.py       accept loop, one worker, lifecycle, the uid check.
  session.py      socket path derivation, stale-socket handling, the identity record.
  cli.py          unchanged, plus --serve / --session
```

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
asked for, and `TERM`/`NO_COLOR`** (which `--color auto` reads). Deliberately *not* the
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

Socket path: `$XDG_RUNTIME_DIR/hsql/<name>.sock`, falling back to a 0700 directory under
the platformdirs user runtime/cache dir when `XDG_RUNTIME_DIR` is unset — which is macOS,
and also WSL2 without systemd, and also this container (§4.8). The fallback is the common
path, not the exotic one. The name is validated as `[A-Za-z0-9_-]{1,64}` so it cannot escape
the directory.

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
bounded by `--queue-timeout` (default: the same value as `--timeout`, once M2 ships
it), and a client that times out in the queue exits 4 with a message that says it never
reached the database — which is a different fact from a query that ran too long, and the
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
- **`SO_PEERCRED` / `LOCAL_PEERCRED` uid check on accept.** Reject and log any peer whose
  uid is not the server's. Belt and braces over the directory mode, and the thing that
  makes a shared `/tmp` misconfiguration fail closed.
- **`--max-lifetime`** (default 8h) and **`--idle-timeout`** (default 30m,
  `0` to disable). A credential held forever because someone opened a tmux tab in March is
  the predictable bad outcome; these two make the default outcome "it goes away."
- **No secrets in `--session-status`.** It reports the adapter, the session name, the
  redacted connection identity, uptime, request count, and current state — through M2's
  declarative redaction (#667), not through a hand-written allowlist.

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
  exactly the fallback branch §4.4 specifies (a 0700 directory under the platformdirs
  runtime dir), and it is not hypothetical — it is unset in the container these
  measurements were taken in, too. The fallback is the common path, not the exotic one, and
  should be tested as such.

What does **not** work, and cannot be made to: a native-Windows `hsql.exe` reaching a
session running inside WSL2. That is not a socket-routing problem to solve — they are two
different Python installations on two different filesystems, and the Windows one has no
`AF_UNIX` to offer regardless. Stay on one side of the boundary.

### 4.9 Lifecycle, and the four ways this goes wrong

| Failure | Detection | Behavior |
| --- | --- | --- |
| **Stale socket** (server died, file remains) | `connect()` → `ECONNREFUSED` | client unlinks it, then falls back or fails per §4.1 |
| **Version skew** (server 2.9, client 2.10) | server sends its version in the handshake; the client compares against a literal in `client.py` | refuse, exit 2, "restart the session" — never serve, because output bytes are the API and two versions may not agree on them |
| **Server busy shutting down** | server stops accepting before it closes the connection | client sees a clean refusal, not a truncated frame |
| **Client dies mid-query** | `EPIPE` on write | server finishes or cancels the request, drops the response, keeps running |

The version check is stricter than it needs to be on purpose. `hsql`'s output format is
documented as frozen, but "frozen" has always meant "across releases we intend"; a server
from a different release serving bytes a caller attributes to the installed version is a
category of bug nobody will diagnose quickly.

### 4.10 Observability

`hsql --session prod --session-status` returns JSON on stdout:

```json
{"session":"prod","pid":8123,"version":"2.9.0","adapter":"duckdb",
 "connection":"/home/ted/warehouse.db","uptime_s":412,"requests":37,
 "state":"idle","queued":0,"transaction_mode":null,
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
   (`test_golden_formats.py`) come along for free, since they already assert on bytes. This
   is the drift guard, and it is the suite that runs on every PR if only one can.
2. **Divergence, marked and asserted** — a multi-invocation test cannot be in group 1, and a
   marker (`@pytest.mark.session_divergent`) takes it out. The marker's cost is that it does
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

- **Import-linter contracts**, next to the existing `hsql does not reach the TUI` one:
  `hsql.client` may not import `harlequin.*` or `click`, and `harlequin.hsql.__init__` may
  not import click at module scope.
- **A cold-start benchmark for the client**, next to the existing one for `hsql`, so the
  20ms is a number CI defends rather than a number this document once measured.
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
- **A compiled client.** 12ms of the 20ms is CPython boot, so a Rust or Go client would
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

1. **The entry-point split and the framing.** `main()` checks for a session before
   importing click; `client.py` (stdlib only), `protocol.py` (chunked frames, handshake,
   version). No server yet — the client's only reachable behavior is the fallback path,
   which is testable on its own. Ships the two import-linter contracts and the client
   benchmark.
2. **The server: accept loop, one worker, the queue, the uid check, socket lifecycle.**
   `hsql --serve NAME`, `--session NAME`, `HSQL_SESSION`, and the §4.1 flag partition on
   both sides. Ships §6's equivalence suite with it, not after it — this is the PR where
   equivalence is cheap to establish and expensive to retrofit.
   **`--session-reset` lands here too**, ahead of the rest of the lifecycle work: §6's
   fixture needs it to give each test a fresh session, so it is a dependency of the suite
   rather than a nicety, and the suite is the thing that proves it works.
3. **Identity and rejection.** The server's recorded connection identity, the
   differing-options rejection, client-side profile resolution against the client's cwd
   (§4.2), `--session-status`.
4. **Cancellation.** `SIGINT` → cancel frame → `connection.cancel()`, the
   `IMPLEMENTS_CANCEL = False` path, and the DuckDB `fetchall() -> None` attribution.
5. **Lifecycle and state hygiene.** `--idle-timeout`, `--max-lifetime`, transaction-mode
   reporting, and the secret-on-a-server-command-line warning (§4.1).
6. **Docs.** The "Headless & Agents" topic gains a session section written per §5.1, plus a
   `SessionStart`-hook example and an `hsql --help` mention. Not optional and not last in
   spirit — a feature that must be deliberately adopted is a feature that lives or dies by
   its docs.

M2 does not block any of this, and this does not block M2 — the two touch `cli.py` in
different places. Sequencing between them is a scheduling question, not a technical one.
The one real ordering constraint is §3.3's: **settle the relationship with M6 first.**
