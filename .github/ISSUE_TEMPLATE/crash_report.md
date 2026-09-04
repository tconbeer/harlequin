---
name: Crash Report
about: Harlequin or hsql exited unexpectedly and wrote a crash report.
title: 'Crash: '
labels: 'bug'
assignees: ''

---
**Before proceeding, please acknowledge**:
- [ ] I have searched Issues and Discussions in this repo for this error.
- [ ] I have redacted secrets and PII from the crash report pasted below.

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
