# Residual search, checked schema learning, and incremental verification

This page records the earlier search-only development comparison. The newer
[compact/lifelong adapter](lifelong-math.md) resolves both long-expression
failures below with a different result representation and exact tool. Its
results are reported separately so the earlier baseline is not rewritten.

Available in v0.13.0, this opt-in experimental adapter connects the architecture
helpers to executable mathematics and bridge workflows. Existing adaptive
Agent defaults remain unchanged. Run the demonstration from this checkout:

```bash
PYTHONPATH=src python -m resimind demo --domain search-learning
```

## What is implemented

`domains.search_learning.run_search_learning` keeps a bounded queue of proposed
actions from multiple states. A failed action leaves alternatives available.
Dependency-ready polynomial subexpressions guide local rewrites; every accepted
edge and the final expansion require exact rational identity verification. A
search ranking is a heuristic, never evidence. Exhaustion returns `limited`
without releasing a partial expression as a solved answer.

```python
from resimind.domains.polynomial_learning import PolynomialProblem
from resimind.domains.search_learning import SearchBudget, run_search_learning

result = run_search_learning(
    PolynomialProblem("((x+y)**2+1)*(x-y)", ("x", "y")),
    budget=SearchBudget(actions=64, frontier=64, depth=32, model_calls=3),
)
assert result.status == "solved"
print(result.expression)
```

Pass a `complete(prompt) -> str` callback to propose model steps. The callback
must enforce its own network timeout and token limits. Replies have the form
`{"after": "...", "tactic": "grind"}`; the tactic is optional and only `grind`
and `rfl` are accepted. Schema errors, rejected identities, duplicate states,
and unfinished Lean goals enter subsequent prompts. Retry counts are global to
the run. Callback exception text is excluded from feedback to avoid exposing
transport credentials.

Pass `lean_backend=LeanPolynomialBackend()` to require the optional installed
Lean 4.29.0 compiler on every accepted rewrite, including already expanded
input. Source hash, target binding, variable mapping, compiled proof hash,
diagnostics and axiom policy must agree. Accepted proof steps retain the exact
generated `lean_source`, which can be saved as a standalone `.lean` file and
recompiled. This proves polynomial equalities over `Rat`; it does not implement
arbitrary Lean tactics, Mathlib theorem search, induction or lemma discovery.

`domains.schema_learning.discover_schemas` anti-unifies pairs of checked
rewrites: shared syntax stays, different subtrees become consistently shared
metavariables. It requires distinct source IDs, verifies every input equality,
then proves each proposed universal polynomial identity independently. A source
ID is provenance supplied by the caller, not proof of an independent source.
Unproved generalizations are rejected. This generalizer covers more patterns
than the older fixed distributivity extractor, but does not infer conditions.

```python
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.rule_abstraction import VerifiedExample
from resimind.domains.schema_learning import discover_schemas
from resimind.knowledge import KnowledgeLibrary

examples = (
    VerifiedExample("(x+y)**2", "x**2+2*x*y+y**2", ("x", "y"), "proof-a"),
    VerifiedExample("(u+v)**2", "u**2+2*u*v+v**2", ("u", "v"), "proof-b"),
)
library = KnowledgeLibrary({"algebra": AlgebraVerifier()})
for candidate in discover_schemas(examples).candidates:
    library.admit(candidate)  # Library admission performs its own check.
library.save("rules.json")
```

Applications check the current record and fingerprint, bound substitution size,
and verify the resulting equality. Revoking a record prevents subsequent use.
Training can collect `result.observations`, fit `StrategyPolicy.fit(...)`, then
pass the frozen policy into later runs. This is empirical strategy selection
outside model weights. The training/demo driver explicitly collects, admits and
persists schemas; the evaluation runner never silently learns from test tasks.

`domains.bridge_incremental.IncrementalBridgeAgent` uses the same obligation
graph. Changed evidence invalidates its consumers and their descendants. A
left-span live-load change rechecks affected cases; changing only supplied
limits rechecks comparisons. Changed case topology starts a fresh cache. Every
run validates the full declared input before releasing anything, and returned
JSON is detached from the cache. The scope is the existing synthetic two-span
Euler–Bernoulli model, with supplied limits and midspan deflection checks.

## Development comparison, 2026-10-13

These are 24 public within-family transfer tasks and eight public stress cases,
not a hidden benchmark. Eight training expressions include variable-renamed
pairs. Training used 90 attempted actions, yielding 90 observations and four
universally checked schemas; 24 other generalizations were rejected. The
policy and library were frozen before evaluation. Exact task strings differ
between training and evaluation, but mathematical families overlap.

| Arm | Independent exact passes / 32 | Stress passes / 8 | Attempted search actions | Solver seconds |
| --- | ---: | ---: | ---: | ---: |
| Frozen v0.12.1, default adaptive settings | 26 | 2 | Different step accounting | 0.208 |
| New search, no learned policy or library | 30 | 6 | 381 | 1.777 |
| Search + learned rules | 30 | 6 | 381 | 1.457 |
| Search + learned policy | 30 | 6 | 329 | 1.752 |
| Search + policy + rules | 30 | 6 | 329 | 1.454 |
| Same, without local residual targeting | 30 | 6 | 299 | 1.534 |
| Same, frontier restricted to one action | 20 | 0 | 402 | 1.561 |
| SymPy exact expansion | 32 | 8 | Not applicable | 0.290 |

