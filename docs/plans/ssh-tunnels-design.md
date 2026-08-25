# Tunnels — a design for [#545](https://github.com/tconbeer/harlequin/issues/545)

A proposal for connecting Harlequin and `hsql` to a database that is only reachable through
an SSH bastion, **with any adapter, including out-of-tree ones that are never changed**.

Nothing here is implemented. This is the design to argue with before the first PR.

---

## Bottom line up front

1. **The tunnel belongs to core, not to adapters.** Every adapter that talks to a network
   database needs the same thing, and the ones that need it most are the ones this repo
   does not own. An adapter-by-adapter implementation is N implementations, N spellings,
   and N adapters that never get around to it.
2. **A tunnel is two mechanisms, and they are separable**: something that makes a remote
   endpoint reachable at `127.0.0.1:<port>`, and a **rewrite** that gets the adapter to
   dial that address instead of the real one. The second is the hard part and the one that
   has to work for an adapter core knows nothing about.
3. **Ship the forwarder as a subprocess, not as a library.** `ssh -L`, plus a generic
   `--tunnel-command` for everything that is not SSH (`cloud-sql-proxy`, `aws ssm`,
   `kubectl port-forward`, `teleport`). This answers the four-year-old blocker on the issue
   — sshtunnel is unmaintained, paramiko is a real dependency — with **zero new
   dependencies**, and it inherits the user's entire `~/.ssh/config`: `ProxyJump`, agent,
   certificates, hardware keys, `Match` blocks, 2FA. A paramiko backend stays possible
   behind the same ABC and an extra (`harlequin[ssh]`); §9 says what would trigger it.
4. **Rewriting is layered, and the bottom layer works with every adapter shipped today**:
   an option's declared role (new, precise, opt-in for adapters), a name backstop for
   `host`/`port` (the same move `harlequin.redact` already makes for passwords), and a DSN
   rewrite inside `conn_str`. Whatever it does, it says so out loud.
5. **The tunnel comes up before Textual does.** That is not an implementation detail: it is
   the only way an SSH password, a key passphrase or a 2FA prompt can reach a human.

Scope of v1: **one hop, one forwarded endpoint, for the life of one invocation.**

---

## 1. What the issue asks for

> Need to think through whether this is an adapter feature or a harlequin feature. Maybe
> it's a library that adapters can use. Would be nice to provide CLI options for
> configuring an ssh tunnel, and then intercept/rewrite the host/port/etc options passed
> through to the adapter.

Two later comments matter:

- The maintainer's blocker (Feb 2025): `sshtunnel` looks perfect and has not shipped in
  four years. paramiko was offered in reply as the maintained thing underneath it.
