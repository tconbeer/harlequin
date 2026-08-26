# Tunnels — a design for [#545](https://github.com/tconbeer/harlequin/issues/545)

A proposal for connecting Harlequin and `hsql` to a database that is only reachable through
an SSH bastion, **with any adapter, including out-of-tree ones that are never changed**.

Nothing here is implemented. This is the design to argue with before the first PR.

---

## Bottom line up front

1. **Harlequin manages a tunnel; it does not touch connection details.** The user gives the
   adapter the address the database has **from the SSH host's side**, and the tunnel makes
   that address true on this machine. A conn_str of `postgresql://me@localhost:5439/prod`
   needs no rewriting — it is already pointing at the tunnel.
2. **That is what makes it work with any adapter.** Core never learns which of an adapter's
   options is the host, never parses a DSN, and adds nothing to the adapter API. An adapter
   cannot tell it is tunneled, and one published four years ago works today.
3. **The forwarder is a subprocess, not a library.** `ssh`, plus a generic
   `--tunnel-command` for what is not SSH (`cloud-sql-proxy`, `aws ssm`,
   `kubectl port-forward`, Teleport). **Zero new dependencies** — which answers the
   four-year-old blocker on the issue — and it inherits the user's whole `~/.ssh/config`:
   `LocalForward`, `ProxyJump`, agent, certificates, hardware keys, `Match` blocks, 2FA.
4. **SSH is a special case of `--tunnel-command` and still earns its own flags** — three of
   them, not nine. The deciding argument is not ergonomics: `ssh_host = "redshift_prod"` in
   a profile is a *name*, while `tunnel_command = "…"` is a shell command, and a
   `pyproject.toml` in a cloned repository must not be able to run one (§6.1).
5. **The tunnel comes up before Textual does**, so an SSH password, a key passphrase or a
   2FA prompt can reach a human — and it is a child process, not `ssh -f`, so it dies with
   the session instead of outliving it.

What core owns is the **lifecycle**: bring the forward up before anything connects, report
what went wrong in the two commands' own error paths, tear it down on exit. That is the
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

**The rewrite half of the issue is what this design drops.** §12 has the reasoning; the short
version is that intercepting connection details means core guessing which of an adapter's
options is a host, and guessing wrong quietly.

## 2. Ten seconds of SSH, for the rest of this document

| | |
|---|---|
| `-L 5439:redshift.internal:5439` | **local forward.** Listen on `127.0.0.1:5439` here; anything that connects is carried over the SSH connection and delivered to `redshift.internal:5439` **as resolved by the SSH host**. |
| `LocalForward 5439 redshift.internal:5439` | The same thing, written in `~/.ssh/config` under a `Host` block instead of on the command line. |
| `-N` | Don't run a remote command. "I am here for the forwards." |
| `-f` | Fork into the background once the forwards are up. |
| `-J`, `ProxyJump` | Reach the destination through another SSH host first. Chains. |
| `-o KEY=VALUE` | Set any `ssh_config` keyword for this invocation. |
| `-o ExitOnForwardFailure=yes` | If a forward can't be set up (port already in use, remote refuses), exit instead of connecting anyway. |
| `ssh -G host` | Print the resolved config for `host` — every `Host`/`Match` block applied — and connect to nothing. |

**`ssh -fN redshift_prod` and `ssh -L …` are not alternatives.** The forward in that command
is real, it is just written down somewhere else: a `Host redshift_prod` block in
`~/.ssh/config` with a `LocalForward` line in it. `-f` and `-N` say *how to run*; `-L` and
`LocalForward` say *what to forward*, and they are the same directive in two places.

This matters for the design: **a user who already tunnels probably has the forward in their
ssh config**, and asking them to repeat it on Harlequin's command line would be a step
backwards. So `--ssh-forward` is optional, and §4.1 is the case where it is not used at all.

## 3. The model

