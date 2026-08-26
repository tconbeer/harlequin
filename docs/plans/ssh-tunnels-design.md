# SSH tunnels — a design for [#545](https://github.com/tconbeer/harlequin/issues/545)

A proposal for connecting Harlequin and `hsql` to a database that is only reachable through
an SSH host, **with any adapter, including out-of-tree ones that are never changed**.

Nothing here is implemented. This is the design to argue with before the first PR.

---

## Bottom line up front

1. **Harlequin manages a tunnel; it does not touch connection details.** The user's
   connection details name the local end of a forward, and that is the whole contract. A
   profile that works today with `ssh -fN redshift_prod` running in another terminal works
   unchanged, with one key added.
2. **That is what makes it work with any adapter.** Core never learns which of an adapter's
   options is the host, never parses a DSN, and adds nothing to the adapter API. An adapter
   cannot tell it is tunneled, and one published four years ago works today.
3. **The forwarder is `ssh`, run as a child process.** **Zero new dependencies** — which
   answers the four-year-old blocker on the issue — and it inherits the user's whole
   `~/.ssh/config`: `LocalForward`, `ProxyJump`, agent, certificates, hardware keys, `Match`
   blocks, 2FA. `ssh -G` tells us what it is about to forward, so nothing is guessed.
4. **Five flags, all `--ssh-*`.** No generic `--tunnel-command` in v1 (§12), no
   `--ssh-user`/`--ssh-port`/`--ssh-identity` — those are `ssh_config` spelled twice.
5. **The tunnel comes up before Textual does**, so a passphrase or 2FA prompt can reach a
   human — and it is a child process, not `ssh -f`, so it dies with the session instead of
   outliving it.

What core owns is the **lifecycle**: bring the forward up before anything connects, report
what went wrong in the two commands' own error paths, tear it down on exit. That is the whole
of the feature.

---

## 1. What the issue asks for

> Need to think through whether this is an adapter feature or a harlequin feature. Maybe
> it's a library that adapters can use. Would be nice to provide CLI options for
> configuring an ssh tunnel, and then intercept/rewrite the host/port/etc options passed
> through to the adapter.

The maintainer's blocker (Feb 2025) was dependencies: `sshtunnel` looks perfect and has not
shipped in four years; paramiko was offered in reply as the maintained thing underneath it.
This design needs neither.

**The rewrite half of the issue is what it drops.** §12 has the reasoning; the short version
is that intercepting connection details means core guessing which of an adapter's options is
a host, and guessing wrong quietly.

## 2. Ten seconds of SSH, for the rest of this document

| | |
|---|---|
| `-L 15439:redshift.internal:5439` | **local forward.** Listen on `127.0.0.1:15439` here; anything that connects is carried over the SSH connection and delivered to `redshift.internal:5439` **as resolved by the SSH host**. |
| `LocalForward 15439 redshift.internal:5439` | The same directive, written in `~/.ssh/config` under a `Host` block instead of on the command line. |
| `-N` | Don't run a remote command. "I am here for the forwards." |
| `-f` | Fork into the background once the forwards are up. |
| `-J`, `ProxyJump` | Reach the destination through another SSH host first. Chains. |
| `-o KEY=VALUE` | Set any `ssh_config` keyword for this invocation. Beats the config file. |
| `-o ExitOnForwardFailure=yes` | If a forward can't be set up, exit instead of connecting anyway. |
| `ssh -G host` | Print the resolved config — every `Host`/`Match` block applied, plus any flags on this command line — and connect to nothing. |

**`ssh -fN redshift_prod` and `ssh -L …` are not alternatives.** The forward in that command
is real; it is just written down somewhere else. `-f` and `-N` say *how to run*; `-L` and
`LocalForward` say *what to forward*, and they are one directive in two places.

That matters here: **a user who already tunnels has the forward in their ssh config**, and
asking them to repeat it on Harlequin's command line would be a step backwards. `--ssh-forward`
is optional, and §4.1 — the motivating case — never uses it.

