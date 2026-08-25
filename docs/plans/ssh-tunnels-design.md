# Tunnels — a design for [#545](https://github.com/tconbeer/harlequin/issues/545)

A proposal for connecting Harlequin and `hsql` to a database that is only reachable through
an SSH bastion, **with any adapter, including out-of-tree ones that are never changed**.

Nothing here is implemented. This is the design to argue with before the first PR.

---

## Bottom line up front

1. **Harlequin manages a tunnel; it does not touch connection details.** The user gives the
   adapter the address the database has **from the SSH host's side**, and the tunnel makes
   that address true on this machine. `--ssh-forward 5432` binds `127.0.0.1:5432` here and
   forwards it there, so a conn_str of `postgresql://user@localhost:5432/app` needs no
   rewriting — it is already pointing at the tunnel.
2. **That is what makes it work with any adapter.** Core never has to know which of an
   adapter's options is the host, never parses a DSN, and adds nothing to the adapter API.
   An adapter cannot tell it is tunneled, and one published four years ago works today.
3. **The forwarder is a subprocess, not a library.** `ssh -L`, plus a generic
   `--tunnel-command` for what is not SSH (`cloud-sql-proxy`, `aws ssm`,
   `kubectl port-forward`, Teleport). **Zero new dependencies** — which answers the
   four-year-old blocker on the issue — and it inherits the user's whole `~/.ssh/config`:
   `ProxyJump`, agent, certificates, hardware keys, `Match` blocks, 2FA. A paramiko backend
   stays possible behind the same ABC and an extra (`harlequin[ssh]`); §8 says what would
   trigger it.
4. **The tunnel comes up before Textual does.** Not an implementation detail: it is the only
   way an SSH password, a key passphrase, or a 2FA prompt can reach a human.

What core owns is the **lifecycle** — bring the forward up before anything connects, report
what went wrong in the two commands' own error paths, tear it down on exit — and that is the
whole of the feature.

---

## 1. What the issue asks for

> Need to think through whether this is an adapter feature or a harlequin feature. Maybe
> it's a library that adapters can use. Would be nice to provide CLI options for
> configuring an ssh tunnel, and then intercept/rewrite the host/port/etc options passed
> through to the adapter.

Two later comments matter:

- The maintainer's blocker (Feb 2025): `sshtunnel` looks perfect and has not shipped in four
  years. paramiko was offered in reply as the maintained thing underneath it.
- A request for **Cloud SQL**, which is not SSH at all. It is the reason the abstraction here
  is "a subprocess that makes a remote endpoint reachable locally", with SSH as one instance.

**The rewrite half of the issue is what this design drops.** §11 has the reasoning; the short
version is that intercepting connection details means core guessing which of an adapter's
options is a host, and guessing wrong quietly. Binding the local end to the port the user
already typed reaches the same place with none of that.

## 2. Where the code sits today

Both commands build a config dict, instantiate one adapter with it, and connect:

| | IDE | headless |
|---|---|---|
| adapter constructed | `cli.py:548`, `adapter_cls(conn_str=conn_str, **config)` | `hsql/cli.py:_connect()` |
| connection opened | `app.py:_connect()`, on a worker thread, after `tui.run()` | `hsql/cli.py:_connect()`, inline |
| connection closed | `app.py:action_quit()` | never explicitly; the process exits |
| cache key | `adapter.connection_id` or `get_connection_hash(conn_str, config)` (`cli.py:553`) | n/a |

Two existing facts the design leans on:

- **A command's own click params are what claim a profile key.** `parse_profile_options()`
  takes `command_options` from `{param.name for param in cmd.params}`; anything else in a
  profile must be a declared option of the adapter. New `--ssh-*` options on both commands
  therefore become valid profile keys and JSON-schema keys with no further plumbing.
- **`first_pass()` settles the adapter before click parses anything**, so nothing here
  changes the start-up cost of an invocation that does not tunnel.

## 3. The model

