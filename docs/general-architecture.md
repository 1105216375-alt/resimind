# General architecture extensions

ResiMind's core loop stays deliberately small:

```text
evidence → proposal → independent verification → atomic commit → residual rebuild
```

The three optional modules below make that loop easier to reuse in larger
applications. They do not replace a domain verifier and they cannot turn model
output, a heuristic, or a remembered trace into a fact.

## Dependency-aware residual obligations

`ObligationGraph` represents a residual as a directed acyclic graph. An
obligation can depend on another obligation and can list the evidence that
supports it. This is useful when a domain has a preparation step, a derived
result, and a final report rather than one flat list of strings.

```python
from resimind import Obligation, ObligationGraph, ObligationUpdate

graph = ObligationGraph((
    Obligation("input", "collect input", evidence=("e1",)),
    Obligation("derive", "derive result", depends_on=("input",)),
    Obligation("report", "prepare report", depends_on=("derive",)),
))
assert [item.id for item in graph.ready()] == ["input"]
graph = graph.update(ObligationUpdate("input", "verified"))
assert [item.id for item in graph.ready()] == ["derive"]
```

The graph only tracks work. `verified` is a status supplied by the calling
application after its own verifier has checked the obligation. `rejected`,
`unknown`, and `invalidated` remain unresolved; a caller can reopen a rejected
node as `pending` after changing the candidate or evidence. If evidence is
revised, `invalidate_evidence` marks the directly affected nodes and all their
dependents as `invalidated`, so the application can re-run the appropriate
slice. The graph never creates a `Fact` and is independent of `Engine`'s
transactional state.

## Transparent strategy scheduling

`StrategyPolicy` ranks already registered strategies from an explicit set of
`StrategyObservation` records. It uses a smoothed success rate with mean cost
as a secondary factor; strategies with too little evidence remain in caller
order for exploration. `StrategyPolicy.fit` returns a frozen policy, and only
frozen JSON snapshots can be loaded.

```python
from resimind import StrategyObservation, StrategyPolicy

policy = StrategyPolicy.fit((
    StrategyObservation("algebra", "symbolic", True, 1.0),
    StrategyObservation("algebra", "symbolic", True, 1.0),
    StrategyObservation("algebra", "model", False, 0.5),
    StrategyObservation("algebra", "model", True, 4.0),
))
order = policy.rank("algebra", ("model", "symbolic"))
```

This is empirical scheduling metadata, not a learned verifier, safety policy,
or proof of future success. The selected strategy still produces a normal
`Candidate`, and the ordinary `Verifier` must accept it before any state
changes.

## Structural rule abstraction

`resimind.domains.rule_abstraction` extracts a deliberately narrow rewrite
schema from at least two independently identified examples. The bundled
extractor recognizes the distributive shape `(A+B)*C → A*C+B*C`; it first
checks each example with the exact rational polynomial verifier and then runs
additional exact probes before returning an `AbstractRule`.

```python
from resimind.domains.rule_abstraction import VerifiedExample, extract_rules

rules = extract_rules((
    VerifiedExample("(x+y)*z", "x*z+y*z", ("x", "y", "z"), "case-a"),
    VerifiedExample("(p+q)*r", "p*r+q*r", ("p", "q", "r"), "case-b"),
))
```

The result is a candidate schema with provenance, not an automatically
admitted knowledge record. `apply_rule` rechecks every concrete substitution
with the exact verifier. Unsupported shapes, failed checks, and insufficient
independent examples return no rule or raise a validation error.

## How the pieces fit

Applications can keep `Engine` as the transaction boundary and use the
extensions around it:

```text
Task / Evidence
      ↓
ObligationGraph ── ready work ──→ proposer / StrategyPolicy
      ↓                                ↓
  domain verifier ← Candidate ← model, rules, or tools
      ↓
  accepted Fact → Engine commit → residual rebuild
      ↓
  verified traces → rule abstraction / knowledge admission
```

The architecture remains provider-neutral. A model can propose text or JSON,
rules can propose deterministic steps, and a scheduler can choose an order;
only the independent domain verifier can authorize committed facts.