```
   --ssh-host redshift_prod    ───────►  ssh -N -o ExitOnForwardFailure=yes redshift_prod
                                         (the -L comes from ~/.ssh/config)

   -a postgres                            adapter dials 127.0.0.1:15439
   "postgresql://tco@localhost:15439/prod" the local end of `LocalForward 15439 …:5439`
```

One rule, and it is the whole contract:

**The connection details name the local end of the forward.** Harlequin runs the tunnel and
touches nothing else.

That is it. In the config behind that example (§4.1) the forward is `LocalForward 15439 <redshift>:5439`, so the
local end is `localhost:15439` and that is what the profile says — the ports differ, and
nothing in Harlequin needs to know or care. The far side's address appears once, in the ssh
config, where it already was.

"Give the address as the SSH host sees it" is a useful mnemonic for the `--ssh-forward 5432`
shorthand below, where the local and remote ports match. It is not the rule. The rule is the
local end.

Nothing is rewritten. `conn_str` and every adapter option reach the adapter exactly as the
user wrote them, and the adapter is never told a tunnel exists.

The forward can come from either place, and Harlequin does not care which:

| where | how |
|---|---|
| `~/.ssh/config` | a `LocalForward` line in the `Host` block. Harlequin passes no `-L`. |
| Harlequin | `--ssh-forward`, repeatable, in one of three forms below |

| `--ssh-forward` | becomes | |
|---|---|---|
| `15439:db.internal:5439` | `-L 127.0.0.1:15439:db.internal:5439` | the general form, and the one to recommend |
| `db.internal:5439` | `-L 127.0.0.1:5439:db.internal:5439` | shorthand: same port on both ends |
| `5432` | `-L 127.0.0.1:5432:localhost:5432` | shorthand: the database runs on the SSH host |

**The docs should recommend a distinct local port**, as the config above does with `15439`.
It sidesteps the collision case entirely, and it is most of the mitigation for the hazard
below: nothing else on a developer's laptop is listening on 15439, so a profile run without
its tunnel fails to connect instead of quietly reaching a local database on 5432.

### What this model gives up, and what it buys

A local port collision is a **hard error**, not a workaround: `ExitOnForwardFailure=yes` means
`ssh` exits saying `bind: Address already in use`, and the user picks a local port with the
three-part form. That is the honest failure; quietly binding somewhere else is the failure
mode that makes people distrust the feature. (§13 asks whether an already-listening port
should instead be taken as "the tunnel is already up" — it is the one case where this bites a
user who does what our own reader did for years.)

What it buys, beyond not guessing:

- **TLS still works the way the user asked for it.** Nothing silently changes the hostname the
  driver validates against.
- **`connection_id` and the catalog cache key stay stable across runs** (§7), because no
  ephemeral port lands in the config the hash is taken over.
- **DuckDB and SQLite need no special case.** They ignore the tunnel; a duckdb `ATTACH` of
  `postgres://localhost:5432/app` goes through it like anything else.

### The hazard this model does have

A profile that says `host = "localhost"` is only correct with the tunnel up. Run it without
`ssh_host` and it connects to whatever is on **your** machine at that port — which, if the
forward uses the database's own port number, may well be a database, just the wrong one. A
distinct local port makes this a connection refused instead.

The `ssh_*` keys live in the same profile as the connection details, so they travel together
and the failure needs someone to have deliberately overridden one; the start-up notice (§6)
says what was forwarded. If it proves to bite, a `require_tunnel = true` profile key can
refuse to connect without one. Not proposed for v1.

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
`--ssh-forward`, no `--ssh-user`, no `--ssh-identity`, no keepalive settings — the `Host`
block says all of it, and Harlequin runs `ssh -N -o ExitOnForwardFailure=yes redshift_prod`.
The connection details are exactly the ones that work today.

Note the ports: local `15439`, remote `5439`. The profile names the **local** end, which is
the only thing the contract says.

### 4.2 The same cluster, with nothing in `~/.ssh/config`

