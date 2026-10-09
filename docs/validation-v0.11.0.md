# ResiMind v0.11.0 release validation — 2026-10-09

This record covers source and package checks. It is not a live-model benchmark
or a ranking against other Agent frameworks.

| Check | Result |
| --- | --- |
| Complete source regression suite, including actual Lean checks | 1,645 passed |
| Runnable source examples | All 12 passed |
| Fresh Python 3.12 venv, wheel installed without Python dependencies | Version 0.11.0; imports came from the installed wheel |
| Installed console outside the repository, 10 domains in text and JSON | 20/20 passed |
| CLI regressions against the installed wheel without source imports | 35 passed; no skips or warnings |
| Packaged Python files compared with the tested source | All 37 matched |
| Actual local Lean compiler | 4.29.0 |

The new goal-progress tests include 30 integration cases and four bounded
syntax-analysis cases. They cover cosmetic deferral, useful distribution in
both directions, power decomposition, valid exploratory steps, finite detour
budgets across state changes and rollback, proof retries, shared statistics,
bounded audit records, and explicit compatibility mode.

The actual-Lean goal test defers a cosmetic proposal without invoking the
compiler. The subsequent expanded candidate passes a real `grind` proof and
completes the task. Other tests retain mathematical rejection, unavailable or
failed proof gates, and the distinction between accepted exploration and
observed goal progress. A smaller or fully expanded-looking false expression
cannot bypass exact verification.

Six additional Lean regressions cover task variables that disappear after
cancellation. The generated theorem retains every original variable binding
and disables only its unused-variable linter; any emitted warning or error
still fails verification. Actual compiler checks confirm that a proof chain
can continue after cancellation and that a false subsequent equality remains
unresolved.

## Reproduce

Install optional Lean 4.29.0 using the [integration guide](../integrations/lean/README.md), then:

```bash
python -m pip install -e '.[dev,deepseek,langgraph]'
python -m pytest -q
python -m resimind demo --domain progress
python -m resimind demo --domain progress --json
python -m resimind demo --domain lean
```

The full suite was allowed to bind a local loopback socket for existing sandbox
integration tests. No tests were disabled. Actual-Lean tests may skip on a
machine without the supported compiler; the Lean demo reports failure if the
required backend is unavailable. These release checks make no paid API calls.

The ten CLI domains are optimization, bridge, customer support, planning,
knowledge growth, adaptive reasoning, expression growth, Lean feedback,
local scheduling and goal progress. The twelve source examples add
`goal_progress` to the eleven examples listed in the [v0.10 validation](validation-v0.10.0.md).

## Scope

Goal-progress checks are bounded heuristics for rational-polynomial expansion.
They run after exact verification and before optional Lean work. Every committed
step retains the original truth and state-binding checks; only a completed
verified derivation can be admitted as a learned rule.

The progress demo uses scripted responses and a zero local-work threshold to
activate them. Cosmetic recovery commits one final expansion after two
callbacks. Useful decomposition commits three steps after three callbacks,
including a step that increases expression size. These are integration
fixtures, not measurements of model performance.

The detector deliberately permits uncertain transformations within a finite
allowance. Its cosmetic classification can still miss useful sign rewrites
that expose a stored rule. Accepted exploratory steps are recorded separately
from goal progress. Assessment counters, including `progress_complete`, do not
by themselves count successful tasks.

Detailed new development comparisons, fresh cases, model requests and raw
traces remain local. See [goal progress](goal-progress.md) for the API and limits.
