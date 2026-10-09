# ResiMind v0.8.0 release validation — 2026-10-09

This record covers the v0.8.0 source and distribution checks. It is not a model
benchmark or a claim of performance against other Agent frameworks.

| Check | Result |
| --- | --- |
| Complete source regression suite, including installed optional integrations | 1,384 passed |
| Runnable source examples | All 8 passed |
| Built wheel installed without dependencies into a fresh Python 3.12 environment | Package and distribution versions both 0.8.0 |
| Installed command run outside the repository: 6 domains, text and JSON output | 12/12 passed |
| CLI regression tests against the installed wheel, without the source import path | 28 passed |

The six installed domains are optimization, bridge, customer support,
planning, knowledge growth, and adaptive reasoning. The wheel contains the
new strategy controller, adaptive adapter and demo, as well as the MIT license.

The suite covers classified failures, per-state retry limits, duplicate
suppression, finite strategy budgets, local subtree edits, full-expression
verification, rule status and revocation, checked checkpoint returns, and
verified rule admission/reload/reuse. Rejected or unknown checks cannot create
accepted facts. The adaptive CLI tests explicitly distinguish scripted model
responses from live model calls.

The eight example modules are `agent_demo`, `mathematics_agent`,
`engineering_agent`, `continuous_bridge`, `customer_support`, `open_planning`,
`knowledge_growth`, and `adaptive_reasoning`.

## Reproduce

```bash
python -m pip install -e '.[dev,deepseek,langgraph]'
python -m pytest -q
python -m examples.adaptive_reasoning
python -m resimind demo --domain adaptive --json
```

The sandbox regression tests need permission to bind a local loopback socket;
they were run with that permission. No check was disabled. The tests and
release demos do not make paid model calls.

## Scope

The adaptive mathematics adapter supports bounded rational polynomial
expansion. Passing these regressions does not establish general mathematical
completeness, universal correctness, or an international performance ranking.
Strategy selection is heuristic, and a run can stop with unresolved work.
Lean is not integrated in this version.

Earlier published pilot data remain labeled as historical. Private v3–v5
evaluation results and raw requests are excluded from this release. The
public evaluation runners and synthetic test fixtures remain available for
independent experiments. Package version metadata changed for this release;
historical frozen experiments retain their original source snapshots.
