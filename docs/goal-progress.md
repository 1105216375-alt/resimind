# Keep reasoning aimed at the unfinished goal

An equivalent expression may leave an expansion task unfinished. The adaptive
polynomial Agent separates identity verification, goal progress and proof
completion. This makes a cosmetic rewrite visible before spending a Lean check
on it, while retaining useful intermediate steps.

```bash
python -m resimind demo --domain progress
python -m resimind demo --domain progress --json
```

The first scripted example changes `(5*u+(-3)*v)**2` into `(5*u-3*v)**2`.
The identity is valid, but the expansion is untouched. The Agent defers this
candidate, keeps the committed state unchanged, and supplies goal feedback to
the next proposal. A subsequent full expansion passes the ordinary verifier.

The second example rewrites `(u+2)*(u+7)` into `u*(u+7)+2*(u+7)` before finishing
the expansion. This distributive step makes the expression larger and can
increase the number of remaining products. It is still a useful decomposition.
Two subsequent callbacks expand the selected left and right subexpressions,
producing three committed steps in total.

Both examples use scripted callbacks and a deliberately zero local-work
threshold. They run actual exact verification without an API key or Lean
compiler. They illustrate policy decisions, not live-model performance.

## Three decisions after identity verification

| Assessment | Policy |
| --- | --- |
| Complete or recognized structural progress | Continue to optional Lean verification and normal commit checks. |
| Cosmetic change | Defer without committing facts or starting a Lean proof for this candidate; describe what still needs expansion. |
| Uncertain intermediate step | Allow a bounded exploratory step, subject to the same exact and optional Lean checks. |

Structural progress includes recognized local distribution and power
decomposition, along with decreases in estimated remaining expansion work.
Smaller AST size alone does not establish progress. Reordering and reassociation
are not universally rejected: they may expose a useful local route and can use
the exploration allowance when their benefit is uncertain.

The progress assessment uses bounded syntax analysis. It is a domain heuristic,
not a theorem that a route is optimal or that every permitted step will help.
Its conservative rules can miss useful transformations. An uncertain step may
temporarily make the estimated work worse; a completed task still needs the
existing expansion goal to be satisfied.

In particular, a change of sign notation can expose the exact shape of a stored
rule. The cosmetic detector does not search the entire library to establish
whether that happens, so it may defer such a helpful rewrite. Compare with
`goal_directed=False` when diagnosing this limitation.

## API and budgets

```python
learner = build_adaptive_learning_agent(
    problem,
    library,
    complete=your_completion_callback,
    goal_directed=True,
    max_progress_detours=1,
    # lean_backend=LeanPolynomialBackend(),
)
```

`goal_directed=True` enables the policy and explicit goal feedback by default.
`goal_directed=False` retains the earlier progress behavior; local scheduling
and expression-growth controls are independent switches.

`max_progress_detours` caps accepted uncertain steps across one task. It is
separate from model calls, actions, rule previews and Lean checks. A failed
proof does not spend an accepted-step allowance; a later proof retry spends it
once if the step commits. Moving to a new state or rolling back does not refund
an allowance. A new task receives a fresh allowance even when statistics are
accumulated across runs. Setting the allowance to zero still permits recognized
distribution and power decomposition.

The callback receives the current expansion goal, bounded excerpts of remaining
unexpanded subexpressions, and progress feedback when applicable. These are
derived from the current expression and task. They do not supply an oracle's
expected answer. Proof feedback and goal feedback serve different purposes:
a proof tactic can establish an equality while the expansion goal remains open.

## Verification and audit

The exact identity verifier runs before the progress gate. A false expression
cannot become eligible just by looking smaller or fully expanded. When Lean is
enabled, every committed candidate must also pass its normal proof gate.
Progress feedback cannot authorize facts, issue proof certificates or admit a
rule. Rule admission still requires a completed, verified derivation.

`AdaptiveStats` retains bounded `progress_events` and counters for assessments,
cosmetic deferrals and exploratory steps. Events distinguish the assessment
from the actual verification decision and committed state change. They are
observations, not learned policy weights or measured compute savings.

In particular, `progress_complete` counts candidates assessed as having an
expanded shape after exact verification. Such a candidate may still fail Lean;
use the final run status and residual to count completed tasks.

This policy currently belongs to the bounded rational-polynomial adapter.
Planning, customer support and engineering keep their domain-specific checks.
Detailed new evaluation records remain local; the public demo is reproducible.
