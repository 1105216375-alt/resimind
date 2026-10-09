# Change strategy after a failed check

The adaptive polynomial adapter makes failure handling an executable part of
the Agent. A failed whole-expression attempt can lead to a smaller local task,
a checked library rule, a symbolic rewrite, or a return to a verified checkpoint.
Every resulting candidate still passes the ordinary state-bound verifier.

The current adapter also compacts like terms, performs bounded local
distributive batches, and optionally uses actual Lean proof feedback to guide
the next proposal. See [expression growth and Lean](expression-growth-and-lean.md)
for the new tools, the two runnable demos and their exact scope.

This is an opt-in adapter. Existing `build_learning_agent` behavior and the
reference runtime remain unchanged.

Within this adapter, bounded local candidate comparison is enabled by default.
Rules and symbolic tools may finish a task without invoking a configured model.
See [local scheduling](cost-aware-scheduling.md) for costs, budgets and rule-effect
observations. The fault-injection demo below explicitly keeps the earlier policy
so its scripted mistakes still exercise recovery.

## Use it

Run the installed offline fault-injection fixture first:

```bash
python -m resimind demo --domain adaptive
```

Its scripted whole-expression mistake is rejected. One checked local edit is
accepted, a later mistake triggers symbolic recovery, and the completed proof
is admitted, saved, reloaded, and applied to a new task. This demonstrates the
real control and verification path, without claiming a live model result.

```python
from resimind import KnowledgeLibrary, Task
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import PolynomialProblem, WorkCounts
from resimind.domains.adaptive_polynomial import (
    AdaptiveStats, build_adaptive_learning_agent,
)

library = KnowledgeLibrary({"algebra": AlgebraVerifier()})
counts, stats = WorkCounts(), AdaptiveStats()
learner = build_adaptive_learning_agent(
    PolynomialProblem("(x+1)*(x+2)*(x+3)", ("x",)),
    library, counts=counts, stats=stats,
    max_model_calls=8, max_steps=64,
    # complete=your_completion_callback,  # Omit for symbolic execution only.
)
outcome = learner.run(Task("expand-001", "Expand the polynomial", "algebra"))
run = outcome.result.run_result
if run.status == "solved" and run.residual.solved:
    print(run.state.facts[-1].value[1])
    print([record.status for record in outcome.admissions])
else:
    print(run.status, run.stop_reason)
```

Omitting `complete` does not invoke a language model. Passing a callback enables
neural proposals as well as the symbolic strategies. The callback receives JSON
instructions for the currently selected strategy; it returns JSON, never code.
The application owns provider credentials, network timeouts, token limits, and
any external cancellation policy.

## What changes after failure

The reusable `resimind.strategy.StrategyController` manages a finite set of
named strategies. It records attempts and failures by the semantic state key
provided by the domain. Revision numbers alone cannot give a failed strategy a
fresh retry allowance. Candidate fingerprints identify repeated proposals, and
budgets survive strategy switches and checkpoint returns.

The polynomial adapter supplies the domain-specific operations:

* **Whole-expression proposal:** ask the model for an equivalent expression.
* **Local proposal:** select an unresolved AST subexpression, ask the model to
  rewrite that subexpression, and replace only the selected subtree. The
  resulting whole-expression identity is independently checked before commit.
* **Verified rule:** execute one bounded substitution of a retrieved identity.
  Check its current status and fingerprint, then check the resulting identity.
* **Primitive rewrite:** use local distributivity and integer-power unfolding.
  The identity checker never supplies a generated answer to this strategy.
* **Compaction and bounded distribution:** keep exact coefficients and like
  terms compact, and expand one eligible local product with a finite term-pair
  budget. Each generated full-expression result is independently checked.
* **Proof feedback and retry:** when Lean is enabled, unfinished goals guide a
  new model proposal or a bounded retry with the fixed `grind` tactic. An exact
  identity alone cannot bypass the enabled formal proof gate.
* **Checkpoint return:** append a newly checked equivalence to a previously
  verified expression. Keep the abandoned branch in the proof and audit, with
  its failed attempts still recorded. No committed fact is silently deleted.

Error diagnosis distinguishes mathematical mismatch, incomplete formal proof, malformed or stale state
binding, unavailable rules, lack of progress, resource limits, and unknown
failures. Diagnosis guides the next eligible strategy; it is not proof that the
next strategy will succeed. Strategy exhaustion remains an unfinished result.

## Verification and knowledge growth

Only the verifier can produce accepted facts. Subtree selection, failure
classification, strategy priority, and checkpoint ranking are planning choices,
not evidence of correctness. A completed expression must satisfy the original
global expansion goal, not just one selected local task.

The existing learning boundary is preserved: a completed verified derivation
may be distilled into a candidate identity, independently checked by the
knowledge library, saved, and reverified on reload. A library lookup does not
authorize an application to a new task. `learn=False` freezes admissions during
evaluation or execution that should not update the library.

## Costs and scope

Model calls and controller actions have separate limits. `max_steps` counts
attempts, including rejected or empty proposals and recovery actions; symbolic
work is not a free model call. The work and strategy audits expose the different
costs. A synchronous callback cannot be forcibly interrupted by an attempt
counter. The adapter caps a run at 64 attempts to fit the library's certificate
bound. Each task starts a fresh controller; an explicitly shared `AdaptiveStats`
or `WorkCounts` object accumulates across tasks. No within-task recovery resets
the remaining budget.

In this adapter, `WorkCounts.proposal_calls` counts completion callback calls;
`AdaptiveStats.action_attempts` includes symbolic and recovery attempts as well.
`model_failures` counts model attempts that do not commit a verified result,
including rejected mathematics, repeated candidates, and response failures.
Reaching the model-call limit can still be followed by a successful symbolic
completion. For bounded rule selection, the adapter considers at most the first
16 retrieved expansion identities; larger libraries need an application-owned
retrieval policy.

The controller can be reused with other domain-owned strategies, semantic keys,
and verifiers. The concrete decomposition and checkpoint adapter here supports
bounded rational polynomial expansion. It does not add a universal theorem
prover, engineering design solver, or automatic policy authoring system.

The historical v5 evaluation runner disables the later expression-growth tools,
local cost selection and Lean gate. It compares the original adaptive adapter with the previous
executable-rule adapter, strong verification-and-retry, and a symbolic-only
control using the same library. Its larger action budget and automatic tool
access are disclosed: the comparison measures the complete implementation,
not strategy scheduling in isolation. Test results and private model traces
are kept outside the public source tree.