## 3. The model

```
   ssh_host = "redshift_prod"    ───────►  ssh -N -o ExitOnForwardFailure=yes redshift_prod
                                           (the -L comes from ~/.ssh/config)

   -a postgres                             adapter dials 127.0.0.1:15439
   host = "localhost", port = 15439        the local end of `LocalForward 15439 …:5439`
```

One rule, and it is the whole contract:

**The connection details name the local end of the forward.** Harlequin runs the tunnel and
touches nothing else.

In the config behind that example (§4.1) the forward is `LocalForward 15439 <redshift>:5439`,
so the local end is `localhost:15439` and that is what the profile says. The ports differ,
and nothing in Harlequin needs to know or care. The far side's address appears once, in the
ssh config, where it already was.

Nothing is rewritten. `conn_str` and every adapter option reach the adapter exactly as the
user wrote them, and the adapter is never told a tunnel exists.

When the forward is *not* already in the ssh config, `--ssh-forward` writes one:

| `--ssh-forward` | becomes | |
|---|---|---|
| `15439:db.internal:5439` | `-L 127.0.0.1:15439:db.internal:5439` | the general form, and the one to recommend |
| `db.internal:5439` | `-L 127.0.0.1:5439:db.internal:5439` | shorthand: same port on both ends |
| `5432` | `-L 127.0.0.1:5432:localhost:5432` | shorthand: the database runs on the SSH host |

**The docs should recommend a distinct local port**, as the config in §4.1 does with `15439`.
It sidesteps the collision case, and it is most of the mitigation for the hazard below:
nothing else on a developer's laptop listens on 15439, so a profile run without its tunnel
fails to connect instead of quietly reaching a local database on 5432.

### What this model gives up, and what it buys

A local port that is already bound is a **hard error** (§5.2), not a workaround.

What it buys, beyond not guessing:

- **TLS still works the way the user asked for it.** Nothing silently changes the hostname the
  driver validates against.
- **`connection_id` and the catalog cache key stay stable across runs** (§7): no ephemeral
  port lands in the config the hash is taken over.
- **DuckDB and SQLite need no special case.** They ignore the tunnel; a duckdb `ATTACH` of
  `postgres://localhost:15439/prod` goes through it like anything else.

### The hazard this model does have

A profile that says `host = "localhost"` is only correct with the tunnel up. Run it without
`ssh_host` and it reaches whatever is on your machine at that port — which, if the forward
uses the database's own port number, may be a database, just the wrong one. A distinct local
port makes it a connection refused instead, and the `ssh_*` keys live in the same profile as
the connection details, so they travel together.

## 4. Worked examples

### 4.1 A Redshift cluster behind a bastion, with the forward already in `~/.ssh/config`

The motivating case — a real config, from this repo's author:

```
Host redshift_prod
  HostName web-1
  User tco
  LocalForward 15439 data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com:5439
  ServerAliveInterval 60
  ServerAliveCountMax 3
```

Today: `ssh -fN redshift_prod`, then `harlequin -a postgres --host localhost --port 15439 …`,
then remember to kill the ssh later.

```toml
[profiles.redshift]
adapter = "postgres"
host = "localhost"
port = 15439
dbname = "prod"
ssh_host = "redshift_prod"
```

`harlequin -P redshift`, and `hsql -P redshift -c "select 1"`. **One key.** No
`--ssh-forward`, no user, no identity file, no keepalive settings — the `Host` block says all
of it, and Harlequin runs `ssh -N -o ExitOnForwardFailure=yes redshift_prod`. The connection
details are exactly the ones that work today.

Note the ports: local `15439`, remote `5439`. The profile names the **local** end, which is
the only thing the contract says.

### 4.2 The same cluster, with nothing in `~/.ssh/config`

