# SSH tunnels — a design for [#545](https://github.com/tconbeer/harlequin/issues/545)

Reach a database through an SSH host, from either command, **with any adapter — including
out-of-tree ones that are never changed.** Nothing here is implemented yet.

## Summary

- **Harlequin runs `ssh`; it does not touch connection details.** The user's details already
  name the local end of a forward. Core never learns which adapter option is the host, never
  parses a DSN, and adds nothing to the adapter API — which is why every adapter works,
  including ones nobody in this org maintains.
- **Four flags, all `--ssh-*`, none of them parsed.** `--ssh-host` and `--ssh-forward` go to
  `ssh` verbatim. Anything they cannot say goes in a `Host` block, which `--ssh-host` names.
- **No new dependencies** — the blocker on the issue since 2025 — and the user's whole
  `~/.ssh/config` comes for free: `LocalForward`, `ProxyJump`, agent, certificates, `Match`.
- **The tunnel starts before Textual**, so a passphrase or 2FA prompt reaches a human. It is a
  child process, not `ssh -f`, so it dies with the session instead of outliving it.

## 1. Ten seconds of SSH

| | |
|---|---|
| `-L 15439:redshift.internal:5439` | **local forward.** Listen on `127.0.0.1:15439`; deliver to `redshift.internal:5439` **as resolved by the SSH host**. |
| `LocalForward 15439 redshift.internal:5439` | the same directive, in a `~/.ssh/config` `Host` block |
| `-N` | run no remote command; I am here for the forwards |
| `-f` | fork to the background once the forwards are up |
| `-o ExitOnForwardFailure=yes` | exit if a forward cannot be set up, rather than connecting anyway |
| `ssh -G host` | print the resolved config — `Host`/`Match` blocks plus this command line's flags — and connect to nothing |

`ssh -fN redshift_prod` and `ssh -L …` are not alternatives. `-f` and `-N` say *how to run*;
`-L` and `LocalForward` say *what to forward*, and they are one directive in two places. **A
user who already tunnels has the forward in their ssh config**, so `--ssh-forward` is optional
and the motivating case (§3.1) never uses it.

## 2. The contract

**The connection details name the local end of the forward.** Harlequin runs the tunnel and
touches nothing else. `conn_str` and every adapter option reach the adapter exactly as typed;
the adapter is never told a tunnel exists.

```
   ssh_host = "redshift_prod"   ─►  ssh -N -o ExitOnForwardFailure=yes redshift_prod
                                    (the -L comes from ~/.ssh/config)

   host = "localhost"           ─►  adapter dials 127.0.0.1:15439, the local end of
   port = 15439                     `LocalForward 15439 …:5439`
```

Consequences worth naming:

- **Nothing is rewritten**, so `connection_id` and the catalog cache key stay stable across
  runs, and TLS still verifies the hostname the user asked for.
- **DuckDB and SQLite need no special case.** They ignore the tunnel; a duckdb `ATTACH` of
  `postgres://localhost:15439/prod` goes through it like anything else.
- **A profile with `host = "localhost"` is only correct with its tunnel up.** Run it without
  `ssh_host` and it reaches whatever is on that port locally. Recommend a distinct local port
  — `15439`, not `5439` — so that mistake is a connection refused rather than the wrong
  database. The `ssh_*` keys sit in the same profile, so they travel together.

## 3. Examples

### 3.1 The motivating case: a real `Host` block

```
Host redshift_prod
  HostName web-1
  User tco
  LocalForward 15439 data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com:5439
  ServerAliveInterval 60
  ServerAliveCountMax 3
```

Today: `ssh -fN redshift_prod`, then `harlequin --host localhost --port 15439 …`, then
remember to kill the ssh.

```toml
[profiles.redshift]
adapter = "postgres"
host = "localhost"
port = 15439
dbname = "prod"
ssh_host = "redshift_prod"
```

`harlequin -P redshift`, `hsql -P redshift -c "select 1"`. **One key.** The connection details
are the ones that already work. Local `15439`, remote `5439` — the profile names the local
end, which is all the contract says.

