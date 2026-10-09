# Compact expressions and proof feedback inside the Agent loop

The adaptive polynomial Agent now has two additional symbolic tools and an
optional Lean proof gate. The existing exact verifier still checks every
candidate; selecting a tool never makes its output a fact.

## Keep intermediate expressions small

```bash
python -m resimind demo --domain growth-control
```

This offline example expands four quadratic trinomials using the original
primitive strategy and the new growth controls. Both start with an empty rule
library, zero model calls, and the same 64-action limit. It prints completion,
actions and peak AST nodes, including an unfinished baseline. This is one
published development example, not a held-out model benchmark.

The new tools are:

- **Compaction:** combine exact rational coefficients and like terms in already
  expanded subexpressions; compress repeated variable products into bounded
  powers. Return a candidate only if its AST-node/length ordering shrinks.
- **Bounded distribution:** rewrite one deepest eligible product or one power
  decomposition. Both multiplication operands must already be expanded;
  combine its generated terms before inserting the result into the whole
  expression. At most 256 term pairs are considered per local batch.

These tools independently generate candidates. They do not ask the identity
verifier for an expected answer. Every full-expression result still passes the
state-bound exact verifier. The 1,024-node, 64-depth, 8,192-character and
64-action limits are unchanged. Optional tool limits do not narrow the
existing problem grammar: an unsupported compaction attempt can fall through
to the original strategies.

Growth control is enabled by default in `build_adaptive_learning_agent`.
Set `control_expression_growth=False` to use the original primitive path.
The historical v5 evaluation runner explicitly disables the new tools to
preserve its controller treatment. New experiments should disclose the added
tools and measure local work as well as model calls.

## Let Lean feedback drive the next proposal

Install Lean **4.29.0** using the [official installation guide](https://lean-lang.org/install/),
then run:

```bash
python -m resimind demo --domain lean
```

The model responses are scripted, while all Lean checks execute the actual
local compiler. The first correct algebraic proposal requests `rfl`, which
does not close this equality. The uncommitted proposal's actual Lean goal and
diagnostics enter the next `proof_model` request. The script selects `grind`
in response; the independently checked proof then permits the commit. The
completed derivation is admitted, saved, reloaded, and used in a new-variable
task whose rule application also needs a Lean proof.

```python
from resimind import KnowledgeLibrary, Task
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import PolynomialProblem
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.integrations.lean import LeanPolynomialBackend

stats = AdaptiveStats()
learner = build_adaptive_learning_agent(
    PolynomialProblem("(x+1)*(x+2)*(x+3)*(x+4)", ("x",)),
    KnowledgeLibrary({"algebra": AlgebraVerifier()}),
    lean_backend=LeanPolynomialBackend(), stats=stats,
    max_steps=64, max_model_calls=8, max_lean_checks=16,
    # complete=your_completion_callback,
)
outcome = learner.run(Task("compact-lean", "Expand and verify each step", "algebra"))
print(outcome.result.run_result.status, stats.lean_checks)
```

With Lean enabled, a model may return `{"after": "...", "tactic": "rfl"}` or
`{"after": "...", "tactic": "grind"}`. Omitting `tactic` selects `grind`.
For a local edit, Lean checks the resulting **whole-expression** equality.
Proof feedback includes the current state binding, proposed result, actual
Lean target, remaining goals and diagnostics. Long feedback is explicitly
marked as truncated. It is proposal guidance, not an accepted fact or an
extra independently proved subgoal in the core `Residual` object.

An unfinished proof triggers the `proof_incomplete` failure kind. The
controller can request a new model proposal or use a bounded `lean_retry`
with `grind` for the same pending candidate. Each action and model call still
uses its normal budget; every backend request uses `max_lean_checks`.
Budget exhaustion and a missing Lean installation leave the run unfinished.
An enabled Lean gate never silently downgrades to exact-only acceptance.

The compiled equality must bind to the exact current candidate. A retrieved
rule is checked again for revocation after the external Lean call, before
commit. The audit records the Lean version, tactic, source and compiled-proof
hashes, standard axioms and feedback. Temporary compiler artifacts are not
retained; hashes alone are not independently checkable proof files.

## What is formally checked

This adapter translates the supported rational-polynomial AST to an equality
over Lean's `Rat`. It accepts only fixed `rfl` and built-in `grind` tactics;
it does not run model-written Lean programs. `grind` includes a ring solver;
this integration does not require Mathlib or expose Mathlib's `ring_nf`.
See [Lean's tactic proof model](https://lean-lang.org/doc/reference/latest/Tactic-Proofs/)
and [proof validation](https://lean-lang.org/doc/reference/latest/ValidatingProofs/).

Success requires compiler acceptance, complete diagnostics, a compiled proof
artifact and an axiom report for the generated theorem. Only Lean's standard
`propext`, `Classical.choice` and `Quot.sound` axioms are allowed. Unfinished
goals, `sorryAx`, unexpected warnings, truncated output and timeouts cannot
authorize a commit. The compiler installation and its bundled libraries are
trusted; see [backend details](../integrations/lean/README.md).

Rule admission and reload continue to use the library's exact algebra
verifier. `lean_backend` adds formal checking to task steps and new rule
applications; it does not change the stored rule format or claim that an old
library was previously checked with Lean. `learn=False` still freezes new
admissions.

This is bounded polynomial reasoning. Natural-language formalization,
arbitrary theorem search, learned tactic policies and general Lean project
editing are outside this adapter's current scope.