```bash
hsql -a postgres --host localhost --port 15439 --dbname prod \
     --ssh-host tco@web-1 \
     --ssh-forward 15439:data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com:5439 \
     -c "select 1"
```

The docs' advice will be to write the `Host` block instead and go back to 4.1 — but this
works, and it is what an agent or a CI job with no dotfiles has to do.

### 4.3 Postgres running on the bastion itself

```bash
harlequin -a postgres --host localhost --port 5432 --ssh-host bastion --ssh-forward 5432
```

`--ssh-forward 5432` is `-L 127.0.0.1:5432:localhost:5432`.

### 4.4 You already run a local Postgres on 5432

```bash
harlequin -a postgres --host localhost --port 15432 \
          --ssh-host bastion --ssh-forward 15432:db.internal:5432
```

The two ports agree because the user wrote both. Nothing infers anything.

### 4.5 Two databases through one bastion

```bash
harlequin -a postgres --ssh-host bastion \
          --ssh-forward 15432:pg.internal:5432 \
          --ssh-forward 15433:analytics.internal:5432 \
          "postgresql://me@localhost:15432/app" "postgresql://me@localhost:15433/analytics"
```

One `ssh` child, two `-L`s.

### 4.6 A jump host in front of the bastion

Nothing new. `ProxyJump jump.example.com` in the `Host` block, or
`--ssh-option ProxyJump=jump.example.com`. Chains of three work because `ssh` does them, not
because we do.

### 4.7 Cloud SQL and other proxies — works, but Harlequin does not run them

The issue thread asks for Cloud SQL, which is not SSH. In v1 the user runs the proxy
themselves and points at its local port:

```bash
cloud-sql-proxy --port 3306 my-proj:us-central1:my-instance &
hsql -a mysql --host 127.0.0.1 --port 3306 --database app -c "select 1"
```

That already works, and it works *because of* the contract in §3 — the details name a local
endpoint and Harlequin does not care what is behind it. What v1 does not do is manage that
process's lifetime the way it manages `ssh`'s. §12 says why that is deferred rather than
refused.

### 4.8 A key with a passphrase, or 2FA

```
$ hsql -P redshift -c "select count(*) from events"
Enter passphrase for key '/home/tco/.ssh/id_ed25519':
ssh: 127.0.0.1:15439 -> data-analytics…:5439 via redshift_prod
```

The prompt is `ssh`'s, on the real terminal, because the child starts before Textual (in the
IDE) and before any output (in `hsql`). An agent running `hsql` unattended uses a key with no
passphrase or an agent, exactly as it would for `ssh` itself.

### 4.9 Something that is not tunneled

`harlequin my.db`, `hsql -a postgres --host prod.example.com …`. No `ssh_host`, no
`harlequin.ssh` import, no cost.

## 5. The forwarder

`harlequin/ssh.py`, one class:

```python
class SshTunnel:
    """An `ssh` child process holding one or more local forwards open."""

    def start(self) -> None:
        """Start it and block until the forwarded ports accept connections.
        Raises HarlequinSshError, quoting ssh's stderr."""

    def stop(self) -> None: ...
    def __enter__(self) -> SshTunnel: ...
    def __exit__(self, *exc: Any) -> None: self.stop()
```

- **Foreground, not `-f`.** The child keeps the terminal, so prompts reach the human, and we
  keep the handle, so it can be killed. `ssh -fN` backgrounds itself and outlives the
  session — which is why the manual workflow needs a `kill` afterwards and this does not.
- **Readiness** is a connect-poll against each local port `ssh -G` reported (§5.1) until
  `--ssh-timeout` (default 10s), which also notices the child exiting early. On failure the
  error quotes ssh's stderr verbatim: its diagnostics are better than any we would write.