```bash
hsql -a postgres --host localhost --port 15439 --dbname prod \
     --ssh-host tco@web-1 \
     --ssh-forward 15439:data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com:5439 \
     -c "select 1"
```

The advice in the docs will be to write the `Host` block instead and go back to 4.1 — but
this works, and it is what an agent or a CI job with no dotfiles has to do.

### 4.3 Postgres running on the bastion itself

```bash
harlequin -a postgres --host localhost --port 5432 --ssh-host bastion --ssh-forward 5432
```

`--ssh-forward 5432` is `-L 127.0.0.1:5432:localhost:5432`: the short form exists because
this is the most common shape.

### 4.4 You already run a local Postgres on 5432

```bash
harlequin -a postgres --host localhost --port 15432 \
          --ssh-host bastion --ssh-forward 15432:db.internal:5432
```

The two ports agree because the user wrote both. Nothing infers anything.

### 4.5 Two databases through one bastion

```bash
harlequin -a postgres --ssh-host bastion \
          --ssh-forward 5432:pg.internal:5432 \
          --ssh-forward 15432:analytics.internal:5432 \
          "postgresql://me@localhost:5432/app" "postgresql://me@localhost:15432/analytics"
```

One `ssh` child, two `-L`s.

### 4.6 A jump host in front of the bastion

Nothing new. `ProxyJump jump.example.com` in the `Host` block, or
`--ssh-option ProxyJump=jump.example.com`. Chains of three work because `ssh` does them, not
because we do.

### 4.7 Cloud SQL — the request from the issue thread

```toml
[profiles.cloudsql]
adapter = "mysql"
host = "127.0.0.1"
port = 3306
database = "app"
tunnel_command = "cloud-sql-proxy --port 3306 my-proj:us-central1:my-instance"
tunnel_port = [3306]
```

Also `kubectl port-forward svc/pg 5432:5432`, `aws ssm start-session … localPortNumber=5432`,
`tsh proxy db --port 5432 …`. Same lifecycle, same readiness poll, same teardown.

### 4.8 A key with a passphrase, or 2FA

```
$ hsql -P redshift -c "select count(*) from events"
Enter passphrase for key '/home/tco/.ssh/id_ed25519':
tunnel: 127.0.0.1:15439 -> data-analytics…:5439 via ssh redshift_prod
```

The prompt is `ssh`'s, on the real terminal, because the child is started before Textual (in
the IDE) and before any output (in `hsql`). An agent running `hsql` unattended uses a key
with no passphrase or an agent, exactly as it would for `ssh` itself.

### 4.9 Something that is not tunneled

`harlequin my.db`, `hsql -a postgres --host prod.example.com …`. No `ssh_host`, no
`tunnel_command`, no `harlequin.tunnel` import, no cost.

## 5. The forwarder

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

One implementation, `SubprocessTunnel`. The SSH options build an argv; `--tunnel-command` is
`shlex.split()` of what the user wrote. Everything after that is shared.

- **Foreground, not `-f`.** The child keeps the terminal, so prompts reach the human, and we
  keep the handle, so it can be killed. `ssh -fN` backgrounds itself and outlives the
  session — which is why the manual workflow needs a `kill` afterwards and this does not.
- **Readiness** is a connect-poll against each known local port until `--tunnel-timeout`
  (default 10s), which also notices the child exiting early. On failure the error quotes the
  child's stderr verbatim, because `ssh`'s diagnostics are better than any we would write.
- **Ports we do not know**: in the 4.1 shape the forward is in `ssh_config`, so Harlequin has
  no port to poll. Two ways out, and the first needs verifying: `ssh -G <host>` prints the
  resolved config and *may* include `localforward` lines, in which case the poll is exact and
  free; otherwise Harlequin skips the poll, waits for the child to survive a short grace
  period, and lets the adapter's own connection be the test. **Confirm `-G` behavior on the
  OpenSSH versions we care about before relying on it** — this container has no `ssh` to
  check against, and the fallback has to be good enough on its own.