```
   --ssh-host bastion                        ssh -N -T \
   --ssh-forward 5432          ───────►        -o ExitOnForwardFailure=yes \
                                               -L 127.0.0.1:5432:localhost:5432 bastion

   -a postgres                               adapter dials 127.0.0.1:5432
   "postgresql://me@localhost:5432/app"  ──► which is the far side's localhost:5432
```

Two rules, and they are the whole contract:

1. **Connection details are read from the SSH host's side.** `localhost` means the bastion's
   localhost. `db.internal` means whatever the bastion resolves that to.
2. **`--ssh-forward` binds the same port locally by default**, so the address the user typed
   is the address that now works here. `--ssh-forward 5432` is `-L 127.0.0.1:5432:localhost:5432`.

Nothing rewrites anything. `conn_str` and every adapter option reach the adapter exactly as
the user wrote them, and the adapter is never told a tunnel exists.

Three spellings of `--ssh-forward`, matching `ssh -L` from the right:

| written | means |
|---|---|
| `5432` | `-L 127.0.0.1:5432:localhost:5432` — the database runs on the bastion |
| `db.internal:5432` | `-L 127.0.0.1:5432:db.internal:5432` — the bastion is a jump host |
| `15432:db.internal:5432` | `-L 127.0.0.1:15432:db.internal:5432` — something local already has 5432 |

Repeatable, because one `ssh` connection carries as many forwards as you like, and the IDE
takes several `CONN_STR`s.

### What this model gives up, and what it buys

A local port collision is a **hard error**, not a workaround: `ExitOnForwardFailure=yes`
means `ssh` exits and says `bind: Address already in use`, and the user picks a local port
with the three-part form. That is the honest failure, and the alternative — quietly binding
somewhere else — is the failure mode that makes people distrust the feature.

What it buys, beyond not guessing:

- **TLS still works the way the user asked for it.** Nothing silently changes the hostname
  the driver validates against; whatever they typed is what gets verified.
- **`connection_id` and the catalog cache key stay stable across runs** (§7), because there
  is no ephemeral port in the config the hash is taken over.
- **DuckDB and SQLite need no special case.** They ignore the tunnel; a duckdb `ATTACH` of
  `postgres://localhost:5432/app` goes through it like anything else.

### The hazard this model does have

A profile that says `host = "localhost"` is only correct with the tunnel up. Run it without
`--ssh-host` and it connects to whatever is on **your** 5432 — which on a developer's laptop
may well be a database, just the wrong one.

Mitigation, in order of how much they cost: the `ssh_*` keys live in the same profile as the
connection details, so they travel together and the failure needs someone to have deliberately
overridden one of them; the start-up notice (§6) says on stderr what was forwarded; and if
this proves to bite in practice, a `require_tunnel = true` profile key can refuse to connect
without one. Not proposed for v1.

## 4. The forwarder

### 4.1 `SubprocessTunnel` — the only backend in v1

```python
class Tunnel(ABC):
    """A child process that makes remote endpoints reachable on localhost."""

    @abstractmethod
    def start(self) -> None:
        """Start it and block until the forwarded ports accept connections.
        Raises HarlequinTunnelError, quoting the child's stderr."""

    def stop(self) -> None: ...
    def __enter__(self) -> Tunnel: ...
    def __exit__(self, *exc: Any) -> None: self.stop()
```

- The child stays in the **foreground** (no `-f`) with the terminal attached, so
  `Enter passphrase for key ...` and a 2FA prompt reach the human. Readiness is a
  connect-poll against each forwarded local port with `--tunnel-timeout` (default 10s),
  which also notices the child exiting early; on failure the error quotes the child's stderr
  verbatim, because `ssh`'s diagnostics are better than any we would write.
- Teardown is `terminate()` then `kill()`, from a `contextlib.ExitStack` wrapping `tui.run()`
  and the `hsql` run, plus an `atexit` backstop.
- `-o ServerAliveInterval=30`. No reconnect in v1: a dead tunnel surfaces as the adapter's own
  connection error, which is already an error modal in the IDE and exit 3 in `hsql`.