- **The only `-o` Harlequin imposes is `ExitOnForwardFailure=yes`**, because a forward that
  silently did not happen is the one failure a user cannot diagnose. Notably *not* imposed:
  `ServerAliveInterval`. A command-line `-o` beats the config file, and keepalives are exactly
  what a working `Host` block already sets (§4.1 sets them) — overriding would be Harlequin
  quietly retuning someone else's connection. The docs say to set them in the `Host` block;
  `--ssh-option` is there for anyone who wants to anyway.
- **Teardown** is `terminate()` then `kill()`, from a `contextlib.ExitStack` wrapping
  `tui.run()` and the `hsql` run, plus an `atexit` backstop.
- **No reconnect in v1, but the IDE says when the tunnel dies.** A thread waiting on the child
  posts a notification when it exits, so a session whose forward dropped shows
  `tunnel closed: <ssh's last line>` rather than an unexplained wall of query errors. `hsql`
  is short-lived enough that the adapter's own connection error is the whole story.

The `Tunnel` ABC an earlier draft proposed is not in v1. One implementation does not need an
abstract base; the second one (§9, §12) is when it earns itself.

### 5.1 Asking `ssh` what it is about to forward

In the 4.1 shape the forward lives in `ssh_config`, so Harlequin does not know the port it
should poll. `ssh -G` answers that: it applies every `Host` and `Match` block, prints the
resolved configuration, and connects to nothing. Verified against the motivating config:

```
$ ssh -G redshift_prod | grep -i forward
localforward 15439 [data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com]:5439
```

**Probe with the argv we are about to run**, not with the destination alone — `ssh -G` echoes
command-line `-L` and `-o` flags back along with the config's, so

```python
forwards = _parse_forwards(run([*argv, "-G"]))
```

gives one list whether the forward came from `--ssh-forward`, from a `Host` block, or from
both. There is no "config forwards" path and "flag forwards" path to keep in agreement,
because `ssh` merges them before we look.

Parsing is two whitespace-separated fields after the keyword, each `port`, `[host]:port`, or a
unix socket path; the brackets are how `ssh` writes a host, and they make IPv6 unambiguous. A
forward whose listen side is a socket path counts as a forward but is not polled.

Three things get an exact answer instead of a guess:

- **The readiness poll** watches the real local ports.
- **`--ssh-host` that forwards nothing** is a usage error that can say what it found:
  `redshift_prod resolves no LocalForward; add one to your Host block or pass --ssh-forward`
  — with a distinct message when the config has a `DynamicForward` (SOCKS) instead, which is a
  working tunnel the adapter cannot use.
- **The notice and the cache key** can name the far side even though Harlequin never saw it.

`ssh -G` costs one subprocess (~10–30ms, no network) on invocations that tunnel, and nothing
on the ones that do not. If it exits non-zero or prints something this cannot parse, Harlequin
degrades rather than fails: no poll, no forwards-nothing error, a short grace period, and the
adapter's own connection is the test.

### 5.2 A local port that is already bound

The default is to **fail**: `ExitOnForwardFailure=yes` means `ssh` exits with
`bind: Address already in use`, and Harlequin reports it. A port that is already answering is
not evidence that the right tunnel is behind it, and reaching the wrong database because
something else happened to be on 15439 is worse than an error at start-up.

`--ssh-reuse` (`ssh_reuse = true`) opts into the other behavior, for the person who keeps
`ssh -fN redshift_prod` running all day and does not want Harlequin fighting it: if **every**
local port `ssh -G` reported is already accepting connections, skip the child and say so.

```
ssh: 127.0.0.1:15439 already listening; reusing (--ssh-reuse)
```

If only some of them answer, that is a half-open state nobody meant, and it is an error naming
the ports either way.

## 6. The CLI and config surface

New options on **both** commands. A profile serves both, and `hsql` is where this matters
most: a cron job or an agent cannot ask a human to run `ssh -fN` in another terminal first.