### 3.2 The same cluster, no `~/.ssh/config`

```bash
harlequin -a postgres --host localhost --port 15439 --dbname prod --user tco \
  --ssh-host tco@web-1 \
  --ssh-forward 15439:data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com:5439
```

`HostName` + `User` → `--ssh-host tco@web-1`. `LocalForward` → `--ssh-forward`, the same text
with the space turned into a colon. (`--user` is Redshift's user; `tco@` is the SSH host's.)
The keepalives have **no flag** — see §8. This is the shape for CI or an agent with no
dotfiles; everyone else writes the `Host` block.

### 3.3 Others

| | |
|---|---|
| database on the SSH host | `--ssh-forward 5432:localhost:5432` — `localhost` is the far side's |
| local 5432 already taken | `--ssh-forward 15432:db.internal:5432`, details say `15432` |
| two databases, one host | `--ssh-forward` twice; one child, two `-L` |
| a jump host in front | `ProxyJump` in the `Host` block. Chains work because `ssh` does them |
| Cloud SQL, `kubectl`, SSM | run the proxy yourself and point at its local port — that already works, because of §2. Harlequin just does not own its lifetime (§8) |
| not tunneled | no `ssh_host`, no `harlequin.ssh` import, no cost |

## 4. The options

Both commands, since one profile serves both and `hsql` is where it matters most: a cron job
cannot ask a human to run `ssh -fN` first.

| option | profile key | meaning |
|---|---|---|
| `--ssh-host TEXT` | `ssh_host` | the destination, verbatim to `ssh`: a `Host` alias, `host`, `user@host`, or `ssh://user@host:port` |
| `--ssh-forward TEXT` | `ssh_forward` | repeatable; whatever follows `ssh -L`, verbatim. Omit when `ssh_config` has it |
| `--ssh-allow-reuse` | `ssh_allow_reuse` | on a bind collision, warn and connect anyway instead of failing (§5.3) |
| `--ssh-timeout FLOAT` | `ssh_timeout` | seconds to wait for the forwards (default 10) |

No short declarations — `-o` is `--output` in both commands. These join the reserved spellings
in `first_pass.attach_adapter_options()`; no published adapter appears to claim one.

**Harlequin parses none of these values.** `ssh` owns its own syntax and error messages, so
there is no Harlequin-shaped subset of either to document or to get wrong. The only parsing in
this feature is of `ssh -G`'s *output* (§5.1) — something `ssh` prints, not something a user
typed.

`--ssh-host` with no forward anywhere is a usage error naming both places to put one.

**The start-up notice**, on `hsql`'s stderr (never stdout) and as an IDE notification and
debug-screen line:

```
ssh: 127.0.0.1:15439 -> data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com:5439 via redshift_prod
```

It is what tells a user which database they are actually looking at.

## 5. Behavior

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

```
first_pass -> click parses -> profile merge -> SshTunnel.start()   <-- prompts happen HERE
                                                    |
                                       adapter_cls(...) -> tui.run() / hsql runs
                                                    |
                                             ExitStack unwinds -> stop()
```

- **Before Textual, in the click callback.** Once Textual owns the terminal, `ssh` cannot ask
  for a passphrase and a 2FA push has nowhere to print "check your phone". It also gives both
  commands one error path — `pretty_print_error` / `diagnostics.report_error`, exit code 3.
- **Foreground, not `-f`.** The child keeps the terminal, so prompts work, and we keep the
  handle, so it can be killed. Teardown is `terminate()` then `kill()` from a
  `contextlib.ExitStack` around `tui.run()` and the `hsql` run, plus an `atexit` backstop.
- **`ExitOnForwardFailure=yes` is the only `-o` Harlequin imposes**, because a forward that
  silently did not happen is the one failure a user cannot diagnose. Notably not imposed:
  `ServerAliveInterval`. A command-line `-o` beats the config file, and §3.1 already sets
  keepalives — overriding them would be Harlequin retuning someone else's connection.
