![ResiMind — let AI explore, verify what you trust](docs/assets/banner.svg)

# ResiMind

**Let AI explore. Verify what you trust.**

ResiMind is a **standalone neuro-symbolic Agent architecture** for turning model proposals into checked results. The model explores possible steps. Independent code checks your domain's explicit rules. Rejected proposals leave committed facts unchanged; unfinished obligations — the **residual** — guide what happens next.

**Neural proposals → symbolic checks → committed facts → residual feedback.**

Plan a day with many possible answers. Check a refund against a merchant policy. Verify an optimization certificate or a continuous-beam calculation. Keep your model; make the acceptance rules executable.

**Python 3.10+ · Standalone Agent · Zero-dependency core · MIT · Experimental v0.7.0**

[中文](README.zh-CN.md) · [Quick start](#try-it-in-three-minutes) · [Open-ended case](#open-ended-planning-many-answers-explicit-constraints) · [Pilot evaluation](docs/evidence/planning-pilot-v1/README.md) · [Related systems (中文)](docs/public-systems-comparison.zh-CN.md) · [Architecture](docs/architecture.md) · [Optional integrations](#optional-integrations)

[**6 runnable domains**](#included-domain-adapters) · [**660 tests passed locally for v0.7.0**](docs/validation.md) · [**Inspect real DeepSeek proposals and decisions**](docs/evidence/open-planning/README.md)

**Measured on original ChinaTravel tasks:** in a frozen 12-task pilot with the same DeepSeek model and resource ceilings, official ReAct passed **0/12**, official NeSy **5/12**, and ReAct + ResiMind **1/12**. ResiMind delivered no invalid plan but left 11 tasks unfinished. [Results, raw records, and limits →](docs/evidence/chinatravel-pilot-v1/README.md)

**Development update:** [bounded search](docs/bounded-search.md) now complements [local repair](docs/local-repair.md): explore alternatives, then release only a fully checked plan. A travel replay resolves one seen task with explicit manual semantic annotations; the conservative default remains 0/2. [Results, limitations and reproduction →](docs/search-development.zh-CN.md)

**Project creator and original publisher: [@1105216375-alt](https://github.com/1105216375-alt).** [Original repository](https://github.com/1105216375-alt/resimind) · [Citation](CITATION.cff)

## Open-ended planning: many answers, explicit constraints

**The request:** “Plan a relaxed day with art and a meal. I like quiet places and coffee.”

The supplied hard constraints are **09:00–18:00, ¥300 for one person, at most 45 minutes walking, at least three distinct stops, art and a meal, and a return to the starting station**. Opening windows, minimum visit durations, and every travel leg must also fit. Quietness and coffee begin as preferences; the later experiment makes coffee mandatory.

### Same request, different valid answers

The model can choose places, order, timing, and transport. The verifier accepts a whole plan that satisfies the constraints, without comparing it to one fixed answer. These two independently checked offline examples satisfy the same original request:

| Feasible plan | Cost | Walking | Return |
| --- | ---: | ---: | --- |
| Gallery → noodles → reading room | ¥61 | 22 min | 13:38 |
| Cafe → pottery workshop → reading room | ¥95 | 8 min | 13:43 |

Venue and transport data are fictional. These checks establish feasibility under the supplied inputs; enjoyment and the “best day” remain subjective. [Inspect the schedules and transport choices](docs/open-planning.md#try-the-three-scenarios).

### A constraint fails: inspect the feedback and revision

![Actual offline planning trace: reject an excessive walk, then verify the revised itinerary](docs/assets/planning-demo.svg)

| Offline scenario | What happens |
| --- | --- |
| Too much walking | Reject **50 minutes > 45 minutes** without changing committed facts; revised transport reduces walking to **22 minutes**, and the plan passes. |
| Rain requires indoor venues | Reject the garden visit; a revised indoor itinerary passes. |
| Return-route measurements are missing | Keep the transport evidence unresolved; no verified itinerary is produced. |

These are executable **offline fixtures**, with deliberately flawed candidates. The checks and state transitions really execute; these mistakes are not attributed to a live model.

**The neuro-symbolic interaction:** neural proposals explore the choices, symbolic checks enforce the explicit constraints, and residual feedback tells the next proposal what still needs fixing or evidence.

### Change the requirement. Watch the plan change.

A real DeepSeek run proposed **gallery → noodles → reading room**. It satisfied the original constraints. We then made **coffee mandatory** in the structured requirements and resubmitted that actual plan:

| Step | What happened |
| --- | --- |
| Recheck the old plan | **Rejected:** a required activity was missing. No facts committed. |
| Give the feedback to DeepSeek | **One new model call** chose gallery → reading room → cafe. |
| Independently check the revision | **Accepted:** ¥73, 35 minutes walking, return at 13:04; all supplied hard constraints passed. |

The model chooses among possible plans; the verifier enforces the requirements. The cafe covers both a meal and coffee in the fictional catalog. This recorded experiment uses synthetic venue and transport data; the check establishes feasibility under those inputs.

[**Inspect both proposals, the rejection, and the checked revision →**](docs/evidence/open-planning/README.md)

[**Full planning rules and all three offline scenarios**](docs/open-planning.md) · [**Runnable example**](examples/open_planning.py) · [**Real DeepSeek audits**](docs/evidence/open-planning/README.md)

## Try it in three minutes

With Python installed:

```bash
git clone https://github.com/1105216375-alt/resimind.git
cd resimind
python -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m resimind demo --domain planning
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`. Installation may download build tools. The demo runs **offline, without an API key**; both `python -m resimind` and the installed `resimind` command work outside the checkout.

The default planning example runs the rejection-and-revision sequence [shown above](#open-ended-planning-many-answers-explicit-constraints).

Choose a different challenge. All four commands run offline:

| Try | What the example makes visible | Command |
| --- | --- | --- |
| [Open-ended planning](docs/open-planning.md) | Many valid answers; time, budget, and route constraints still apply | `python -m resimind demo --domain planning` |
| [Customer support](docs/customer-support.md) | A ¥259 refund proposal fails the configured ¥249 calculation | `python -m resimind demo --domain customer-support` |
| [Mathematics](docs/constrained-optimization.md) | A lower objective is useless if the candidate violates a constraint; check an exact optimality certificate | `python -m resimind demo` |
| [Bridge engineering](docs/continuous-bridge.md) | Balanced forces can still hide incompatible rotations at a shared pier | `python -m resimind demo --domain bridge` |

Add `--json` to inspect the audit. [Connect DeepSeek](docs/open-planning.md#let-deepseek-choose-the-plan) when you want the model to compose its own plan.

## Use the standalone Agent

ResiMind owns the reasoning loop: collect evidence, propose, verify, commit facts, and rebuild remaining obligations. Run it directly:

```python
from resimind import Task
from resimind.domains.planning import DOMAIN, build_agent, demo_problem, verified_plan

agent = build_agent(demo_problem())  # Offline; inject complete=your_model for neural proposals.
result = agent.run(Task("day-out", "Plan a relaxed day with art and a meal.", DOMAIN))
run = result.run_result
if run.status == "solved" and run.residual.solved:
    print(verified_plan(result))
else:
    print("Still unresolved:", run.residual.pending)
```

Model SDKs and external workflow connectors are [optional integrations](#optional-integrations). No LangGraph installation is needed for this Agent or the built-in domain demos.

## Customer support: check a refund before promising one

A customer asks to return an order. The recorded item payment is **¥249**, with **¥10 shipping**. Under the example merchant's configured policy, shipping is excluded from this quote.

| Situation | What the Agent does |
| --- | --- |
| Candidate proposes **¥259** | Rejects the amount; the incorrect proposal changes no committed facts. |
| Corrected candidate proposes **¥249** | Verifies eligibility and amount, then produces a structured recommendation. |
| Delivery date is missing | Keeps the required evidence pending; no completed refund recommendation. |
| Request is outside the configured window | Recommends human review; does not invent a policy exception. |

```bash
python -m resimind demo --domain customer-support
python -m resimind demo --domain customer-support --scenario missing-delivery
python -m resimind demo --domain customer-support --scenario expired
```

These are offline demonstrations using fictional merchant rules and synthetic orders. The Agent prepares a recommendation; it does not execute a refund or claim that money has arrived. A missing-evidence run exits nonzero because the task is still open. You can also connect a real DeepSeek callback.

[Read the customer-support adapter and live example →](docs/customer-support.md) · [Actual DeepSeek run: 5 calls, 2 rejected proposals, verified recommendation](docs/evidence/customer-support/README.md)

## Mathematics: a solution is not yet a proof

![Actual offline optimization trace: reject, correct, certify](docs/assets/verification-demo.gif)

[Static trace](docs/assets/verification-demo.png). This is an executed offline fixture with a deliberately infeasible candidate.

Minimize a three-variable quadratic with coupled terms, an equality constraint, nonnegativity, and an upper bound:

$$
\min_x\;2x_1^2+x_1x_2+x_2^2+x_3^2-8x_1-3x_2-3x_3,
\quad x_1+x_2+x_3=3,\quad x\geq0,\quad x_1\leq1.
$$

![Constrained optimization: infeasible proposal and certified optimum](docs/assets/optimization.svg)

The initial equality-stationary proposal is **(26/15, 1/5, 16/15)**. Its objective is lower, but it violates `x1 <= 1`: the verifier rejects it. After feedback, the Agent proposes **(1, 3/4, 5/4)** with objective **−73/8**.

That answer alone cannot close the task. The verifier must check exact **LDLᵀ positive-definiteness, primal feasibility, KKT stationarity and complementarity, and a polynomial global-optimality certificate**. All arithmetic uses rational numbers. The offline proposer searches active sets; the verifier checks the submitted certificate without running that search.

```bash
python -m examples.constrained_optimization
python -m examples.constrained_optimization --json
```

```text
ACCEPT certify_convexity   → positive_definiteness_verified   (2 remaining)
REJECT certify_primal_dual → primal_inequality_violation      (2 remaining)
ACCEPT certify_primal_dual → primal_and_kkt_verified          (1 remaining)
ACCEPT certify_global     → global_gap_identity_verified     (0 remaining)
```

[Read the problem, proof, and failure cases →](docs/constrained-optimization.md)

## Bridge engineering: continuity changes the answer

Analyze a synthetic **24 m + 30 m two-span continuous bridge girder** with unequal flexural stiffness, dead load, left/right/both-span live loading, and two supplied load combinations: **eight cases in total**.

![Continuous bridge: structure and bending-moment load-case envelope](docs/assets/continuous-bridge.svg)

The first proposal treats the spans as independent simply supported beams. It can balance forces and still be wrong: the rotations at the shared pier must agree. The verifier checks the beam equations and displacement compatibility before accepting the corrected continuous-beam solution.

The Agent then has to account for every required load case, calculate support reactions and positive/negative moment extrema, form the case envelope, and compare supplied moment and **midspan-deflection** limits. Omitting a pattern leaves outstanding obligations; a checked limit exceedance remains a valid completed analysis.

```bash
python -m examples.continuous_bridge
python -m examples.continuous_bridge --json
```

| Result | Value | Governing pattern |
| --- | ---: | --- |
| Pier hogging moment | −7,281.94 kN·m | Both spans |
| Span AB sagging maximum | +3,403.57 kN·m | Left span |
| Span BC sagging maximum | +6,241.85 kN·m | Right span |

[Read the structural model, equations, and limits →](docs/continuous-bridge.md)

> Both showcases execute real checks with deterministic offline proposers and deliberately invalid candidates. Supply a model callback to use neural proposals. These runs demonstrate verification behavior, not LLM accuracy. The bridge is a synthetic equivalent line-beam example with supplied limits, not a design-code assessment; its midpoint checks are not a global deflection envelope.

## Included domain adapters

| Adapter | Independent checks | Completion requires |
| --- | --- | --- |
| [Open-ended planning](src/resimind/domains/planning.py) | Budget, time windows, route continuity, visit coverage, walking and indoor requirements | Any feasible itinerary with grounded transport; subjective preferences stay unverified |
| [Customer support](src/resimind/domains/customer_support.py) | Scoped order evidence, configured policy, exact refund arithmetic | A checked recommendation or human-review outcome; missing evidence stays open |
| [Constrained optimization](src/resimind/domains/optimization.py) | Exact factorization, feasibility, KKT, polynomial certificate | A verified global optimum certificate |
| [Continuous bridge](src/resimind/domains/bridge.py) | Equilibrium, curvature and compatibility, all load cases, moment extrema | Complete case envelopes and supplied-limit comparisons |
| [Linear equation](src/resimind/domains/mathematics.py) | Rational normalization, solving and substitution | Original-equation checks, including no-solution/identity cases |
| [Axial bar](src/resimind/domains/engineering.py) | Declared assumptions, dimensions, nominal stress and supplied limit | Verified stress and comparison |

`solved` means the declared obligations are complete. It does not automatically mean a feasible solution exists or an engineering limit is satisfied. Each adapter has a bounded scope; the shared core makes those checks explicit.

## Where the neuro-symbolic part lives

**Neural proposals.** `ModelProposer` accepts a provider-neutral `complete(prompt: str) -> str` callback. A real model can choose a registered action and propose a claim with evidence references. The domain builders accept `build_agent(problem, complete=your_complete)`. The callback receives current state, outstanding obligations, evidence, allowed actions, and the last verifier feedback; it returns a JSON candidate or `null`.

**Symbolic checks.** Trusted domain code independently recomputes results and checks references, prerequisites, scope, exact rational arithmetic, and units. The verifier creates the facts that may be committed. A model cannot grant itself verification with a confidence score or a `verified` field.

**Residual feedback.** A residual is the set of outstanding goals, unknowns, and hard constraints. It is rebuilt from committed facts. A rejection preserves state and returns its reason to the proposer; a missing prerequisite keeps the task open.

This is a **neuro-symbolic architecture with an injectable neural component**. It ships no trained weights, neural training pipeline, or external-model benchmark. It is not a general theorem prover. See the [implementation map and boundaries](docs/neuro-symbolic.md).

## The architecture

```mermaid
flowchart LR
    T[Task] --> A[Agent]
    A --> E[Evidence tools]
    A --> P[Model / rule proposer]
    E --> P
    M[Reviewed route memory] --> P
    R[Residual obligations] --> P
    P --> C[Candidate + references]
    C --> V[Independent verifier]
    E --> V
    V -->|accept + binding checks| F[Commit verified facts]
    V -->|reject / defer + feedback| P
    V -->|interrupt| H[Stop and hand off]
    F --> D[Rebuild residual]
    D --> R
    F --> O[Facts + JSON audit trail]
```

- **Evidence before claims:** registered tools collect typed inputs for each task.
- **Verification before state changes:** verdicts bind to candidate, state, and evidence; commits check references and conflicts atomically.
- **Explicit unfinished work:** goals, unknowns, and hard constraints remain visible until discharged by the domain adapter.
- **Reusable routes:** in-memory routes require review and matching applicability; reused steps still need verification on current inputs.
- **Inspectable execution:** every attempt records its decision, reason, state, and residual; attempt and stall budgets bound the loop.

## Build your own adapter

Implement three small interfaces:

| Interface | Your responsibility |
| --- | --- |
| `Proposer.propose(...)` | Suggest the next candidate using a model, rules, or retrieval |
| `Verifier.verify(...)` | Check the current inputs and candidate independently; return `accept`, `reject`, `defer`, or `interrupt` |
| `Domain.rebuild(...)` | Recompute all remaining obligations from committed facts |

Assemble them with `Agent`, `Task`, registered `EvidenceTool` objects, and optional `RouteMemory`. The core does not import either reference domain. See the [adapter guide](docs/adapters.md) and [domain contract](docs/domain-contract.md) for connecting your own calculation, simulation, rule checker, or proof tool. These detailed guides are currently in Chinese; [neuro-symbolic.md](docs/neuro-symbolic.md) provides an English implementation map.

## Optional integrations

The standalone ResiMind Agent remains responsible for proposal, verification, and residual feedback. Add only the integration your application needs:

| Integration | Purpose | Extra |
| --- | --- | --- |
| [DeepSeek](docs/live-model.md) | Supply real neural proposals to any domain builder. | `.[deepseek]` |
| [OpenAI Responses](docs/live-model.md#optional-openai-responses-path) | Use an alternative model callback. | `.[openai]` |
| [LangGraph](docs/langgraph.md) | Call ResiMind from an existing graph and route completed or unresolved results. | `.[langgraph]` |

For live planning, run from a checkout:

```bash
python -m pip install '.[deepseek]'
# Set DEEPSEEK_API_KEY and DEEPSEEK_MODEL in your local environment first.
python -m examples.open_planning --live --json
```

Live calls can incur provider charges and can remain unresolved. The model guide documents request budgets and timeouts; there is no offline fallback after a failed live request. [Validation](docs/validation.md) distinguishes actual live records from simulated transport tests.

**LangGraph is an optional outer connector.** The ResiMind core and all domain adapters run without it. Use its [verification subgraph](docs/langgraph.md) when integrating with an existing LangGraph project; it forwards checked facts or unresolved obligations and does not supply additional domain verification rules.

## More examples and checks

```bash
python -m examples.agent_demo       # task → tool → model callback → verifier
python -m examples.inventory        # independent recomputation and rejection
python -m examples.document_review  # missing evidence stays unresolved
python -m examples.route_memory     # review, applicability, and revocation
python -m pip install -e '.[dev]'
python -m pytest -q
```

The [validation record](docs/validation.md) describes local checks and their scope. No external-model performance or production reliability is claimed.

## Current scope

ResiMind is an experimental, synchronous Agent framework. Tool evidence is collected **before** the loop. Optional DeepSeek/OpenAI model callbacks and a LangGraph verification subgraph are included. Dynamic tool scheduling, automatic multi-role planning, CAS/SMT/prover connectors, learned memory, persistence, and distributed execution are not included.

Domain verifiers and residual builders are trusted application code; their correctness determines what `solved` means. Evidence labels do not authenticate real-world inputs. Digests catch result mix-ups, not malicious plugins. Callers must enforce model/tool timeouts and resource limits; step budgets cannot interrupt a blocked callback. The optional model wrappers bound response tokens and API attempts; these are not a total-cost cap or a hard deadline for the full run.

This repository contains a generic implementation and synthetic examples distilled from the Bridge Doctor application's workflow. It contains no original application records, customer data, credentials, or private model logs. Read [provenance](docs/provenance.md), [architecture and trust boundaries](docs/architecture.md), and [security](SECURITY.md).

## Authorship and citation

ResiMind was initiated and originally published by [@1105216375-alt](https://github.com/1105216375-alt), drawing on the creator's Bridge Doctor application. The original repository is [1105216375-alt/resimind](https://github.com/1105216375-alt/resimind); its [releases](https://github.com/1105216375-alt/resimind/releases) record published versions. See [provenance](docs/provenance.md) for the extraction scope.

If you use or discuss ResiMind, please cite the project and link to the original repository. Suggested citation:

> 1105216375-alt. ResiMind (version 0.7.0), 2026. https://github.com/1105216375-alt/resimind

Machine-readable citation metadata is provided in [CITATION.cff](CITATION.cff). Citation is appreciated, not an additional license condition. Commercial use is permitted under the [MIT License](LICENSE), which requires retaining its copyright and permission notices in copies or substantial portions of the software.

New domain adapters, counterexamples, and verifier failure tests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