The user's `~/.ssh/config` is read by `ssh` itself, so `--ssh-host bastion` picks up
`HostName`, `User`, `Port`, `IdentityFile` and `ProxyJump` from a `Host bastion` block — all
of it, correct, and matching whatever the user already tests with `ssh bastion`. This is the
strongest argument for the subprocess: **that configuration surface is not ours to
reimplement or to document.**

### 4.2 `--tunnel-command` — the same lifecycle, for what is not SSH

```bash
hsql --tunnel-command 'cloud-sql-proxy --port 5432 my-proj:us-central1:pg' --tunnel-port 5432
hsql --tunnel-command 'kubectl port-forward svc/pg 5432:5432' --tunnel-port 5432
```

The command binds whatever port the user's connection details name — the same contract as
§3, so there is nothing to substitute. `--tunnel-port` (repeatable) is what the readiness
poll watches; omit it and Harlequin starts the child and carries on.

Roughly 30 lines on top of the SSH backend. It covers the Cloud SQL request on the issue,
every corporate access proxy, and the next one nobody has heard of yet — and it is what makes
the whole feature **testable in CI without an SSH server** (§9).

### 4.3 Not in v1

Reconnect, and `ssh` control-socket multiplexing (which would let a second Harlequin reuse a
live connection, and which Windows OpenSSH does not support anyway).

## 5. Public API impact

**None.** No change to `HarlequinAdapter`, `HarlequinConnection`, `HarlequinCursor`,
`AbstractOption`, `catalog.py` or `driver.py`. One new `HarlequinTunnelError(HarlequinError)`
in `exception.py`, and one new module, `harlequin/tunnel.py`.

That an adapter cannot tell it is tunneled is not an accident of the design — it is the
property being bought, and it is why this works for adapters nobody in this org maintains.

## 6. The CLI and config surface

New options on **both** commands. A profile serves both, and `hsql` is where this matters
most: a cron job or an agent cannot ask a human to run `ssh -L` in another terminal first.

| option | meaning |
|---|---|
| `--ssh-host TEXT` | the jump host, or a `Host` alias from `~/.ssh/config` |
| `--ssh-user TEXT` | `ssh -l`; defaults to whatever ssh_config or the local user says |
| `--ssh-port INT` | the SSH port, if not 22 |
| `--ssh-identity PATH` | `ssh -i` |
| `--ssh-forward TEXT` | repeatable; `PORT`, `HOST:PORT`, or `LOCAL:HOST:PORT` (§3) |
| `--ssh-option KEY=VALUE` | repeatable `-o` passthrough; the escape hatch for the rest |
| `--tunnel-command TEXT` | §4.2; mutually exclusive with `--ssh-host` |
| `--tunnel-port INT` | repeatable; ports the readiness poll waits for |
| `--tunnel-timeout FLOAT` | seconds to wait (default 10) |

`--ssh-host` with no `--ssh-forward` is a usage error naming the three spellings: a tunnel
that forwards nothing is always a mistake, and it is better caught before an SSH handshake
than after one.

No password flag. `--ssh-password` would be a credential in `ps` output and in shell history,
for a case `ssh` already handles better with a key, an agent, or its own prompt. If one is
ever added it is `secret=True`, and `redact._SECRET_NAME` grows `passphrase`, which it is
missing today.

`hsql`'s flags are the frozen part of its API, so these join the reserved spellings in
`first_pass.attach_adapter_options()`: an adapter that declares `--ssh-host` today would lose
that spelling, visibly, in `--help`, and keep the option under its profile key. Worth a survey
of published adapters before merging; I know of none that claims one.

In a config file, unremarkably — and note that the connection details are the far side's:

```toml
[profiles.prod]
adapter = "postgres"
host = "localhost"
port = 5432
dbname = "app"
ssh_host = "bastion.example.com"
ssh_forward = ["5432"]
```

Both commands pick this up through the existing profile merge. `config_schema.py` generates
its keys from click params, so `schemas/config-v1.json` regenerates with
`scripts/write_config_schema.py`, and the schema test keeps them honest.

