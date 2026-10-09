# ResiMind v0.12.0 release validation — 2026-10-09

This record covers source and package checks. It is not a live-model benchmark
or a ranking against other Agent frameworks.

| Check | Result |
| --- | --- |
| Complete source regression suite, including actual Lean checks | 1,649 passed |
| Cost-aware scheduling and overflow regressions | 42 passed |
| Bounded mixed-product expansion with no model callback | Solved with 11 checked steps and 4 bounded overflow grants |
| Default-budget control for the same mixed product | Remains unfinished without an overflow reserve |
| Overflow accounting | Cumulative estimated excess stayed within the configured reserve |
| Default compatibility | `max_local_work_overflow=0` preserves the prior behavior |

The new path keeps the normal `max_local_work=4096` bound. An explicit
`max_local_work_overflow` reserve can be spent across several accepted local
steps; only the estimated excess is charged, and failed or unverified
candidates do not consume it. Exact identity checks and the goal-directed
progress gate run before the optional Lean gate for every accepted step.

The mixed-product check is a zero-model symbolic control, not a claim about
model quality or general polynomial completeness. Fresh model requests and raw
development traces remain local.

## Reproduce

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```