- **Teardown** is `terminate()` then `kill()`, from a `contextlib.ExitStack` wrapping
  `tui.run()` and the `hsql` run, plus an `atexit` backstop.
- **The only `-o` Harlequin imposes is `ExitOnForwardFailure=yes`**, and only because a
  forward that silently did not happen is the one failure the user cannot diagnose. Notably
  *not* imposed: `ServerAliveInterval`. A command-line `-o` beats the user's config file, and
  keepalives are exactly the sort of thing a working `Host` block already sets — overriding
  them would be Harlequin quietly retuning a connection someone else configured. The docs say
  to set them in the `Host` block; `--ssh-option` is there for anyone who wants to anyway.
- **No reconnect in v1, but the IDE says when the tunnel dies.** A thread waiting on the
  child posts a notification when it exits, so a session whose forward dropped shows
  `tunnel closed: <ssh's last line>` rather than an unexplained wall of query errors. `hsql`
  is short-lived enough that the adapter's own connection error is the whole story.

## 6. The CLI and config surface

New options on **both** commands. A profile serves both, and `hsql` is where this matters
most: a cron job or an agent cannot ask a human to run `ssh -fN` in another terminal first.

| option | meaning |
|---|---|
| `--ssh-host TEXT` | `[user@]host[:port]`, or a `Host` alias from `~/.ssh/config` |
| `--ssh-forward TEXT` | repeatable; `PORT`, `HOST:PORT`, or `LOCAL:HOST:PORT`. Omit when `ssh_config` has it |
| `--ssh-option KEY=VALUE` | repeatable `-o`; `IdentityFile`, `ProxyJump`, `User`, anything |
| `--tunnel-command TEXT` | §4.7; mutually exclusive with `--ssh-host` |
| `--tunnel-port INT` | repeatable; ports the readiness poll waits for |
| `--tunnel-timeout FLOAT` | seconds to wait (default 10) |

Three SSH flags, not nine. An earlier draft had `--ssh-user`, `--ssh-port`, `--ssh-identity`
and more; every one of them is `ssh_config` or `-o` spelled twice, and the docs' advice is to
write a `Host` block, which is reusable outside Harlequin and is where a user's `ProxyJump`,
certificate and `Match` rules already live.

`--ssh-host` with neither `--ssh-forward` nor a `LocalForward` in its resolved config is a
usage error naming both places — a tunnel that forwards nothing is always a mistake, and it is
better caught before an SSH handshake than after one. (This is the second thing that depends
on `ssh -G`; without it, this check degrades to "no `--ssh-forward` and no way to know", and
should then not fire at all rather than fire wrongly.)

No password flag. `--ssh-password` would be a credential in `ps` output and in shell history,
for a case `ssh` already handles better with a key, an agent, or its own prompt. If one is
ever added it is `secret=True`, and `redact._SECRET_NAME` grows `passphrase`, which it is
missing today.

`hsql`'s flags are the frozen part of its API, so these join the reserved spellings in
`first_pass.attach_adapter_options()`. Worth a survey of published adapters before merging; I
know of none that claims one.

**The start-up notice.** `hsql` on stderr (never stdout — that belongs to query output), the
IDE as a notification and on the debug screen:

```
tunnel: 127.0.0.1:15439 -> data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com:5439 via ssh redshift_prod
```

One line, and it is what tells a user which database they are actually looking at.

### 6.1 A config file must not be able to run a command

This is the one place the feature can make Harlequin *less* safe, and it is why SSH keeps its
own flags rather than collapsing into `--tunnel-command`.

Config files are discovered in the **working directory**, including `pyproject.toml`. Clone a
repository, run `harlequin` in it, and today the worst a hostile config can do is name a
database. With `tunnel_command`, it would run a shell command. The same is true of
`ssh_option`, because `ProxyCommand=…` is a command.

