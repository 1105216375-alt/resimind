# ResiMind v0.9.0 release validation — 2026-10-09

This record covers the v0.9.0 source and distribution checks. It is not a
model benchmark or a performance ranking against other Agent frameworks.

| Check | Result |
| --- | --- |
| Complete source regression suite, including installed optional integrations and real Lean checks | 1,560 passed |
| Runnable source examples | All 10 passed |
| Built wheel installed without Python dependencies into a fresh Python 3.12 environment | Package and distribution versions both 0.9.0 |
| Installed command run outside the repository: 8 domains, text and JSON output | 16/16 passed |
| CLI regression tests against the installed wheel, without the source import path | 31 passed, no skips |
| Installed Lean compiler | 4.29.0; actual proof verification passed |

The eight CLI domains are optimization, bridge, customer support, planning,
knowledge growth, adaptive reasoning, growth control, and Lean feedback.
The ten example modules are `agent_demo`, `mathematics_agent`,
`engineering_agent`, `continuous_bridge`, `customer_support`, `open_planning`,
`knowledge_growth`, `adaptive_reasoning`, `expression_growth`, and
`lean_feedback`.

New regression coverage includes bounded compaction and distribution,
independent identity checks before commit, unchanged expression and action
limits, generated-rule persistence and reuse, and the legacy strategy path.
The Lean checks cover actual failed-proof feedback followed by a successful
proof, fixed tactics, candidate and state binding, proof and axiom reports,
finite retry budgets, missing compilers, malformed backend results, and rule
revocation during an external proof check. Resolving an elan shim through
symbolic links cannot trigger an implicit toolchain download.

## Reproduce

Install Lean 4.29.0 as described in the [integration guide](../integrations/lean/README.md).
Then run:

```bash
python -m pip install -e '.[dev,deepseek,langgraph]'
python -m pytest -q
python -m examples.expression_growth
python -m examples.lean_feedback
python -m resimind demo --domain growth-control --json
python -m resimind demo --domain lean --json
```

The sandbox regression tests need permission to bind a local loopback socket;
they were run with that permission. No check was disabled. Tests requiring
the actual Lean installation may skip on a machine where it is absent;
the Lean demo instead exits unsuccessfully and reports the unfinished run.

The growth-control example uses no model calls. The Lean example uses scripted
proposals and the real local compiler: a failed `rfl` proof supplies actual
goals to the next proposal, `grind` closes the equality, and a saved/reloaded
rule is formally checked when reused. These are reproducible integration
demonstrations, not live LLM evaluations. No release check makes paid model
calls.

## Scope

This release supports bounded rational-polynomial reasoning. Lean is an
optional gate for candidate steps and rule applications. Rule admission and
reload retain the library's exact algebra verifier; existing stored rules
are not retroactively claimed to have Lean proofs. Compiler artifacts are
temporary, and their audit hashes are not standalone proof certificates.
See the [design and limits](expression-growth-and-lean.md).

Passing these regressions does not establish general theorem-proving ability,
universal correctness, or superiority to other frameworks. Earlier published
pilots retain their historical labels. New private quantitative comparisons
and raw records remain outside the release; public runners, synthetic tests,
and runnable demonstrations remain available for independent experiments.