| option | profile key | meaning |
|---|---|---|
| `--ssh-host TEXT` | `ssh_host` | `[user@]host[:port]`, or a `Host` alias from `~/.ssh/config` |
| `--ssh-forward TEXT` | `ssh_forward` | repeatable; `PORT`, `HOST:PORT`, or `LOCAL:HOST:PORT`. Omit when `ssh_config` has it |
| `--ssh-option KEY=VALUE` | `ssh_option` | repeatable `-o`; `IdentityFile`, `ProxyJump`, `User`, anything |
| `--ssh-reuse` | `ssh_reuse` | if the forwarded ports already answer, use them instead of starting a child (§5.2) |
| `--ssh-timeout FLOAT` | `ssh_timeout` | seconds to wait for the forwards (default 10) |

Five flags. An earlier draft had nine, including `--ssh-user`, `--ssh-port` and
`--ssh-identity`; every one of those is `ssh_config` or `-o` spelled a second time, and the
docs' advice is to write a `Host` block — reusable outside Harlequin, and where a user's
`ProxyJump`, certificate and `Match` rules already live.

No password flag. `--ssh-password` would be a credential in `ps` output and in shell history,
for a case `ssh` already handles better with a key, an agent, or its own prompt. If one is
ever added it is `secret=True`, and `redact._SECRET_NAME` grows `passphrase`, which it is
missing today.

`hsql`'s flags are the frozen part of its API, so these join the reserved spellings in
`first_pass.attach_adapter_options()`. Worth a survey of published adapters before merging; I
know of none that claims one.

**The start-up notice**, on `hsql`'s stderr (never stdout — that belongs to query output) and
as an IDE notification and debug-screen line:

```
ssh: 127.0.0.1:15439 -> data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com:5439 via redshift_prod
```

One line, and it is what tells a user which database they are actually looking at.

### 6.1 A config file must not be able to run a command

Config files are discovered in the **working directory**, including `pyproject.toml`. Clone a
repository, run `harlequin` in it, and today the worst a hostile config can do is name a
database.

Four of the five keys above are names, ports and a flag. `ssh_option` is not: `-o
ProxyCommand=…` runs a command, and so does `LocalCommand` with `PermitLocalCommand`.

Proposed rule: **`ssh_option` is honored only from the user's own config** — an explicit
`--config-path`, the user config dir, or `$HOME` — and from the command line. A profile
supplied by a file found in the working directory that sets it is a config error naming the
file. `config.py` already tracks which file supplied each profile (`Provenance`), so the check
has what it needs.

This is also the reason a general "run this command" option is a bigger decision than it
looks, and part of why there isn't one in v1 (§12).

## 7. Ordering, cache keys, and the two commands

```
first_pass -> click parses -> profile merge -> SshTunnel.start()   <-- prompts happen HERE
                                                     |
                                        adapter_cls(...) -> tui.run() / hsql runs
                                                     |
                                              ExitStack unwinds -> stop()
```

The IDE opens its database on a worker thread after the app is running, which is right for a
database and wrong for a tunnel: once Textual owns the terminal, `ssh` cannot ask for a
passphrase and a 2FA push has nowhere to print "check your phone". Starting the child in the
click callback also means one error path for both commands — `pretty_print_error` /
`diagnostics.report_error`, exit code 3 (`ExitCode.CONNECTION`) — before a widget is mounted.

The cost is that `harlequin -P redshift` authenticates before showing a UI, which is what
every CLI that tunnels does and what a user who configured one expects.

**Cache keys need one small change.** Nothing is rewritten, so `get_connection_hash()` and
`adapter.connection_id` are already stable across runs — but two bastions fronting two
databases both look like `localhost:15439`, and would share a catalog cache and a query
history. The ssh destination and the resolved forwards join the hashed material.

## 8. Public API impact

**None.** No change to `HarlequinAdapter`, `HarlequinConnection`, `HarlequinCursor`,
`AbstractOption`, `catalog.py` or `driver.py`. One new `HarlequinSshError(HarlequinError)` in
`exception.py`, and one new module, `harlequin/ssh.py`.

