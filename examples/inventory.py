"""Reconcile synthetic inventory with evidence-bound integer arithmetic.

Run from the project root after installation: ``python -m examples.inventory``.
The deterministic proposer deliberately submits a wrong answer first. An LLM
could replace it without receiving the verifier's authority to commit facts.
"""

from __future__ import annotations

from residual_agent import (
    Candidate, Decision, Engine, Evidence, Fact, Residual, RunResult, State,
    Verdict,
)

SUBJECT = "synthetic-widget"
SCOPE = "synthetic-shift-1"
UNIT = "item"
ACTION = "reconcile_inventory"
TARGET = "inventory:reconciled"
INPUTS = ("opening_count", "received_count", "dispatched_count")


def make_evidence(
    opening: int = 10, received: int = 6, dispatched: int = 4,
) -> tuple[Evidence, ...]:
    """Create synthetic observations; no files, network or private data are used."""
    return tuple(
        Evidence(f"evidence:{metric}", SUBJECT, metric, value, UNIT,
                 "synthetic-fixture", SCOPE)
        for metric, value in zip(INPUTS, (opening, received, dispatched))
    )


class InventoryDomain:
    """Rebuild obligations from committed quantities, including their equation."""

    def rebuild(self, state: State) -> Residual:
        quantities = {
            fact.metric: fact.value
            for fact in state.facts
            if fact.subject == SUBJECT and fact.scope == SCOPE
            and fact.unit == UNIT and type(fact.value) is int and fact.value >= 0
        }
        missing = tuple(
            f"inventory:missing:{metric}"
            for metric in (*INPUTS, "closing_count") if metric not in quantities
        )
        balanced = not missing and quantities["closing_count"] == (
            quantities["opening_count"] + quantities["received_count"]
            - quantities["dispatched_count"]
        )
        return Residual(
            goals=() if balanced else (TARGET,),
            unknowns=missing,
            hard_constraints=() if balanced else ("inventory:balance_valid",),
        )


class InventoryVerifier:
    """Bind every observation to the requested item, shift, metric and unit."""

    def verify(
        self, candidate: Candidate, state: State, residual: Residual,
        evidence: tuple[Evidence, ...],
    ) -> Verdict:
        def verdict(decision: Decision, reason: str, facts=()) -> Verdict:
            return Verdict.for_candidate(
                candidate, state, decision, evidence=evidence,
                facts=facts, reasons=(reason,),
            )

        if candidate.action != ACTION or candidate.target != TARGET:
            return verdict(Decision.REJECT, "unsupported_action_or_target")
        by_id = {item.id: item for item in evidence}
        measurements: dict[str, Evidence] = {}
        for reference in candidate.refs:
            item = by_id.get(reference)
            if item is None:
                return verdict(Decision.REJECT, "unknown_evidence_reference")
            if (item.subject != SUBJECT or item.scope != SCOPE
                    or item.unit != UNIT or item.metric not in INPUTS):
                return verdict(Decision.REJECT, "evidence_binding_mismatch")
            if type(item.value) is not int or item.value < 0:
                return verdict(Decision.REJECT, "invalid_count")
            if item.metric in measurements:
                return verdict(Decision.REJECT, "ambiguous_measurement")
            measurements[item.metric] = item
        if any(metric not in measurements for metric in INPUTS):
            return verdict(Decision.DEFER, "missing_inventory_measurement")
        closing = (measurements["opening_count"].value
                   + measurements["received_count"].value
                   - measurements["dispatched_count"].value)
        if closing < 0:
            return verdict(Decision.REJECT, "negative_closing_count")
        if candidate.claim != f"closing_count={closing}":
            return verdict(Decision.REJECT, "arithmetic_claim_mismatch")
        input_facts = tuple(
            Fact(f"fact:{metric}", SUBJECT, metric, measurements[metric].value,
                 UNIT, (measurements[metric].id,), SCOPE)
            for metric in INPUTS
        )
        output_fact = Fact(
            "fact:closing_count", SUBJECT, "closing_count", closing, UNIT,
            tuple(measurements[metric].id for metric in INPUTS), SCOPE,
        )
        return verdict(Decision.ACCEPT, "inventory_balance_verified",
                       input_facts + (output_fact,))


class InventoryProposer:
    """A replaceable, untrusted proposal source; it cannot add facts to state."""

    def __init__(self, evidence: tuple[Evidence, ...], *, wrong_first: bool = True,
                 action: str = ACTION) -> None:
        self.evidence = evidence
        self.wrong_first = wrong_first
        self.action = action
        self.attempt = 0

    def propose(self, state: State, residual: Residual) -> tuple[Candidate, ...]:
        self.attempt += 1
        values = {item.metric: item.value for item in self.evidence}
        closing = values.get("opening_count", 0) + values.get("received_count", 0)
        closing -= values.get("dispatched_count", 0)
        if self.wrong_first and self.attempt == 1:
            closing += 1
        return (Candidate(
            f"inventory-proposal:{self.attempt}", self.action, TARGET,
            f"closing_count={closing}", tuple(item.id for item in self.evidence),
        ),)


def run_demo() -> RunResult:
    evidence = make_evidence()
    return Engine(
        InventoryDomain(), InventoryVerifier(), (InventoryProposer(evidence),),
        evidence=evidence, max_steps=5, max_no_progress=3,
    ).run()


if __name__ == "__main__":
    print(run_demo().to_json())
