---
description: Changelog entry conventions, loaded when CHANGELOG.md is edited.
globs:
  - CHANGELOG.md
---

# Writing a CHANGELOG.md entry

Entries go under `[Unreleased]`.

- **Be as concise as possible.** One or two sentences per entry, saying what changed and how to use it. Cut every word that does not help a user decide whether they care.
- **Only NOTABLE, user-facing changes.** User-facing bugs and enhancements. If a change is invisible to someone using Harlequin, it does not get an entry.
- **No implementation details.** Harlequin users do not care how it works inside. If an entry is explaining internals, cut it.
- **Reference an existing issue, if there is one.** Look for the issue the change closes before writing the entry; omit the link only when no issue exists. Do not invent or guess a number. Format: `([#1040](https://github.com/tconbeer/harlequin/issues/1040))`, at the end of the sentence.
- Keep-a-changelog headings: Features, Performance, Bug Fixes, Dependencies, Refactoring.
- Releases are cut by the `release.yml` workflow, which bumps the version and rolls `[Unreleased]` into a version heading. Never hand-edit released sections or `version` in `pyproject.toml`.