Proposed rule: **`tunnel_command` and `ssh_option` are honored only from the user's own
config** — an explicit `--config-path`, the user config dir, or `$HOME` — and from the command
line. A profile supplied by a file found in the working directory that sets either one is a
config error naming the file. `config.py` already tracks which file supplied each profile
(`Provenance`), so the check has the information it needs. `ssh_host` and `ssh_forward` are
names and ports, not commands, and stay allowed everywhere.

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

The cost is that `harlequin -P redshift` authenticates before showing a UI, which is what
every CLI that tunnels does and what a user who configured one expects.

**Cache keys need one small change.** Nothing is rewritten, so `get_connection_hash()` and
`adapter.connection_id` are already stable across runs — but two bastions fronting two
databases both look like `localhost:5439`, and would share a catalog cache and a query
history. The ssh destination and the forward specs join the hashed material.

## 8. Public API impact

**None.** No change to `HarlequinAdapter`, `HarlequinConnection`, `HarlequinCursor`,
`AbstractOption`, `catalog.py` or `driver.py`. One new `HarlequinTunnelError(HarlequinError)`
in `exception.py`, and one new module, `harlequin/tunnel.py`.

That an adapter cannot tell it is tunneled is not an accident — it is the property being
bought, and it is why this works for adapters nobody in this org maintains.

## 9. Dependencies, imports, packaging

**v1 adds no dependency at all**: `subprocess`, `socket`, `shlex`.

`harlequin/tunnel.py` imports no Textual and no adapter, and joins the "adapter API is
reachable without the TUI" import-linter contract. It is imported from the CLI callbacks only
when a tunnel was asked for, so 4.9 pays nothing — `scripts/cold_start.py` should show no
change.

**When paramiko would earn its place** — as `harlequin[ssh]`, a second `Tunnel`
implementation, nothing else changing:

- Windows users without OpenSSH. It ships with Windows 10+, but is removable.
- Containers: this repo's own dev container has no `ssh` binary, and neither does the
  `python:3.x-slim` image most people build on.
- Anything wanting in-process sockets rather than a listener — the shape Google's Cloud SQL
  *connector* (as opposed to its proxy binary) wants, and the one thing `--tunnel-command`
  cannot express.

Until one of those has a user asking, the argument on the issue stands: an unmaintained
wrapper is not worth taking, and a maintained one is still a `cryptography` dependency on
every Harlequin install, to solve a problem `ssh` already solves on the same machine.

## 10. Testing

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
| `--ssh-host` alone, `-G` reports a `localforward` | argv has no `-L`, poll watches that port |
| `--ssh-host` alone, no forward anywhere | usage error naming both places |
| `--ssh-host` and `--tunnel-command` | usage error, exit 2 |
| a garbage forward spec | usage error naming the three forms |
| `tunnel_command` from a cwd-discovered config | config error naming the file (§6.1) |
| child exits during the poll | `HarlequinTunnelError` quoting its stderr, exit 3 |
| poll times out | ditto, naming `--tunnel-timeout` |
| profile round trip | `ssh_host`/`ssh_forward` survive the merge and reach the argv |
| cache key | two ssh hosts, same conn_str, two different hashes |

One test runs `ssh -V` and skips if absent, to prove the built argv is accepted by a real
client where one exists.

## 11. Phasing

1. **`harlequin/tunnel.py`** — the `Tunnel` ABC, `SubprocessTunnel`, argv construction, the
   readiness poll, `HarlequinTunnelError`. Unit tests only; no CLI. Mergeable alone.
2. **Wire it into both commands** — the options, the `ExitStack`, the notice, the cache-key
   change, the §6.1 provenance rule, and the loopback end-to-end test.
