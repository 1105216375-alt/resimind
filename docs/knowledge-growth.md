# Verified knowledge growth

ResiMind can turn a completed, verified derivation into reusable knowledge. A new task retrieves that knowledge as proposal guidance; its own verifier still checks every application.

```text
propose → verify → commit → rebuild the remaining task
                      ↓
           extract a derivation certificate
                      ↓
             verify → store → retrieve → verify again
```

The included algebra adapter demonstrates this complete path with exact polynomial identities. Its default proposer is a deterministic rewrite grammar. The recorded results below use no model calls and do not measure neural discovery or state-of-the-art performance.

## Run the installed demo

```bash
python -m resimind demo --domain knowledge-growth
python -m resimind demo --domain knowledge-growth --json
```

The demo derives `(u+v)**3` from an empty knowledge library, verifies and saves the resulting identity, reloads and re-verifies it, then expands `(2*x+3*y)**3` in two fresh runs. The fixed-library run needs **16 proposals**, while the growing-library run needs **1**; both solve the task. Discovering the cubic identity costs another **16 proposals**.

This standalone cubic demonstration differs from the ten-task study below: that study learns a square first, allowing the square rule to help derive the cube.

## Use the Python API

The following example runs without an API key or external dependencies:

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from resimind.agent import Task
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import (
    PolynomialProblem, WorkCounts, build_learning_agent,
)
from resimind.knowledge import KnowledgeLibrary

verifiers = {"algebra": AlgebraVerifier()}
library = KnowledgeLibrary(verifiers)

discovery = build_learning_agent(
    PolynomialProblem("(u+v)**3", ("u", "v")), library,
).run(Task("discover-cube", "Derive a cubic expansion", "algebra"))

assert discovery.result.run_result.status == "solved"
assert any(record.status == "verified" for record in discovery.admissions)

with TemporaryDirectory() as folder:
    path = Path(folder) / "rules.json"
    library.save(path)
    library = KnowledgeLibrary.load(path, verifiers)  # Re-verifies certificates.

counts = WorkCounts()
transfer = build_learning_agent(
    PolynomialProblem("(2*x+3*y)**3", ("x", "y")),
    library, counts=counts,
).run(Task("transfer-cube", "Expand a new polynomial", "algebra"), learn=False)

print(transfer.result.run_result.status)  # solved
print(counts.proposal_calls)             # 1
print(transfer.retrieved_ids)
```

`LearningResult.result` contains the ordinary `AgentResult`. `admissions` records knowledge-verification outcomes; `learning_errors` reports extraction or admission failures separately from task completion. `learn=False` disables post-task learning, which is useful when freezing a library for evaluation. A retrieved rule ID alone does not establish that the task used the rule.

To supply neural proposals, pass your existing `complete(prompt: str) -> str` model callback as `build_learning_agent(problem, library, complete=model_callback)`. The adapter supplies `ModelProposer` with its candidate contract and available identities. The callback's candidates face the same task verifier, and the callback cannot directly approve knowledge.

The optional [current-state proposal and rule-execution interface](state-bound-polynomial-actions.md) exposes the latest verified expression directly and lets the model select one verified rule for bounded symbolic execution. The resulting step still requires independent verification.

The [adaptive reasoning adapter](adaptive-reasoning.md) additionally controls bounded strategy changes, local subtree proposals, symbolic fallback, and verified checkpoint returns. It uses the same independent admission and reuse checks described here.

## Extend another domain

[`LearningAgent`](../src/resimind/learning.py) accepts an `agent_factory(task, records)` and a `distill(agent_result)` function. The factory builds your ordinary Agent using retrieved records as guidance. The distiller returns `KnowledgeCandidate` objects only after a completed task.

Register an application-owned [`KnowledgeVerifier`](../src/resimind/knowledge.py) under the candidate's domain. Its `verify(candidate)` method returns `Verification("verified" | "rejected" | "unknown", reason, verifier_id)`. The library invokes it for admission and again on reload. A serialized approval flag, source-task name, or evidence reference is not a proof.

Candidate assumptions constrain lookup by exact matching. **Matching a condition is not proof that it holds:** the new task's verifier must establish applicability before committing a result. The algebra expansion adapter recalls unconditional identities; conditional equation cancellation requires its own checked use. `library.revoke(rule_id, reason)` retires a record from future lookup, and reload preserves that revocation. This local JSON store does not provide authenticated provenance or concurrent-writer coordination.

The algebra verifier covers bounded exact rational polynomial expressions and explicit, guarded equation-cancellation certificates. It does not prove arbitrary mathematics. Unsupported expressions, missing capabilities, or resource-limit exhaustion return `unknown`; unknown outcomes are never admitted as verified knowledge. Finite numerical examples alone are not accepted as a general rule certificate.

## Controlled development results

The study learns `(u+v)**2` and `(u+v)**3`, then saves, reloads, re-verifies, and freezes the library before ten different expressions. Both arms use the same primitive proposer and 64-proposal ceiling. A separate scorer checks exact values on complete grids whose sizes follow independently computed polynomial degree bounds.

| Measure | Fixed library | Growing library |
| --- | ---: | ---: |
| Independently valid completed tasks | 10/10 | 10/10 |
| Transfer proposals / committed steps | 84 / 84 | 24 / 24 |
| Runtime identity-check calls | 1,182 | 210 |
| Committed cross-task rule applications | 0 | 8 |

Learning the two identities costs **13 additional proposals**, because the square helps derive the cube. Discovery also performs 110 runtime identity checks; admission and reload invoke the knowledge verifier twice each. Six invalid or unsupported admission probes produce **0/6 wrong admissions**. This finite probe set is not a general soundness guarantee.

The 71.4% reduction concerns transfer proposal calls. Matching the learned rules adds work, and runtime check counts include repeated verification of previous proof steps. These measurements do not establish the same reduction in wall time, total computation, tokens, or money. Both arms already solve all ten tasks. The cases are published development examples, not a held-out model benchmark.

See the [complete methodology and per-task results in Chinese](knowledge-growth.zh-CN.md) and the [machine-readable result summary](evidence/knowledge-growth-v1/summary.json). From a source checkout with development dependencies installed, reproduce the study with:

```bash
python -m benchmarks.knowledge_growth --output /tmp/resimind-knowledge-growth-summary.json
python -m pytest -q tests/test_knowledge_growth_eval.py
```
