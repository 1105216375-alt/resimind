# ResiMind v0.10.0 release validation — 2026-10-09

This record covers source and package checks. It is not a live-model benchmark,
a statement of total compute savings, or a ranking against other frameworks.

| Check | Result |
| --- | --- |
| Complete source regression suite, including installed integrations and actual Lean checks | 1,603 passed |
| Runnable source examples | All 11 passed |
| Fresh Python 3.12 venv, wheel installed without Python dependencies | Version 0.10.0; imports came from the installed wheel |
| Installed console command outside the repository, 9 domains in text and JSON | 18/18 passed |
| CLI regressions against the installed wheel without source imports | 33 passed; no skips or warnings |
| Packaged Python files compared with the tested source | All 35 matched |
| Actual local Lean compiler | 4.29.0 |

The nine CLI domains are optimization, bridge, customer support, planning,
knowledge growth, adaptive reasoning, expression growth, Lean feedback and
local scheduling. The eleven examples are `agent_demo`, `mathematics_agent`,
`engineering_agent`, `continuous_bridge`, `customer_support`, `open_planning`,
`knowledge_growth`, `adaptive_reasoning`, `expression_growth`, `lean_feedback`
and `cost_aware_scheduling`.

The 38 new scheduling regressions cover configured-but-unused callbacks,
useful terminal rules, comparisons between multiple rules, nested expression
growth, failed-rule isolation, revocation after preview, exact and Lean gates,
model fallback, finite budgets, independent task state and bounded observation
logs. Read-only controller eligibility checks are exercised against global,
per-state and branch limits. A spent local strategy cannot hide an available
model fallback.

Three additional expression-protocol regressions check explicit power and
multiplication instructions, rejection of `^` followed by a corrected proposal,
and rejection of implicit multiplication without automatic answer rewriting.

## Reproduce

Install optional Lean 4.29.0 using the [integration guide](../integrations/lean/README.md).
Then:

```bash
python -m pip install -e '.[dev,deepseek,langgraph]'
python -m pytest -q
python -m examples.cost_aware_scheduling
python -m resimind demo --domain scheduling --json
python -m resimind demo --domain lean --json
```

The complete suite was allowed to bind a local loopback socket for its existing
sandbox integration checks. No tests were disabled. Actual-Lean tests may skip
on machines without the supported compiler; the Lean demo reports failure if
its required proof gate is unavailable. Release checks make no paid model calls.

The scheduling demo uses zero callback invocations for its normal examples.
Its separate fallback case explicitly has a zero local-work budget and one
scripted response. The Lean demo similarly activates scripted proposals with
a zero local-work budget while executing actual compiler checks. The adaptive
fault-injection demo retains the earlier policy explicitly. These fixtures are
labeled demonstrations, not live-model evaluations.

## Scope

The new cost heuristics belong to the bounded rational-polynomial adapter.
They estimate unfinished syntax and favor useful local work; they do not
guarantee an optimal proof or the lowest actual runtime. The selected step still
passes exact verification and optional Lean verification. Observation logs do
not authorize knowledge or train a selection policy.

Historical published experiments retain their original labels. New development
comparisons, fresh-task results, model requests and raw traces remain private.
See [the scheduling guide](cost-aware-scheduling.md) for the API and limits.