**The start-up notice.** `hsql` on stderr (never stdout — that belongs to query output), the
IDE as a notification and on the debug screen:

```
tunnel: 127.0.0.1:5432 -> localhost:5432 via ssh bastion.example.com
```

One line, and it is what tells a user which database they are actually looking at.

## 7. Ordering, cache keys, and the two commands

```
first_pass -> click parses -> profile merge -> Tunnel.start()   <-- prompts happen HERE
                                                     |
                                        adapter_cls(...) -> tui.run() / hsql runs
                                                     |
                                              ExitStack unwinds -> Tunnel.stop()
```

The IDE opens its database on a worker thread after the app is running, which is right for a
database and wrong for a tunnel: once Textual owns the terminal, `ssh` cannot ask for a
passphrase and a 2FA push has nowhere to print "check your phone". Starting the tunnel in the
click callback also means one error path for both commands — `pretty_print_error` /
`diagnostics.report_error`, exit code 3 (`ExitCode.CONNECTION`) — before a widget is mounted.

The cost is that `harlequin --ssh-host …` authenticates before showing a UI, which is what
every CLI that tunnels does and what a user who typed an SSH flag expects.

**Cache keys need one small change.** Nothing is rewritten, so `get_connection_hash()` and
`adapter.connection_id` are already stable across runs — but two bastions fronting two
databases both look like `localhost:5432`, and would share a catalog cache and a query
history. The ssh destination (and the forward specs) join the hashed material.

## 8. Dependencies, imports, packaging

**v1 adds no dependency at all**: `subprocess`, `socket`, `shlex`.

`harlequin/tunnel.py` imports no Textual and no adapter, and joins the "adapter API is
reachable without the TUI" import-linter contract. It is imported from the CLI callbacks only
when a tunnel was asked for, so an invocation without one pays nothing —
`scripts/cold_start.py` should show no change.

**When paramiko would earn its place** — as `harlequin[ssh]`, a second `Tunnel`
implementation, nothing else changing:

- Windows users without OpenSSH. It ships with Windows 10+, but is removable.
- Containers: this repo's own dev container has no `ssh` binary, and neither does the
  `python:3.x-slim` image most people build on.
- Anything wanting in-process sockets rather than a listener — which is the shape Google's
  Cloud SQL *connector* (as opposed to its proxy binary) wants, and the one thing
  `--tunnel-command` cannot express.

Until one of those has a user asking, the argument on the issue stands: an unmaintained
wrapper is not worth taking, and a maintained one is still a `cryptography` dependency on
every Harlequin install, to solve a problem `ssh` already solves on the same machine.

## 9. Testing

**End to end, no SSH server, no `online` marker.** `--tunnel-command` makes the whole
lifecycle testable with a Python child as the forwarder:

```python
--tunnel-command "{sys.executable} -m tests.tcp_forward 5432 127.0.0.1 {real_port}"
--tunnel-port 5432
```

A ~30-line loopback TCP forwarder in `tests/`, a stub server on the far side, and the test
asserts that the adapter reached through it, that the readiness poll waited, and that the
child is dead once the command exits. Runs on all three OSes and needs no secrets.

**Unit, no network:**

| case | expected |
|---|---|
| `--ssh-forward 5432` | argv contains `-L 127.0.0.1:5432:localhost:5432` |
| `--ssh-forward db.internal:5432` | `-L 127.0.0.1:5432:db.internal:5432` |
| `--ssh-forward 15432:db.internal:5432` | `-L 127.0.0.1:15432:db.internal:5432` |
| `--ssh-forward` repeated | one child, two `-L` |
| `--ssh-host` with no `--ssh-forward` | usage error, exit 2 |
| `--ssh-host` and `--tunnel-command` | usage error, exit 2 |
| a garbage forward spec | usage error naming the three forms |
| child exits during the poll | `HarlequinTunnelError` quoting its stderr, exit 3 |
| poll times out | ditto, naming `--tunnel-timeout` |
| profile round trip | `ssh_host`/`ssh_forward` survive the merge and reach the argv |
| cache key | two ssh hosts, same conn_str, two different hashes |