- **The IDE says when the tunnel dies.** A thread waiting on the child posts
  `tunnel closed: <ssh's last line>`, so a dropped forward is not an unexplained wall of query
  errors. No reconnect in v1. `hsql` is short-lived; the adapter's error is the whole story.

### 5.1 `ssh -G` says what is about to be forwarded

```
$ ssh -G redshift_prod | grep -i forward
localforward 15439 [data-analytics.<aws-acct>.us-east-1.redshift.amazonaws.com]:5439
```

**Probe with the argv we are about to run.** `ssh -G` echoes command-line `-L` flags back
along with the config's, so one call returns one list whether the forward came from
`--ssh-forward`, a `Host` block, or both — there are not two sources to keep in agreement.

Parsing is two whitespace-separated fields after the keyword, each `port`, `[host]:port`, or a
socket path (which counts as a forward but is not polled). It costs one subprocess, ~10–30ms,
no network. If `-G` fails or prints something unparseable, Harlequin degrades: no poll, no
forwards-nothing error, a short grace period, and the adapter's connection is the test.

### 5.2 Readiness

Connect-poll each local port `-G` reported until `--ssh-timeout` (default 10s), noticing the
child exiting early. On failure, the error quotes ssh's stderr verbatim — its diagnostics are
better than any we would write.

### 5.3 A local port that is already bound

Harlequin always tries to start the tunnel; `ExitOnForwardFailure=yes` means `ssh` exits
rather than connecting without the forward. What happens next is the flag:

| | |
|---|---|
| default | fail and exit 3, quoting ssh (`bind: Address already in use`) |
| `--ssh-allow-reuse` | if **every** forwarded port is now accepting connections, warn and connect anyway |

```
ssh: 127.0.0.1:15439 is already bound; connecting through the existing listener (--ssh-allow-reuse)
```

The reuse test is behavioral — the child failed, but the ports answer — rather than a match
against ssh's message, so it does not depend on wording. If only some ports answer, that is a
half-open state nobody meant: fail either way.

Failing by default is the safe direction: a port that answers is not proof the right tunnel is
behind it. `--ssh-allow-reuse` is for the person who keeps `ssh -fN redshift_prod` running all
day and does not want Harlequin fighting it.

## 6. What this touches

**No public API change.** Nothing in `HarlequinAdapter`, `HarlequinConnection`,
`HarlequinCursor`, `AbstractOption`, `catalog.py` or `driver.py`. One new
`HarlequinSshError(HarlequinError)`, one new module.

**No dependency**: `subprocess`, `socket`. `harlequin/ssh.py` imports no Textual and no
adapter, joins the "adapter API is reachable without the TUI" contract, and is imported only
when `ssh_host` is set — `scripts/cold_start.py` should show no change.

**One cache-key change.** Two bastions fronting two databases both look like
`localhost:15439`, and would share a catalog cache and query history. The ssh destination and
the resolved forwards join the hashed material.

**Config, for free.** A command's click params claim its profile keys, so `ssh_*` become valid
profile and JSON-schema keys with no further plumbing; regenerate `schemas/config-v1.json`.

## 7. Testing, and phasing

No SSH server, no `online` marker.

- **Lifecycle, all three OSes.** `SshTunnel` takes an argv and ports, so tests hand it a
  Python child: a ~30-line loopback forwarder in `tests/`, covering start, poll, timeout,
  reuse, half-open, the death notification, and teardown.
- **End to end, Unix.** A fake `ssh` on `PATH` that understands `-G` and `-L` exercises the
  real CLI path including the probe. Skipped on Windows.
- **Unit**: `--ssh-forward` reaches argv unchanged and repeats in order; `--ssh-host` is
  unparsed; `-G` lines with a bind address, IPv6 and a socket path parse; `-G` returning only
  `dynamicforward` is a usage error; `-G` garbage degrades; no forward anywhere is a usage
  error; the argv carries `ExitOnForwardFailure` and no other `-o`; `-o` still means
  `--output`; profile round trip; two ssh hosts hash differently.