- A request for **Cloud SQL** (Google's connector), which is not SSH at all. It is the
  reason the abstraction below is "a thing that makes a remote endpoint reachable
  locally", with SSH as one instance, rather than an SSH feature with a config file.

## 2. Where the code sits today

Both commands build a config dict, instantiate one adapter with it, and connect:

| | IDE | headless |
|---|---|---|
| adapter constructed | `cli.py:548`, `adapter_cls(conn_str=conn_str, **config)` | `hsql/cli.py:_connect()` |
| connection opened | `app.py:_connect()`, on a worker thread, after `tui.run()` | `hsql/cli.py:_connect()`, inline |
| connection closed | `app.py:action_quit()` | never explicitly; the process exits |
| cache key | `adapter.connection_id` or `get_connection_hash(conn_str, config)` (`cli.py:553`) | n/a |

Four existing facts the design leans on:

- **One invocation, one adapter.** `first_pass()` already settles which one, before click
  parses anything, and `attach_adapter_options()` puts only that adapter's options on the
  command. So at the moment the rewrite has to happen, the adapter class is in hand and its
  `ADAPTER_OPTIONS` can be inspected.
- **A command's own click params are what claim a profile key.** `parse_profile_options()`
  takes `command_options` from `{param.name for param in cmd.params}`; anything else in the
  profile must be a declared option of the adapter. New `--ssh-*` options on both commands
  therefore become valid profile keys and JSON-schema keys with no further plumbing.
- **`AbstractOption` already carries semantics core acts on.** `secret=True` is the
  precedent: core cannot enumerate what is sensitive across every adapter, so the adapter
  declares it and one module acts on it.
- **`harlequin.redact` already parses DSNs**, for the password inside them, with a
  name-based backstop for the adapters that predate the declaration. The endpoint rewrite
  needs the same two moves against the same strings.

## 3. The shape

```
                    spec (CLI + profile)
                            |
             harlequin.tunnel.resolve()  ──► RewritePlan   (pure; no I/O)
                            |                    │
                 Tunnel.start()  ──► Endpoint    │  what to dial, and where it is written
                            |                    ▼
                            └────────► rewritten conn_str + options
                                                 |
                                    adapter_cls(conn_str, **options)
```

Three pieces, in a new `harlequin/tunnel.py` (plus `harlequin/dsn.py`, §5.3):

```python
@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int

class Tunnel(ABC):
    """Something that makes one remote endpoint reachable on localhost."""

    @abstractmethod
    def start(self, remote: Endpoint) -> Endpoint:
        """Bring the forward up and return the local endpoint. Blocks until it
        is accepting connections. Raises HarlequinTunnelError."""

    def stop(self) -> None:
        return None

    def __enter__(self) -> Tunnel: ...
    def __exit__(self, *exc: Any) -> None: self.stop()
```

`resolve()` is pure and is where all the interesting behavior is; the backends are small.

## 4. The forwarder

### 4.1 `SubprocessTunnel` — the only backend in v1

```
ssh -N -T -o ExitOnForwardFailure=yes -L 127.0.0.1:54321:db.internal:5432 bastion
```

- The local port is chosen by binding `127.0.0.1:0`, reading the port, and closing the
  socket, then handing that number to `ssh`. There is a race; it is the race every tool
  that does this has, and `ExitOnForwardFailure=yes` turns losing it into a clean error
  rather than a silent no-forward. `--ssh-local-port` pins it for anyone who cares.
- The child stays in the **foreground** (no `-f`) with the terminal attached, so
  `Enter passphrase for key ...` and a 2FA prompt reach the human. Readiness is a
  connect-poll against the local port with `--tunnel-timeout` (default 10s), which also
  notices the child exiting; on failure, the child's stderr is what the error message
  quotes, verbatim.
- Teardown is `terminate()` then `kill()`, from a `contextlib.ExitStack` that wraps
  `tui.run()` and the `hsql` run, plus an `atexit` backstop.

The user's `~/.ssh/config` is read by `ssh` itself. `--ssh bastion` gets `HostName`, `User`,
`Port`, `IdentityFile`, `ProxyJump`, `Match` — all of it, correct, for free, and matching
whatever the user already tests with `ssh bastion`. This is the single strongest argument
for the subprocess: **the configuration surface is not ours to reimplement or to document.**

### 4.2 `--tunnel-command` — the same backend, for everything that is not SSH

```bash
hsql --tunnel-command 'cloud-sql-proxy --port {local_port} my-proj:us-central1:pg' -a postgres
hsql --tunnel-command 'aws ssm start-session --target i-0abc --document-name AWS-StartPortForwardingSessionToRemoteHost --parameters host={remote_host},portNumber={remote_port},localPortNumber={local_port}'
hsql --tunnel-command 'kubectl port-forward svc/pg {local_port}:{remote_port}'
```

Placeholders: `{local_port}`, `{remote_host}`, `{remote_port}`. Same lifecycle, same
readiness poll, same teardown; `--ssh` is sugar over it with an argv we build.

This is ~40 lines on top of the SSH backend and it covers the Cloud SQL request on the
issue, every corporate access proxy, and the next one nobody has heard of yet. It is also
what makes the whole feature **testable in CI without an SSH server** (§10).

### 4.3 What v1 does not do

One hop and one endpoint. Multiple forwards over one `ssh` connection is a one-line
extension (`-L` repeats) and can wait for someone who wants it. No reconnect: `ssh` gets
`-o ServerAliveInterval=30`, and a dead tunnel surfaces as the adapter's own connection
error, which is already an error modal in the IDE and exit 3 in `hsql`.

## 5. The rewrite — the part that has to work with any adapter

`resolve()` answers two questions with no I/O: **what remote endpoint is this invocation
about to dial**, and **where do I write the local one so the adapter uses it**. It returns
a plan, which the CLI applies after `Tunnel.start()`:

```python
@dataclass(frozen=True)
class RewritePlan:
    remote: Endpoint
    sites: tuple[Site, ...]      # option pair, or a span inside a conn_str
    described: str               # "--host/--port", "conn_str[0]" -- what we print
```

Three sources, in precedence order. All of them are consulted; if two name **different**
remote endpoints, that is an error naming both, resolved by `--tunnel-remote HOST:PORT`.

### 5.1 Declared roles (new, precise, opt-in)

An additive keyword on `AbstractOption`, exactly parallel to `secret=`:

```python
TextOption(name="host", ..., role="host")
TextOption(name="port", ..., role="port")
```

- `role: ClassVar[str] = ""` as a class attribute and a ctor kwarg, so a third-party
  subclass that predates it still answers (the `getattr` move `to_dict()` already makes).
- Reported by `to_dict()`, so it shows up in `hsql --spec` and the generated config schema.
- Nothing in core requires it. It is how an adapter with an unguessable spelling
  (`--server`, `--endpoint`, `--bootstrap-servers`) opts into being tunneled correctly, and
  the in-tree adapters have nothing to declare.

### 5.2 The name backstop (works with every adapter installed today)

When no option declares a role, core matches option names — `host`, `hostname`, `server`,
`address` paired with `port` — the same way `redact._SECRET_NAME` matches password-ish keys,
and for exactly the same reason: **every adapter's options predate the declaration**, and an
adapter the user installed last year is the whole point of the feature.

This is a guess, so it is guarded three ways:

1. It only runs when the user asked for a tunnel. Nothing changes for anyone else.
2. It only matches options **the invocation actually set** (CLI or profile). An unset option
   is not evidence of anything.
3. **It prints what it did.** `hsql` on stderr, the IDE as a notification and on the debug
   screen:
   ```
   tunnel: forwarding db.internal:5432 -> 127.0.0.1:54321 (ssh bastion); rewrote --host, --port
   ```
   Visibility is the mitigation for a wrong guess, and it is also the thing that tells a
   user their adapter needs `role=` upstream.

### 5.3 Inside `conn_str`

Adapters that take a DSN (`postgres://user:pw@db.internal:5432/app`) carry the endpoint
positionally, where no option describes it. Core rewrites the host and port **in place** for
two forms:

- URI-shaped: `scheme://[user[:pass]@]host[:port][/...]`
- libpq keyword-shaped: `host=db.internal port=5432 dbname=app`

Anything else is refused with a message pointing at `--tunnel-remote` and the adapter's own
host option. `redact.py` already has regexes for the second thing it finds in these strings;
this proposes lifting the parse into `harlequin/dsn.py` and having both callers use it, so
"where is the host in a DSN" and "where is the password in a DSN" cannot drift apart. (The
existing precedent for two implementations of one string question is
`export._deduplicate_column_names()`, and AGENTS.md is not fond of it.)

### 5.4 When there is nothing to rewrite

DuckDB, SQLite, and any adapter with no network endpoint: `resolve()` finds no site, and the
invocation **fails** with a usage error rather than silently connecting around the tunnel.

```
hsql: no host to forward for adapter 'duckdb'.
      This adapter declares no host option and the connection string names no host.
      Use --tunnel-remote HOST:PORT if you meant to forward something else.
```

Failing loudly matters: the silent version connects straight to a database the user believed
was reachable only through a bastion.

## 6. The CLI and config surface

New options on **both** commands (a profile serves both, and `hsql` is where an agent or a
cron job needs this most). All are `ssh_*`/`tunnel_*` profile keys with the same spelling.

| option | meaning |
|---|---|
| `--ssh [user@]host[:port]` | the jump host, as `ssh` would take it; also an alias from `~/.ssh/config` |
| `--ssh-identity PATH` | `-i` |
| `--ssh-option KEY=VALUE` | repeatable `-o` passthrough; the escape hatch for everything not listed |
| `--tunnel-command TEXT` | §4.2; mutually exclusive with `--ssh` |
| `--tunnel-remote HOST:PORT` | name the remote endpoint instead of discovering it |
| `--tunnel-local-port INT` | pin the local port (default: ephemeral) |
| `--tunnel-timeout FLOAT` | seconds to wait for the forward (default 10) |

Seven spellings, namespaced. `hsql`'s flags are the frozen part of its API, so these join the
reserved set in `first_pass.attach_adapter_options()`: an adapter that declares `--ssh-host`
today loses that spelling and keeps the option under its profile key, visibly, in `--help`.
(Worth a survey of published adapters before merging; I know of none that claims one.)

No password flags. `--ssh-password` would be a credential in `ps` output and in shell
history for a case `ssh` already handles better with keys, an agent, or its own prompt. If
one is added later it is `secret=True` and `redact._SECRET_NAME` grows `passphrase`, which it
is missing today.

In a config file, unremarkably:

```toml
[profiles.prod]
adapter = "postgres"
host = "db.internal"
port = 5432
dbname = "app"
ssh = "bastion.example.com"
```

Both commands pick this up through the existing profile merge; `config_schema.py` generates
the keys from click params, so `schemas/config-v1.json` regenerates with
`scripts/write_config_schema.py` and the schema test keeps them honest.

## 7. Ordering, and why the tunnel comes up before Textual

```
first_pass -> click parses -> profile merge -> resolve()  [pure]
                                                  |
                                            Tunnel.start()      <-- prompts happen HERE
                                                  |
                                            apply the plan
                                                  |
                              adapter_cls(...) -> tui.run() / hsql runs
```

The IDE opens its database on a worker thread after the app is running, which is right for a
database and wrong for a tunnel: once Textual owns the terminal, `ssh` cannot ask for a
passphrase, and a 2FA push has nowhere to print "check your phone". Starting the tunnel in
the click callback also means one error path for both commands —
`pretty_print_error` / `diagnostics.report_error`, exit code 3 (`ExitCode.CONNECTION`) —
before a single widget is mounted.

The cost is that `harlequin --ssh ...` authenticates before showing a UI, which is what every
CLI that tunnels does, and is what the user expects from having typed an SSH flag.

## 8. Two things that break quietly if we forget them

**Cache keys.** `get_connection_hash(conn_str, config)` is computed from the config dict, and
`adapter.connection_id` is usually a hydrated connection string. Hash the **pre-rewrite**
values, or an ephemeral local port changes the key on every run and the user silently loses
query history and the catalog cache. Concretely: compute the hash in `cli.py` before applying
the plan, and when a tunnel is active prefer it over `adapter.connection_id` (which will have
`127.0.0.1:54321` baked into it). The ssh destination joins the hashed material, so two
bastions to two databases do not collide.

**TLS.** An adapter told to connect to `127.0.0.1` will fail certificate hostname
verification under `sslmode=verify-full` and friends. Core cannot fix this generically —
libpq has `host`/`hostaddr` for exactly this, other drivers have nothing — so it is
documented, and the rewrite notice is the place a user finds out why their `verify-full`
connection started failing.

## 9. Dependencies, imports, packaging

**v1 adds no dependency at all.** `subprocess`, `socket`, `shlex`.

`harlequin/tunnel.py` and `harlequin/dsn.py` import no Textual and no adapter; both join the
"adapter API is reachable without the TUI" import-linter contract, and `harlequin.tunnel` is
imported from the CLI callbacks only when a tunnel was asked for, so an invocation without
one pays nothing (`scripts/cold_start.py` should show no change).

**When paramiko would earn its place** — as `harlequin[ssh]`, a second `Tunnel`
implementation, no change anywhere else:

- Windows users without OpenSSH (it ships with Windows 10+, but is removable and absent from
  many CI images).
- Containers: this repo's own dev container has no `ssh` binary, and neither does the
  `python:3.x-slim` image most people build on.
- Anything needing in-process sockets rather than a listener — which is also the shape the
  Google Cloud SQL *connector* (as opposed to its proxy binary) wants, and the one thing
  `--tunnel-command` genuinely cannot express.

Until one of those has a user asking, the argument on the issue stands: an unmaintained
wrapper is not worth taking, and a maintained one is still a cryptography dependency for
every Harlequin install to solve a problem `ssh` already solves on the same machine.

## 10. Testing

**Unit, no network** — `resolve()` is a pure function and gets the table:

| adapter shape | conn_str | options set | expected |
|---|---|---|---|
| declares `role="host"`/`"port"` | — | `--server`, `--port` | rewrites both |
| `host`/`port` names only | — | `--host`, `--port` | rewrites both, with a notice |
| DSN, URI form | `postgres://u@db:5432/app` | — | rewrites the span |
| DSN, libpq form | `host=db port=5432` | — | rewrites both keys |
| both, agreeing | DSN + `--host` | | rewrites both sites |
| both, disagreeing | | | `HarlequinTunnelError`, names both |
| duckdb / sqlite | file path | | usage error, §5.4 |
| DSN we cannot parse | `weird::thing` | | usage error naming `--tunnel-remote` |

**End to end, no SSH server.** `--tunnel-command` makes the whole path testable with a
python subprocess as the forwarder:

```python
--tunnel-command "{sys.executable} -m tests.tcp_forward {local_port} {remote_host} {remote_port}"
```

A ~30-line loopback TCP forwarder in `tests/`, a real SQLite-over-TCP or a stub server on the
far side, and the test asserts the adapter actually reached through it, that the local port
was substituted, and that the child is dead after the command exits. Runs on all three OSes,
needs no `online` marker, no secrets, no `ssh`.

**The SSH argv** is asserted as data (`_ssh_argv(spec, remote, local) == [...]`) rather than
by running it. One test does run `ssh -V` and skips if absent, to prove the built argv is at
least accepted by a real client where one exists.

## 11. Public API impact

Additive only; every out-of-tree adapter keeps working untouched:

- `AbstractOption.role` — new class attribute + ctor kwarg + `to_dict()` key. Adapters that
  never set it are handled by §5.2.
- `HarlequinTunnelError(HarlequinError)` in `exception.py`.
- No change to `HarlequinAdapter`, `HarlequinConnection`, `HarlequinCursor`, `catalog.py` or
  `driver.py`. **An adapter does not know it is tunneled**, which is the property that makes
  this work for adapters nobody in this org maintains.

## 12. Alternatives considered

| | why not |
|---|---|
| **Each adapter grows `--ssh-*`** | N implementations of one thing, N spellings; the adapters that need it most are out-of-tree. Exactly the "workaround here is a permanent tax" case AGENTS.md warns about — except the tax is paid N times, by other people. |
| **A library adapters call (`harlequin-tunnel`)** | Adapters must opt in, so "works with any adapter" becomes "works with adapters that adopted it". Reasonable *in addition*, for an adapter that wants a tunnel inside its own connection (Cloud SQL connector); not a substitute. |
| **`sshtunnel`** | The blocker on the issue. Four years without a release, and it is a wrapper over paramiko that we would be depending on for ~150 lines of behavior. |
| **paramiko directly, in v1** | A real dependency (cryptography) on every install, and it reimplements the part of `~/.ssh/config` users already have working — `paramiko.SSHConfig` handles some of it, not `Match`, not certificates, not the agent story on Windows. Kept as the v2 backend, §9. |
| **Tell users to run `ssh -L` themselves** | It works, and it is what people do today. It also means two terminals, a remembered port, and a profile that only works if a human ran something first — which is precisely what `hsql` in a cron job or an agent loop cannot do. |
| **A nested `[profiles.x.ssh]` config table** | Prettier in TOML, but the CLI merge is flat by name (`merge_profile_with_cli`), so it would need a second mapping between flag names and config keys. Not worth it for seven keys. |

## 13. Phasing

1. **`harlequin/dsn.py`** — lift the DSN parse out of `redact.py`, no behavior change, tests
   pin the existing redaction. Small, mergeable alone.
2. **`harlequin/tunnel.py`: `resolve()` + `RewritePlan`** — pure, plus `AbstractOption.role`
   and the `to_dict()` key. The §10 table is the whole test suite. No CLI yet.
3. **`SubprocessTunnel` + `--tunnel-command`** on both commands, with the lifecycle, the
   notice, the cache-key fix, and the loopback end-to-end test.
4. **`--ssh` and friends** — argv construction over PR 3, reserved spellings in
   `attach_adapter_options()`, `--info` / debug-screen reporting, regenerated config schema.
5. **Docs** in `tconbeer/harlequin-web`, and a `CHANGELOG.md` entry under `[Unreleased]`
   → Features, referencing [#545](https://github.com/tconbeer/harlequin/issues/545).

## 14. Open questions for review

1. **Is the name backstop (§5.2) acceptable?** It is what makes the feature work with the
   adapters people have installed right now, and it is a guess that prints itself. The strict
   alternative is `role=` only, which means the feature does nothing useful until every
   adapter ships a release.
2. **`--ssh` sugar in v1, or `--tunnel-command` alone?** The generic one is strictly more
   powerful and half the code; the sugar is what makes the feature discoverable and is what
   the issue asks for.
3. **Should `harlequin` (the IDE) offer to keep the tunnel across a reconnect** if a
   reconnect action is ever added? Nothing needs it today.
4. **Multiple `conn_str`s** are supported by the IDE. v1 forwards one endpoint and errors on
   two distinct ones. Is anyone attaching two remote databases through one bastion?
