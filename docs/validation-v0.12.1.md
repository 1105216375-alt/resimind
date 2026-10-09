# ResiMind v0.12.1 release validation — 2026-10-10

This patch release tightens the bounded local-work reserve introduced in
v0.12.0. It is not a live-model benchmark or a ranking against other Agent
frameworks.

| Check | Result |
| --- | --- |
| Complete source regression suite, including actual Lean checks | 1,652 passed |
| Overflow scheduling and strict option regressions | Passed |
| Multiple matching verified rules | Charge binds to the selected rule's estimate |
| Failed identity / unavailable proof gate | No overflow charge, fact, or admission |
| Repeated previews | Denial accounting is deduplicated by state and candidate |
| Package import | `resimind.__version__` and installed metadata report 0.12.1 |

The reserve remains opt-in. Every accepted step still passes exact identity and
goal-progress checks, plus Lean when configured; failed candidates and rollback
do not consume or refund accepted overflow.

Detailed model pressure tests and raw requests remain local.