All arms made zero model calls in this comparison. Evaluation uses a separate
degree-complete rational-grid oracle. CAS output has associative chains
balanced for the scorer's syntax limits; this formatting does not establish
correctness. Unknown oracle results never count as passes. There were no wrong
answers accepted in this finite set; this is not a universal reliability claim.

Search attempts, legacy committed steps, and CAS operations are **not equal
compute budgets**. Timing is one local run per arm, excludes scoring, includes
unsolved tasks, and is not a statistically stable speed estimate. Training cost
is reported separately (about 0.057 s here). Compared with the frozen baseline,
search improved completion by 12.5 percentage points while taking about seven
times as much solver time. Compared with the untrained search, the fitted
policy used 13.6% fewer actions. Learned rules added no further measured action
saving on this set. Removing local residual targeting used fewer actions here;
there is no evidence of a mathematical search benefit from that component yet.

The remaining failures are `(x+y+1)**10` and
`(x+y+1)**6*(2*x-y+1)**6`, within the current expression, action and frontier
bounds. SymPy solved both. The Agent architecture should be evaluated for
verified tool orchestration and reuse; these results do not establish a better
polynomial engine than a mature CAS.

For four successive synthetic bridge snapshots (initial, changed left load,
changed supplied limit, changed stiffness), fresh runs checked 8+8+8+8 cases.
Incremental runs checked 8+4+0+8: **32 → 20 checks, a 37.5% reduction**. Every
snapshot matched both fresh calculation and the independent rational stiffness
oracle. This measures case-check reuse, not wall-clock speed or real bridge
design validity.

Reproduce with optional SymPy installed and a frozen baseline checkout:

```bash
PYTHONPATH=src:. python -m benchmarks.run_search_learning_eval \
  --baseline-root /absolute/path/to/baseline \
  --output /absolute/path/outside/repository/new-evaluation
```

The runner writes source/task hashes before evaluation, stores training and
raw results outside the repository, and detects source changes during the run.
The baseline for the table was commit `84eeb72`; the saved source and task
manifest digest is
`be22b5049bb780cf3d582593149a698ad94cb1ace60b2551dcfb99d30d49f97f`.
Earlier development runs and failed network attempts were retained locally.

### Live callback check and regression status

Six public tasks were also sent to the configured DeepSeek endpoint. The
requested alias was `deepseek-chat`; responses identified `deepseek-flash`.
Each request used temperature 0 and at most 2,048 output tokens. Direct answers
had one call per task; verifier-retry and full search allowed at most three.

| Arm | Exact passes / 6 | API calls | Oracle unknowns |
| --- | ---: | ---: | ---: |
| Direct model answer | 3 | 6 | 1 |
| Model with exact-verifier feedback and retry | 3 | 14 | 0 |
| Full search with frozen library/policy and model fallback | 4 | 6 | 0 |

All four successful full-search tasks completed without a model call. Its six
calls were spent on the two unsolved tasks. The final run made 26 requests,
reported 38,226 total tokens and no transport errors. The exact scorer could
not certify one direct-model response within its bounds, so it remained
unknown rather than being counted as wrong or correct.

This was **development feedback**, not a pristine held-out trial. The first
successful-network run exposed unhelpful tactic-error feedback and missing
retries after duplicate states. After fixing these, verifier-retry improved
from 2/6 to 3/6; full search stayed at 4/6. Both runs were retained. Six tasks,
unequal call budgets, repeated prompts and overlapping task families cannot
support statistical superiority or a frontier claim. Raw prompts, responses,
usage and both source manifests remain local; no credentials are recorded.

The final full regression run passed **1,727 tests**, with **21 optional tests
skipped**. Real Lean compilation and tactic-repair tests ran on this machine.
Tests cover false identities, invalid model syntax, stale dependency results,
revoked rules, forged formal metadata, callback failures, budget exhaustion,
bridge input changes, independent numeric results and the new CLI workflow.

## Relation to published work

External knowledge growth without model retraining is established prior art:
[Voyager](https://arxiv.org/abs/2305.16291) uses an executable skill library and
environment feedback without changing model weights. ResiMind's bounded
polynomial schemas and exact admission checks are a different, narrower
implementation; this comparison is architectural, not a head-to-head result.

[DeepSeek-Prover-V2](https://arxiv.org/abs/2504.21801) combines formal subgoal
decomposition and reinforcement learning, with reported MiniF2F and PutnamBench
results. This project is an Agent runtime, not a trained theorem-proving model,
and does not yet run those proof benchmarks. Its polynomial test percentage
cannot be compared to their theorem-proving percentage. A frontier claim would
require broader theorem tasks, strong verifier-guided search baselines,
matched model/compute budgets, held-out evaluation, and independent reproduction.

The next technical priorities are a compact expression representation that
does not grow into long AST chains, a useful rule-retrieval/selection policy,
and evaluation on new task families. Keep this search adapter opt-in while
those performance and generalization questions remain open.