- One test runs `ssh -V` and skips if absent, proving the argv is accepted by a real client.

Phasing: **(1)** `harlequin/ssh.py` — argv, `-G` probe and parser, poll, reuse,
`HarlequinSshError`; unit and lifecycle tests, no CLI. **(2)** the four options, the
`ExitStack`, the notice, the cache key, the end-to-end test. **(3)** `--info` and debug-screen
reporting, the death notification, regenerated schema. **(4)** docs in `harlequin-web` — the
contract, and the `Host` block as the recommended setup — plus a `CHANGELOG.md` entry under
`[Unreleased]` → Features citing [#545](https://github.com/tconbeer/harlequin/issues/545).

## 8. Rejected and deferred

**Rejected.**

| | |
|---|---|
| **Rewriting the adapter's host/port** (the issue's sketch, and this doc's first draft) | Core would have to know which option is the host: a `role=` declaration no adapter has, a name-matching guess, and a DSN rewrite — three mechanisms that can be quietly wrong, to reach where a local forward reaches with none. It also bakes an ephemeral port into `connection_id` and breaks TLS hostname verification unasked. |
| **`--ssh-option KEY=VALUE`** | `-o ProxyCommand=…` is arbitrary code execution, and config files are discovered in the working directory — a cloned repo's `pyproject.toml` must not be able to run a command. A deny-list would mostly work, but `KnownHostsCommand` arrived in OpenSSH 8.5, so it is a list to revisit forever and a miss is a hole. Four flags that are a hostname, a forward spec, a boolean and a number have nothing to deny. A `ProxyCommand` in the user's own `~/.ssh/config` is unaffected and always was. |
| **`--ssh-config PATH`** (`ssh -F`) | Looks safer — a path, not a keyword — but the file it names can hold a `ProxyCommand`. Same threat, extra step, no deny-list possible. |
| **`--ssh-user` / `--ssh-port` / `--ssh-identity`** | `ssh_config` spelled a second time. |
| **A configurable local bind address** | `ssh_config` and the forward spec already do it; `0.0.0.0` should take deliberate effort. |
| **`require_tunnel = true`** | A distinct local port already turns the mistake into a connection refused. |
| **A `{tunnel_port}` placeholder in the conn_str** | General, but a second thing to learn for the same result, and it makes the profile unusable *without* the tunnel. |
| **SOCKS (`ssh -D`) with `socket.socket` patched** | Keeps the real hostname, so TLS is untouched — but only works for pure-Python drivers. psycopg2, mysqlclient, ODBC and duckdb open sockets in C. |
| **Per-adapter `--ssh-*`** | N implementations, N spellings, and the adapters that need it most are out-of-tree. |
| **A library adapters call** | Opt-in, so "any adapter" becomes "adapters that adopted it". Fine *in addition*, for something like the Cloud SQL connector. |
| **`sshtunnel`** | Four years without a release, wrapping paramiko for behavior a binary on the machine already has. |
| **paramiko in v1** | A `cryptography` dependency on every install, reimplementing the `~/.ssh/config` handling users already have working. |
| **"Just run `ssh -fN` yourself"** | Works today, but means a second terminal, an orphaned tunnel to kill, and a profile that only connects if a human ran something first — which `hsql` in a cron job cannot do. |

**Deferred, and what brings each back.**

| | |
|---|---|
| `--ssh-keepalive SECONDS` | someone runs an all-day IDE session with no `~/.ssh/config` and gets dropped (§3.2) |
| `--tunnel-command` for non-SSH proxies | a Cloud SQL or `kubectl` user wants Harlequin to own the proxy's lifetime, with an answer to the code-execution problem above |
| a paramiko backend, `harlequin[ssh]` | a user on Windows or in a container with no `ssh` binary |
| reconnect after a drop | the death notification proves not to be enough |

Each is additive: the flags are namespaced, one class has no ABC to satisfy, and no adapter is
involved in any of it.