That an adapter cannot tell it is tunneled is not an accident — it is the property being
bought, and it is why this works for adapters nobody in this org maintains.

## 9. Dependencies, imports, packaging

**v1 adds no dependency at all**: `subprocess`, `socket`, `shlex`.

`harlequin/ssh.py` imports no Textual and no adapter, and joins the "adapter API is reachable
without the TUI" import-linter contract. It is imported from the CLI callbacks only when
`ssh_host` is set, so 4.9 pays nothing — `scripts/cold_start.py` should show no change.

**When paramiko would earn its place** — as `harlequin[ssh]`, a second implementation behind
whatever interface the two then share:

- Windows users without OpenSSH. It ships with Windows 10+, but is removable.
- Containers: this repo's own dev container has no `ssh` binary, and neither does the
  `python:3.x-slim` image most people build on.
- In-process sockets rather than a listener — the shape Google's Cloud SQL *connector* wants.

Until one of those has a user asking, the argument on the issue stands: an unmaintained
wrapper is not worth taking, and a maintained one is still a `cryptography` dependency on
every Harlequin install, to solve a problem `ssh` already solves on the same machine.

## 10. Testing

No SSH server, and no `online` marker.

**Lifecycle, all three OSes.** `SshTunnel` takes an argv and a list of ports, so a test can
hand it a Python child instead of `ssh`:

```python
SshTunnel(argv=[sys.executable, "-m", "tests.tcp_forward", "15439", "127.0.0.1", str(port)],
          ports=[15439])
```

A ~30-line loopback TCP forwarder in `tests/` and a stub server on the far side cover start,
poll, reuse, timeout, the death notification, and teardown.

**End to end, Unix.** A fake `ssh` on `PATH` — a Python script that understands `-G` and `-L`
well enough to print a `localforward` line and then forward — exercises the real CLI path
including the `-G` probe. Skipped on Windows, where the lifecycle tests above are the
coverage.

**Unit, no processes:**

| case | expected |
|---|---|
| `--ssh-forward 5432` | argv contains `-L 127.0.0.1:5432:localhost:5432` |
| `--ssh-forward db.internal:5439` | `-L 127.0.0.1:5439:db.internal:5439` |
| `--ssh-forward 15439:db.internal:5439` | `-L 127.0.0.1:15439:db.internal:5439` |
| `--ssh-forward` repeated | one child, two `-L` |
| `-G` line `localforward 15439 [host]:5439` | local 15439, remote host:5439 |
| `-G` with a bind address, IPv6, a socket path | parsed; the socket path is not polled |
| `-G` reports only `dynamicforward` | usage error, distinct message |
| `-G` exits non-zero or prints garbage | no poll, no forwards-nothing error, still starts |
| `--ssh-host` alone, no forward anywhere | usage error naming both places |
| a garbage forward spec | usage error naming the three forms |
| `ssh_option` from a cwd-discovered config | config error naming the file (§6.1) |
| child exits during the poll | `HarlequinSshError` quoting its stderr, exit 3 |
| poll times out | ditto, naming `--ssh-timeout` |
| profile round trip | `ssh_host`/`ssh_forward` survive the merge and reach the argv |
| cache key | two ssh hosts, same conn_str, two different hashes |

One test runs `ssh -V` and skips if absent, to prove the built argv is accepted by a real
client where one exists.

## 11. Phasing

1. **`harlequin/ssh.py`** — `SshTunnel`, argv construction, the `-G` probe and parser, the
   readiness poll, `HarlequinSshError`. Unit and lifecycle tests; no CLI. Mergeable alone.
2. **Wire it into both commands** — the five options, the `ExitStack`, the notice, the
   cache-key change, the §6.1 provenance rule, and the end-to-end test.
3. **`--info` / debug-screen reporting**, the IDE's tunnel-died notification, regenerated
   config schema.
