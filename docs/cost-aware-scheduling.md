# Use what you know. Call the model when needed.

The adaptive polynomial Agent now compares bounded local candidates before
requesting a model. Configuring a model callback does not require invoking it:
a useful checked rule or a small symbolic rewrite may finish the task directly.

```bash
python -m resimind demo --domain scheduling
python -m resimind demo --domain scheduling --json
```

This offline example learns and reloads polynomial rules, then compares empty
and learned libraries on a quartic and a nested square. The normal tasks have a
callback configured but should never invoke it. A separate case deliberately
sets the local-work budget to zero and invokes one scripted callback. It makes
no live-model performance claim and requires no API key.

For a single unusually large but still bounded symbolic step, pass
`max_local_work_overflow` to allow one retry above `max_local_work`; its hard
ceiling is the sum and all identity, goal, and Lean gates remain active. The
default is `0`.

## How selection works

For the current verified state, the adapter previews bounded rule substitutions,
compaction and local distribution. Previews are untrusted candidates. They do
not become facts, enter the library, call Lean or ask the identity verifier to
generate an expected answer.

A candidate that completes the expansion can outrank a smaller intermediate
expression. Otherwise, the adapter considers remaining expansion work and
expression growth. A matching rule can be skipped if compacting its inputs
looks cheaper. Costs are syntactic heuristics: neither predicted time nor a
guarantee of the shortest proof.

The selected candidate passes the ordinary state-bound exact verifier. With
Lean enabled it also needs a formal proof. Rule status, fingerprint and current
state are checked at execution; an earlier preview cannot authorize a revoked
rule. Candidate failures, duplicate suppression, finite action and proof
budgets, and checked recovery still apply.

When no suitable local candidate remains, or the estimated work exceeds the
configured local budget, an available model can propose a step. Actual proof
feedback can still guide a model retry. A missing or exhausted model does not
authorize an unchecked result.

## Configure and inspect

```python
from dataclasses import asdict
import json

from resimind import KnowledgeLibrary, Task
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.domains.polynomial_learning import PolynomialProblem

stats = AdaptiveStats()
library = KnowledgeLibrary({"algebra": AlgebraVerifier()})
learner = build_adaptive_learning_agent(
    PolynomialProblem("(x+2)*(x+5)*(x+7)", ("x",)), library,
    stats=stats,
    cost_aware_scheduling=True,  # default
    max_local_work=4096,        # heuristic work threshold, not tokens or seconds
    max_rule_previews=16,
    max_model_calls=8,
    max_steps=64,
    # complete=your_completion_callback,
    # lean_backend=LeanPolynomialBackend(),
)
outcome = learner.run(Task("local-first", "Expand the polynomial", "algebra"))
print(outcome.result.run_result.status, stats.model_calls)
print(json.dumps(asdict(stats), indent=2))
```

`max_local_work=0` is useful for an explicitly labeled model-fallback test.
`cost_aware_scheduling=False` preserves the earlier scheduling policy;
`control_expression_growth` independently controls compaction and distribution.
The historical v5 runner and the scripted `adaptive` fault-injection demo pin
the earlier policy. The `lean` proof-feedback demo sets a zero local-work budget
for its discovery phase so that its scripted callback is actually exercised.

`scheduling_decisions` records bounded candidate previews, the selected strategy
and rule, selection reasons, state binding and preview work. `rule_usage`
records actual rule attempts, their outcome, task context and before/after
complexity. Both lists retain at most 512 entries, with omission counters.
Reusing a supplied `AdaptiveStats` object accumulates observations across runs;
per-task budgets reset. `asdict(stats)` can be saved as application-owned JSON.

These observations are separate from the verified knowledge library. They do
not certify a rule, change its status, or train a policy. A smaller expression
or a shorter observed run does not establish how much work a rule saved;
measured savings require a paired comparison with an alternative run.

## Evaluate the whole Agent

Useful measurements include correctly completed tasks, model calls and tokens,
actual rule applications, committed steps, preview work, verification calls,
wall time and peak expression size. Zero API calls are valuable when local
methods suffice, but extra preview or proof work must still be counted.

Compare empty and learned libraries under the same policy and limits. Keep
development regressions separate from newly frozen cases; label forced model
fallback and proof-failure tests separately from naturally occurring failures.
The current implementation is a bounded polynomial adapter. Its local cost
heuristics are not a universal scheduler for every domain supported by the
Agent core.
