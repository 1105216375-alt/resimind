# Current-state proposals and executable verified rules

The polynomial learning adapter offers an opt-in neural interface that makes the current verified expression explicit. Each proposed action cites its state revision, state fingerprint and required evidence references. A model no longer needs to copy the previous expression into a `before` field.

```python
from resimind import Task
from resimind.domains.polynomial_learning import PolynomialProblem, build_learning_agent

# library is a KnowledgeLibrary configured with an AlgebraVerifier.
# complete(prompt: str) -> str is your provider callback.
agent = build_learning_agent(
    PolynomialProblem("(2*x+3*y+1)**3", ("x", "y")),
    library,
    complete=complete,
    guidance="structured",
    proposal_protocol="state_bound",
    allow_rule_execution=True,
    max_steps=8,
)
result = agent.run(Task("expand", "Expand and verify", "algebra"), learn=False)
```

Each prompt includes `current_expression`, `state_revision`, `state_fingerprint`, `required_reference_ids`, action-specific `claim_schemas`, and serialization examples generated from the current state. Examples contain placeholders, not computed answers. The outer candidate contract remains `id`, `action`, `target`, `claim`, `refs`; `claim` is a JSON object serialized inside a string.

| Action | Inner claim fields | Behavior |
| --- | --- | --- |
| `rewrite_polynomial` | `state_revision`, `state_fingerprint`, `after`, `rule_id` | Check the model's proposed next whole expression. An empty rule ID means its own derivation. |
| `certify_expansion` | Same fields | Also require a fully expanded result. |
| `apply_verified_rule` | `state_revision`, `state_fingerprint`, `rule_id`, `rule_fingerprint` | Compute exactly one substitution using a selected, available verified rule, then independently check the result. |

`apply_verified_rule` is available only with `allow_rule_execution=True`. It uses the same first-match policy as a manual rule citation: try the whole current expression, then children left to right. A manual citation must provide exactly that substitution as `after`; additional simplification must use an empty rule ID. The executor does not automatically select another rule, expand remaining terms, invoke a CAS, or request an extra model response. Each selected action consumes one normal proposal/step.

The executable mode asks the model to prefer this action when a supplied identity matches an unexpanded current subexpression. The model still makes that choice; the prompt does not run a matcher or precompute a replacement. Evaluating this mode measures the combined effect of the available action and its selection instruction.

Only initially retrieved rules that are still verified and unchanged in the library may execute. Unknown, revoked, changed or mismatched records are rejected. AST growth is bounded before substitution; grammar and resource limits are checked afterward. Every result still passes independent exact rational-polynomial verification. A verified label or a model-provided fingerprint cannot bypass these checks.

Committed facts preserve the actual `before`, resulting `after`, rule ID and rule fingerprint. Completed chains use the existing distillation, independent admission and save/reload verification path. Failed or incomplete runs cannot promote a new rule. `learn=False` freezes learning for transfer evaluation.

The default `proposal_protocol="legacy"` preserves the existing callback contract. The deterministic proposer is unchanged. This interface addresses continuation and rule execution; it does not establish higher model accuracy or general mathematical capability by itself.

The local study runner `benchmarks.run_stateful_algebra_eval` compares the legacy interface, the new state-bound interface, and explicit rule execution using one frozen library and a common model. Its new expressions exclude the previous study's full task inventory. API requests require the explicit `run --live` option. Raw model traces belong outside the repository.
