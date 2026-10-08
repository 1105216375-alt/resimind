# Residual Agent

**Models propose. Verifiers decide. Residual obligations drive the next step.**

A domain-independent **neuro-symbolic Agent architecture** combining tasks, evidence tools, model proposals, independent verification, residual-driven execution, and reviewed route memory. This Python framework is distilled from the neuro-symbolic collaboration, residual reasoning, and reviewed route memory used in the Bridge Doctor application. [中文](README.md)

**Experimental, v0.1.0.** This library coordinates verification. It does not prove arbitrary natural-language claims. The domain verifier and residual builder are trusted application code.

`Agent.run(Task)` collects evidence through registered tools, selects applicable reviewed routes, constructs domain adapters and proposers, and returns verified facts plus residual obligations. `ModelProposer` accepts a provider-neutral model callback; the complete Agent demo uses a deterministic fake callback for offline execution.

The internal loop is `residual → propose → verify → accept/reject/defer/interrupt → commit → rebuild`. A residual records outstanding goals, unknowns, and hard constraints. Candidates cannot declare themselves verified or mark the task complete. Verification results bind the candidate, state, and input evidence; only verifier-produced facts may be committed.

Python 3.10+; no runtime dependencies, model accounts, or network access required for the examples:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m examples.agent_demo
python -m examples.inventory
python -m examples.document_review
python -m examples.route_memory
python -m pytest -q
```

The synthetic inventory and document-review examples demonstrate independent recomputation, evidence scope checks, incorrect candidate rejection, and missing evidence deferral. Reviewed memory stores action routes, never automatically reusable conclusions. Approval and applicability are required; current evidence must still pass the verifier.

Implement `Proposer.propose`, `Verifier.verify`, and `Domain.rebuild` to add a domain. A model adapter can implement the proposer protocol; this release intentionally ships no provider-specific SDK integration. See the runnable [examples](examples), [architecture](docs/architecture.md), and [adapter guide](docs/adapters.md).

Included: task orchestration, registered evidence tools, structured model proposals, immutable contracts, result binding, atomic transitions, evidence references, conflict checks, four decisions, round-robin scheduling, attempt/stall budgets, JSON traces, and in-memory reviewed/revocable route memory.

Tools collect evidence before the loop; dynamic tool scheduling and automatic multi-role planning are not implemented.

Not included: model weights, arbitrary code execution, a theorem prover, learned memory, automatic distillation, persistence, distributed execution, or enforceable time/token/cost limits. Callbacks are trusted Python code and need external timeout/process isolation if necessary. Digests detect result mix-ups, not malicious verifiers. Evidence provenance labels are not authentication. Traces may contain application data; callers control storage and disclosure.

Only generic code and synthetic data are included; no original application records, credentials, private reports, or competition materials. See [provenance](docs/provenance.md), [contribution guidelines](CONTRIBUTING.md), and the [MIT license](LICENSE).
