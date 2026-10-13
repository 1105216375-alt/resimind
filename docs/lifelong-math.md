# Verified compact computation and learning across episodes

Available in v0.13.0, this opt-in experimental adapter connects a bounded rule library, structural
retrieval, exact computation, empirical route selection and the earlier search
loop. It addresses concrete representation and integration failures. It does
not establish state-of-the-art general reasoning or theorem proving.

```bash
PYTHONPATH=src python -m resimind demo --domain lifelong
```

The demo actually solves two training problems, extracts a universally checked
schema, saves and reloads a frozen memory, transfers the schema to new
coefficients, and solves a long expansion with a compact certificate. It makes
no API calls and needs no optional package.

## Architecture and trust boundaries

The compact proposer parses the existing bounded grammar into an interned DAG.
Repeated subexpressions share a node. It computes sparse rational terms with
binary powering and explicit node, term, product, degree and coefficient bounds.
It does not call the equality verifier to obtain an answer.

The gate independently evaluates the original source with the existing
sequential-power exact ring verifier, then checks every proposed coefficient.
Sparse terms have a strict canonical schema: bound to the original source and
variable order, unique sorted powers, reduced nonzero rational coefficients,
positive denominators and explicit size limits. A single wrong coefficient or
wrong input binding prevents acceptance. Sampling does not authorize output.

The input AST limit remains 1,024 nodes. A result is an
`ExpansionCertificate`, with at most 4,096 sparse terms, rather than another
input expression that must fit the same AST size. Its rendered text is for
display; it may be too large to feed into legacy expression APIs. Very large
coefficient literals may also prevent rendering while the structured result
remains available. This separates input syntax, computation and result data
without removing their resource bounds.

Structural retrieval groups verified rules by expression shape and matches
their shared metavariables. The default index examines at most 256 records and
returns at most 16 applicable candidates. It fixes the earlier failure where
16 irrelevant entries could hide a later applicable rule. A cached match is
not authority: use still checks the live record, revocation and fingerprint,
then verifies the resulting equality.

`LifelongMathAgent.solve(..., learn=True)` records actual route outcomes and
bounded learning history. Pairs of successful rewrites produce candidate
schemas, which go through universal identity checking and library admission.
Rule learning is currently restricted to expressions that fit the existing
schema grammar; oversized printed answers report a learning error rather than
pretending a new rule was stored. History holds at most 32 examples and 4,096
route observations; the agent caps all retained rule records at 256.

The route policy is an empirical success/cost ranker learned from observed
latency, not a neural policy or reinforcement-learning breakthrough. Cold
ordering is recall → compact computation → search. A frozen policy can choose
the cheaper computation route ahead of recall. Tool failures and proof
feedback are classified conservatively into observed categories; these labels
are not learned causal explanations.

When Lean is required, compact exact-only computation is marked unavailable.
It cannot silently replace a required formal proof. Recall and search retain
the existing generated `Rat` equality proof gate. Search/model/Lean budgets are
shared across route changes. Compact proposal arithmetic has its own explicit
budget, and the independent ring checker retains its separate fixed bounds;
these are not interchangeable compute units or a single wall-clock budget.

## Train, freeze, use, and persist

```python
from resimind.domains.lifelong_math import LifelongMathAgent
from resimind.domains.polynomial_learning import PolynomialProblem

agent = LifelongMathAgent()
for text, variables in (("(a+b)**3", ("a", "b")),
                        ("(u+v)**3", ("u", "v"))):
    result = agent.solve(PolynomialProblem(text, variables), learn=True)

frozen = agent.freeze()
frozen.save("local-memory")
restored = LifelongMathAgent.load("local-memory")
result = restored.solve(PolynomialProblem("(2*x+3*y)**3", ("x", "y")),
                        routes=("recall",))
assert result.status == "solved"
assert result.model_calls == 0
```

Learning is explicitly disabled by default. A frozen snapshot refuses training,
is detached from its parent, and revalidates active rules. Reload rechecks the
rules and history; saved approval flags are not trusted. Metadata binds to the
rules-file digest so a mixed save generation fails closed. Use one writer per
store. Digests detect inconsistent files; they are not authentication against
someone who can rewrite the entire store. Route observations guide choice but
never establish mathematical truth.

## Development results, 2026-10-13

The source was frozen before generation of 240 unique tasks across seeds 91,
731 and 20261013. Each seed contains 16 tasks from each of five families:
power transfer, composed expressions, rational coefficients, shared cancelling
subexpressions and large output. Eight fixed training expressions, followed by
training-only route probes, produced four rules and 64 discovery/admission
checks. Training took about 0.023 s in this run and was excluded from evaluation
timing. The exact training strings do not appear in evaluation; polynomial
concepts overlap, and this is a development benchmark.

| Arm | Exact passes / 240 | Total solver seconds | Direct rule completions |
| --- | ---: | ---: | ---: |
| Previous frozen search implementation | 220 | 16.315 | Not separately recorded |
| Compact proposer + independent gate only | 240 | 0.099 | 0 |
| New router, empty memory | 240 | 0.276 | 0 |
| New router, learned memory, fixed recall-first order | 240 | 0.424 | 48 |
| New router, learned memory and frozen route policy | 240 | 0.255 | 0 |
| SymPy expand + polynomial output | 240 | 0.522 | Not applicable |

All arms made zero model calls. Every new-implementation family passed 48/48.
The prior search failed 12 large-output and eight shared-cancellation cases.
There were 20 paired completion gains and no losses. Final output was checked
against separate SymPy coefficients; factored answers, duplicate terms and
wrong coefficients have explicit scorer rejection tests. The CAS baseline and
this oracle share SymPy, so their agreement is not independent evidence about
SymPy itself. Separate degree-complete exact-grid tests independently certify
the eight older stress cases, now 8/8 including both previously unsolved ones.

The ablation matters: **compact computation explains the completion gain**.
Memory is capable of direct transfer, but it did not improve completion here.
Forcing memory first cost more than using the compact solver. The empirical
policy learned to favor compact computation; it did not invent a new method.
This is evidence for correct tool selection on these tasks, not evidence that
knowledge growth expands general mathematical capability.

These are single-machine descriptive times, not universal speed claims. The
prior implementation ran in a separate process; new arms were shuffled per
task, with a warmed CAS import and scoring after timed calls. Search actions,
arithmetic products and CAS work have different semantics. Changing from
repeated rewrite search to a dedicated exact tool is a method change, not a
fair claim that the search algorithm itself became 60 times faster. SymPy also
solved every task; the experiment does not establish superiority over CAS.

The full regression run passed **1,769 tests**, with **21 optional skips**.
Real installed Lean checks ran. A subsequent benchmark-manifest correction was
checked with the 42 compact/lifelong tests and the full evaluation rerun.

Reproduce with optional SymPy installed, a fresh output directory outside the
repository, and a snapshot of the prior search implementation:

```bash
PYTHONPATH=src:. python -m benchmarks.run_lifelong_eval \
  --baseline-root /absolute/path/to/prior-search-snapshot \
  --output /absolute/path/outside/repository/new-study
```

Raw records, earlier development runs, saved memory and the baseline snapshot
remain local. The measured implementation snapshot predates release metadata
and documentation changes; its source digest is
`61eb851beeadc951581c4b5de5989a1aaa61e9ad9674c097f6748f3d624275a5`;
the 240-task digest is
`3cb77e9fd96ff3450f94d95b6c72e859748a01fc162c0c9537df1100d6efd130`.
See the [research completion criteria](frontier-program.zh-CN.md) for what
remains unimplemented or unproven.
