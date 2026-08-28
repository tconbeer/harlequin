# Skill trigger evals

`trigger_queries.json` is the eval set for the `hsql` skill's `description`, which is the
only part of a skill always in context and the whole of what decides whether it loads.

Run it with the description-optimization loop in the `skill-creator` skill, which needs
the `claude` CLI. It is a pre-merge exercise on a change to the description, not a CI
check: scoring it costs model calls, and the answer is a rate rather than a pass or fail.

Ten positives and ten negatives, split train/test so a description tuned on one half is
scored on turns it was never tuned against. The positives include turns that never say
"hsql" or "harlequin", because under-triggering is the failure mode; the negatives are
near misses — SQL-shaped work that is not a query, and database-shaped work that belongs
to another tool.

It lives here rather than beside the skill because
`src/harlequin/hsql/skill/` is what ships in the wheel, and is the directory a
marketplace entry points at as a plugin.

## Last run

2026-08-28, against the description as it ships: 10/10 positives and 10/10 negatives on
both splits. The description was not tuned against this set — it scored at ceiling on the
first pass — so the set's value now is as a regression check on the next edit to the
description, and it will need harder near misses before it can distinguish one good
description from another.