One test runs `ssh -V` and skips if absent, to prove the built argv is accepted by a real
client where one exists.

## 10. Phasing

1. **`harlequin/tunnel.py`** — the `Tunnel` ABC, `SubprocessTunnel`, argv construction, the
   readiness poll, `HarlequinTunnelError`. Unit tests only; no CLI. Mergeable alone.
2. **Wire it into both commands** — the options, the `ExitStack`, the notice, the cache-key
   change, and the loopback end-to-end test.
3. **`--tunnel-command`**, `--info` / debug-screen reporting, regenerated config schema.
4. **Docs** in `tconbeer/harlequin-web` — including the "details are the far side's" contract,
   which is the whole of what a user has to learn — and a `CHANGELOG.md` entry under
   `[Unreleased]` → Features, referencing
   [#545](https://github.com/tconbeer/harlequin/issues/545).

## 11. Alternatives considered

| | why not |
|---|---|
| **Rewriting the adapter's host/port** (the issue's original sketch, and an earlier draft of this doc) | Core has to know which option is the host. There is no general answer: it would take a new `role=` declaration on `AbstractOption` that no published adapter has, a name-matching backstop for `host`/`port` that is a guess, and an in-place DSN rewrite for adapters that take a positional conn_str — three mechanisms, each with a way to be quietly wrong, to reach where `-L 5432:localhost:5432` reaches with none. It also bakes an ephemeral local port into `connection_id` (losing the catalog cache every run) and breaks TLS hostname verification without the user having asked for it. |
| **A `{tunnel_port}` placeholder** the user writes into their conn_str | Fully general and explicit, and it survives any option spelling — but it is a second thing to learn for the same result, and it makes a profile unusable without the tunnel rather than merely wrong. Worth revisiting only if the port-collision case turns out to be common. |
| **A SOCKS proxy (`ssh -D`) with `socket.socket` patched** | The one design where the driver keeps the real hostname, so TLS verification is untouched. It only works for pure-Python drivers: psycopg2, mysqlclient, ODBC and duckdb's extensions open sockets in C and never see Python's `socket` module. |
| **Each adapter grows `--ssh-*`** | N implementations of one thing, N spellings, and the adapters that need it most are out-of-tree. |
| **A library adapters call (`harlequin-tunnel`)** | Adapters must opt in, so "works with any adapter" becomes "works with adapters that adopted it". Reasonable *in addition*, for an adapter that wants a tunnel inside its own connection (the Cloud SQL connector); not a substitute. |
| **`sshtunnel`** | The blocker on the issue: four years without a release, and it is a wrapper over paramiko for behavior we would otherwise get from a binary already on the machine. |
| **paramiko directly, in v1** | A `cryptography` dependency on every install, and it reimplements the part of `~/.ssh/config` users already have working. Kept as the v2 backend, §8. |
| **Tell users to run `ssh -L` themselves** | It works, and it is what people do today. It also means two terminals, a remembered port, and a profile that only connects if a human ran something first — which is exactly what `hsql` in a cron job or an agent loop cannot do. |

## 12. Open questions for review

1. **Is `--ssh-forward` required, or can it default to `--ssh-port`-style guessing?** Required
   is proposed. The alternative is reading the conn_str to find a port, which is the
   inference this design exists to avoid — but it would make the common case one flag.
2. **Should the local bind address be configurable?** `127.0.0.1` is proposed and hardcoded.
   `0.0.0.0` would expose the far side's database to the user's network, which seems like a
   thing to make people ask for.
3. **`require_tunnel = true`** as a profile key, for the §3 hazard — worth it now, or wait for
   someone to be bitten?
4. **Flag naming**: `--ssh-*` for the SSH backend and `--tunnel-*` for the generic one reads
   well but splits `--tunnel-timeout` away from its siblings. All `--ssh-*` with
   `--ssh-command`? All `--tunnel-*`?
