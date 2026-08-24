---
description: Changelog entry conventions, loaded when CHANGELOG.md is edited.
globs:
  - CHANGELOG.md
---

# Writing a CHANGELOG.md entry

Entries go under `[Unreleased]`, referencing the issue they close.

- **Only NOTABLE, user-facing changes.** User-facing bugs and enhancements. If a change is invisible to someone using Harlequin, it does not get an entry.
- **No implementation details.** Harlequin users do not care how it works inside. If an entry is explaining internals, cut it.
- **One or two sentences per feature**, saying what was added and how to use it.
- Keep-a-changelog headings: Features, Performance, Bug Fixes, Dependencies, Refactoring.
- Releases are cut by the `release.yml` workflow, which bumps the version and rolls `[Unreleased]` into a version heading. Never hand-edit released sections or `version` in `pyproject.toml`.