3. **`--tunnel-command`**, `--info` / debug-screen reporting, regenerated config schema.
4. **Docs** in `tconbeer/harlequin-web`: the "details are the far side's" contract, and the
   `Host` block as the recommended way to configure a tunnel. Plus a `CHANGELOG.md` entry
   under `[Unreleased]` → Features, referencing
   [#545](https://github.com/tconbeer/harlequin/issues/545).

## 12. Alternatives considered

| | why not |
|---|---|
| **Rewriting the adapter's host/port** (the issue's original sketch, and an earlier draft of this doc) | Core has to know which option is the host, and there is no general answer: a new `role=` declaration no published adapter has, a name-matching backstop for `host`/`port` that is a guess, and an in-place DSN rewrite for adapters that take a positional conn_str — three mechanisms, each with a way to be quietly wrong, to reach where a local forward reaches with none. It also bakes an ephemeral port into `connection_id` (losing the catalog cache every run) and breaks TLS hostname verification without the user having asked for it. |
| **Only `--tunnel-command`, with SSH as one way to spell it** | Mechanically correct — `--tunnel-command 'ssh -N redshift_prod'` is the whole feature. Rejected for §6.1: a name in a config file is safe and a command is not, and the safety rule is only writable if the two are different keys. The ergonomics are the secondary argument: `ssh_host` needs no quoting, survives a Windows shell, and is what someone looks for in `--help`. |
| **A `{tunnel_port}` placeholder** the user writes into their conn_str | General and explicit, and it survives any option spelling — but it is a second thing to learn for the same result, and it makes a profile unusable *without* the tunnel rather than merely wrong. |
| **A SOCKS proxy (`ssh -D`) with `socket.socket` patched** | The one design where the driver keeps the real hostname, so TLS verification is untouched. It only works for pure-Python drivers: psycopg2, mysqlclient, ODBC and duckdb's extensions open sockets in C and never see Python's `socket` module. |
| **Each adapter grows `--ssh-*`** | N implementations of one thing, N spellings, and the adapters that need it most are out-of-tree. |
| **A library adapters call (`harlequin-tunnel`)** | Adapters must opt in, so "works with any adapter" becomes "works with adapters that adopted it". Reasonable *in addition*, for an adapter that wants a tunnel inside its own connection (the Cloud SQL connector); not a substitute. |
| **`sshtunnel`** | The blocker on the issue: four years without a release, and it is a wrapper over paramiko for behavior we would otherwise get from a binary already on the machine. |
| **paramiko directly, in v1** | A `cryptography` dependency on every install, and it reimplements the part of `~/.ssh/config` users already have working. Kept as the v2 backend, §9. |
| **Tell users to run `ssh -fN` themselves** | It works, and it is what people do today — including the author of this repo. It also means a second terminal, an orphaned tunnel to remember to kill, and a profile that only connects if a human ran something first, which is exactly what `hsql` in a cron job or an agent loop cannot do. |

## 13. Open questions for review

1. **Should an already-listening local port mean "the tunnel is already up"?** Today's answer
   is a hard error from `ExitOnForwardFailure`, which means a user who still runs
   `ssh -fN redshift_prod` by hand gets `bind: Address already in use` on 15439 from every
   Harlequin start. Skipping the
   child when every known local port already accepts connections would fix that, at the cost
   of connecting to *something* on that port without knowing it is the right tunnel. Leaning
   toward skip-with-a-notice.
2. **How much do we lean on `ssh -G`?** It is what makes the 4.1 shape fully checkable — the
   readiness poll and the "you forwarded nothing" error both want it. It needs verifying
   against real OpenSSH versions (including Windows) before either depends on it.
3. **Should the local bind address be configurable?** `127.0.0.1` is proposed and hardcoded.
   `0.0.0.0` would expose the far side's database to the user's network, which seems like a
   thing to make people ask for.
4. **`require_tunnel = true`** as a profile key, for the §3 hazard — worth it now, or wait for
   someone to be bitten?
5. **Naming**: `--ssh-*` for the SSH backend and `--tunnel-*` for the generic one splits
   `--tunnel-timeout` away from its siblings. Alternative: `--ssh-*` everywhere plus
   `--tunnel-command`, with `--ssh-timeout` covering both.
