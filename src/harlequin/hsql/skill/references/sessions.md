# Warm sessions: `--serve` and `--session`

Read this when you are about to run many invocations against one database, when the work
needs state to survive between them (a temp table, a `SET`, a loaded extension), or when
`HSQL_SESSION` is set and you want to know what that changes.

Every ordinary invocation starts a process and opens its own connection, and takes that
connection down with it. A session is one connection held open by a server process, with
invocations sent to it: each answers in milliseconds, and what one leaves behind the next
one finds. POSIX only — a session is reached over a unix socket, which native Windows
does not have. WSL2 is Linux and has them.

## Starting one, and stopping it

```bash
hsql --serve dev -P dev
```

```
note: session 'dev' is ready (duckdb). Send it queries with `hsql --session dev -c ...`, or set HSQL_SESSION=dev. Ctrl-C stops it.
```

`--serve` runs in the foreground, writing a line per request to stderr, until `Ctrl-C`.
Background it with `&`, or hand it to a service manager (a systemd *user* unit, since the
socket belongs to your user).

**Starting one is the human's decision, not yours.** A session holds an authenticated
connection — and an SSH tunnel, if it opened one — for as long as it runs. Suggest one
when the work would benefit; start one only when you are asked to.

## Sending invocations to it

```bash
hsql --session dev -c "select count(*) from orders"
export HSQL_SESSION=dev          # the same thing, for every invocation after it
```

Everything else is unchanged: the same flags, formats, exit codes and bytes on stdout as
a cold run, and the request carries your working directory, so `-f ./script.sql` and
`-o ./out.csv` mean what they would have meant.

The two spellings differ when no session is running. `--session NAME` is an assertion and
exits `3`. `HSQL_SESSION` is a preference: the invocation runs cold and says so on
stderr, which is the line to read when something you created a moment ago is missing.

## What a session keeps

- **Temp tables.** `create temp table` in one invocation is queryable by the next.
- **Settings.** `SET`, `search_path`, time zones, `PRAGMA`s, installed extensions.
- **In-memory databases.** `hsql --serve scratch ":memory:"` is a scratch warehouse that
  outlives the invocations that write to it.
- **Transactions.** A `begin` that no invocation committed stays open, holding its locks,
  and wraps every request after it. Nothing rolls it back — a cold run's process exit
  used to. Commit in the invocation that begins, or reset the session.

```bash
hsql --session dev --session-reset      # fresh connection; all of the above is gone
hsql --session dev --session-status     # what it is doing, as JSON
```

`--session-status` answers even while a query is running: `state` is `idle`, `busy` or
`unavailable`, `queued` is how many requests are waiting, and `expires_in_s` is how long
the session has left.

## Which options go where

| Group            | Options                                                        | Where                  |
| ---------------- | -------------------------------------------------------------- | ---------------------- |
| Connection       | `CONN_STR`, `-a`, `-r`, the SSH options, every adapter option  | `--serve`, once        |
| Session lifetime | `--idle-timeout`, `--max-lifetime`, `--queue-timeout`          | `--serve`, once        |
| Per request      | `-c`, `-f`, `--format`, `-o`, `--limit`, `--timeout`, `--catalog`, the rest | `--session` |
| Meta             | `-P`, `--config-path`                                          | either                 |

A connection option on a served invocation is refused with exit `2` rather than ignored —
the session connected once, with what `--serve` was given:

```
hsql: error: --read-only is a connection option. The session named 'dev' was started without it, and its connection is fixed. Drop it here, or start a session with it: 'hsql --serve NAME --read-only ...'.
```

So **`--read-only` is a property of the session, not of your invocation**. If the work
needs a read-only connection and the running session is not one, say so and ask; do not
quietly run writable.

## One at a time, and how it ends

One connection means one query at a time; a second invocation waits its turn.
`--queue-timeout SECONDS` on `--serve` bounds that wait, and a request that spends it
exits `4` without reaching the database. `Ctrl-C` cancels a request whether it had
started or was still queued, and exits `130`; where the adapter cannot cancel, hsql says
on stderr that the query is still running and still holding the session.

A session stops itself after 30 minutes with no request, or 8 hours after it connected —
`--idle-timeout 0` and `--max-lifetime 0` on `--serve` switch either off. "It worked ten
minutes ago and now it is cold" is usually the idle timeout. Neither clock interrupts a
running query.

## Keeping one running for a whole agent session

A `SessionStart` hook starts one once and reuses it, and `HSQL_SESSION` points every
invocation at it. In `.claude/settings.json`:

```json
{
  "env": { "HSQL_SESSION": "dev" },
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "hsql --session dev --session-status >/dev/null 2>&1 || setsid hsql --serve dev -P dev >>/tmp/hsql-dev.log 2>&1 &"
          }
        ]
      }
    ]
  }
}
```

`--session-status` exits `3` when nothing is listening, so the session is started once;
`setsid` detaches it so the hook returns. Propose this rather than writing it unasked —
it puts a standing database connection on someone's machine.

<https://harlequin.sh/docs/hsql/sessions> is the same ground for a human reader.