4. **Docs** in `tconbeer/harlequin-web`: the "details name the local end" contract, and the
   `Host` block as the recommended way to configure a tunnel. Plus a `CHANGELOG.md` entry
   under `[Unreleased]` → Features, referencing
   [#545](https://github.com/tconbeer/harlequin/issues/545).

## 12. Alternatives considered

| | why not |
|---|---|
| **Rewriting the adapter's host/port** (the issue's original sketch, and the first draft of this doc) | Core has to know which option is the host, and there is no general answer: a new `role=` declaration no published adapter has, a name-matching backstop for `host`/`port` that is a guess, and an in-place DSN rewrite for adapters that take a positional conn_str — three mechanisms, each with a way to be quietly wrong, to reach where a local forward reaches with none. It also bakes an ephemeral port into `connection_id` (losing the catalog cache every run) and breaks TLS hostname verification without the user having asked for it. |
| **A generic `--tunnel-command`** (`cloud-sql-proxy`, `aws ssm`, `kubectl port-forward`, Teleport) | Deferred, not refused. It is ~30 lines over `SshTunnel` and it is the natural home for the Cloud SQL request — but it is a "run this string" option, which needs the §6.1 rule to be airtight before it ships, and 4.7 already works without Harlequin managing the process. Nothing in v1 forecloses it: the flags are namespaced `--ssh-*` precisely so a `--tunnel-*` family can arrive beside them. |
| **A `{tunnel_port}` placeholder** the user writes into their conn_str | General and explicit, and it survives any option spelling — but it is a second thing to learn for the same result, and it makes a profile unusable *without* the tunnel rather than merely wrong. |
| **A SOCKS proxy (`ssh -D`) with `socket.socket` patched** | The one design where the driver keeps the real hostname, so TLS verification is untouched. It only works for pure-Python drivers: psycopg2, mysqlclient, ODBC and duckdb's extensions open sockets in C and never see Python's `socket` module. |
| **Each adapter grows `--ssh-*`** | N implementations of one thing, N spellings, and the adapters that need it most are out-of-tree. |
| **A library adapters call (`harlequin-tunnel`)** | Adapters must opt in, so "works with any adapter" becomes "works with adapters that adopted it". Reasonable *in addition*, for an adapter that wants a tunnel inside its own connection (the Cloud SQL connector); not a substitute. |
| **`sshtunnel`** | The blocker on the issue: four years without a release, and it is a wrapper over paramiko for behavior we would otherwise get from a binary already on the machine. |
| **paramiko directly, in v1** | A `cryptography` dependency on every install, and it reimplements the part of `~/.ssh/config` users already have working. Kept as the v2 backend, §9. |
| **Tell users to run `ssh -fN` themselves** | It works, and it is what people do today — including the author of this repo. It also means a second terminal, an orphaned tunnel to remember to kill, and a profile that only connects if a human ran something first, which is exactly what `hsql` in a cron job or an agent loop cannot do. |

## 13. Settled in review

- **No rewriting of connection details.** They name the local end of the forward (§3).
- **`ssh -G` reports `localforward`**, verified against the config in §4.1, so the readiness
  poll and the forwards-nothing error are exact (§5.1).
- **A bound local port fails**; `--ssh-reuse` is the opt-in (§5.2).
- **Every option starts with `ssh`.** Five of them.
- **No `--tunnel-command` in v1**, and no `require_tunnel` profile key.

## 14. Open questions for review

1. **Should the local bind address be configurable?** `127.0.0.1` is proposed and hardcoded
   for `--ssh-forward`; `0.0.0.0` would expose the far side's database to the user's network,
   which seems like a thing to make people ask for. (`ssh_config` can already do it, which is
   an argument for leaving the flag simple.)
2. **`ssh_option` in a project-local config** — §6.1 proposes refusing it there. The
   alternative is refusing `ssh_option` from *any* config file, CLI only, which is simpler to
   explain and to test but would stop someone putting `ProxyJump` in their own
   `~/.config/harlequin/config.toml`.
