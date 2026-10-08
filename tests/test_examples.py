"""Behavioral checks for the offline examples and their domain boundaries."""

from dataclasses import replace
import json

import pytest

from resimind import Candidate, Decision, Fact, State
from examples import document_review, inventory, route_memory


def test_inventory_rejects_wrong_answer_then_recomputes_and_commits() -> None:
    result = inventory.run_demo()
    assert result.status == "solved"
    assert result.residual.solved
    assert [event.decision for event in result.trace] == [
        Decision.REJECT, Decision.ACCEPT,
    ]
    rejected, accepted = result.trace
    assert rejected.reasons == ("arithmetic_claim_mismatch",)
    assert rejected.before == rejected.after == State()
    assert rejected.residual_before == rejected.residual_after
    assert accepted.after.revision == 1
    closing = next(fact for fact in result.state.facts if fact.metric == "closing_count")
    assert closing.value == 12
    assert closing.evidence_refs == tuple(item.id for item in inventory.make_evidence())
    assert json.loads(result.to_json())["status"] == "solved"


@pytest.mark.parametrize("changes", [
    {"subject": "different-widget"},
    {"scope": "synthetic-shift-0"},
    {"unit": "box"},
    {"metric": "unrelated_count"},
])
def test_inventory_rejects_evidence_from_wrong_semantic_slot(changes: dict) -> None:
    evidence = inventory.make_evidence()
    evidence = (replace(evidence[0], **changes),) + evidence[1:]
    candidate = Candidate("proposal", inventory.ACTION, inventory.TARGET,
                          "closing_count=12", tuple(item.id for item in evidence))
    verdict = inventory.InventoryVerifier().verify(
        candidate, State(), inventory.InventoryDomain().rebuild(State()), evidence,
    )
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("evidence_binding_mismatch",)
    assert not verdict.facts


@pytest.mark.parametrize("value", [True, -1, 10.0, "10"])
def test_inventory_rejects_noninteger_or_negative_counts(value) -> None:
    evidence = inventory.make_evidence()
    evidence = (replace(evidence[0], value=value),) + evidence[1:]
    candidate = Candidate("proposal", inventory.ACTION, inventory.TARGET,
                          "closing_count=12", tuple(item.id for item in evidence))
    verdict = inventory.InventoryVerifier().verify(
        candidate, State(), inventory.InventoryDomain().rebuild(State()), evidence,
    )
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("invalid_count",)


def test_inventory_defers_without_all_referenced_measurements() -> None:
    evidence = inventory.make_evidence()
    candidate = Candidate("proposal", inventory.ACTION, inventory.TARGET,
                          "closing_count=12", (evidence[0].id, evidence[1].id))
    verdict = inventory.InventoryVerifier().verify(
        candidate, State(), inventory.InventoryDomain().rebuild(State()), evidence,
    )
    assert verdict.decision is Decision.DEFER
    assert verdict.reasons == ("missing_inventory_measurement",)


def test_inventory_residual_checks_equation_independently() -> None:
    solved = inventory.run_demo().state
    tampered = State(revision=solved.revision, facts=tuple(
        replace(fact, value=99) if fact.metric == "closing_count" else fact
        for fact in solved.facts
    ))
    residual = inventory.InventoryDomain().rebuild(tampered)
    assert residual.goals == (inventory.TARGET,)
    assert residual.unknowns == ()
    assert residual.hard_constraints == ("inventory:balance_valid",)
    assert not residual.solved


def test_document_deferral_does_not_close_obligations() -> None:
    result = document_review.run_demo()
    assert result.status == "solved"
    assert [event.decision for event in result.trace] == [
        Decision.DEFER, Decision.ACCEPT,
    ]
    deferred = result.trace[0]
    assert deferred.before == deferred.after == State()
    assert deferred.residual_before == deferred.residual_after
    assert deferred.residual_after.unknowns
    assert result.state.revision == 1
    assert {fact.metric for fact in result.state.facts} == set(document_review.REQUIRED)


def test_document_with_unavailable_evidence_stalls_honestly() -> None:
    result = document_review.run_demo(include_review_date=False)
    assert result.status == "stalled"
    assert result.stop_reason == "max_no_progress"
    assert result.state == State()
    assert not result.residual.solved
    assert "document:missing:section:review_date" in result.residual.unknowns
    assert all(event.decision is Decision.DEFER for event in result.trace)


@pytest.mark.parametrize("changes", [
    {"subject": "different-document"}, {"scope": "draft-1"}, {"unit": "html"},
])
def test_document_checks_subject_revision_and_unit(changes: dict) -> None:
    evidence = document_review.make_evidence()
    evidence = (replace(evidence[0], **changes),) + evidence[1:]
    candidate = Candidate("proposal", document_review.ACTION, document_review.TARGET,
                          "required_sections_present", tuple(item.id for item in evidence))
    verdict = document_review.DocumentVerifier().verify(
        candidate, State(), document_review.DocumentDomain().rebuild(State()), evidence,
    )
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("evidence_binding_mismatch",)


def test_document_domain_ignores_other_revisions() -> None:
    state = State(facts=tuple(
        Fact(f"old:{metric}", document_review.SUBJECT, metric, "present", "text",
             (f"evidence:{metric}",), "draft-1")
        for metric in document_review.REQUIRED
    ))
    residual = document_review.DocumentDomain().rebuild(state)
    assert not residual.solved
    assert len(residual.unknowns) == len(document_review.REQUIRED)


def test_route_review_gates_reuse_without_reusing_the_answer() -> None:
    demo = route_memory.run_demo()
    assert demo["pending_routes"] == []
    assert demo["approved_routes"] == ["inventory-balance-v1"]
    assert demo["revoked_routes"] == []

    def closing(run: dict) -> int:
        assert run["status"] == "solved"
        return next(fact["value"] for fact in run["state"]["facts"]
                    if fact["metric"] == "closing_count")

    assert closing(demo["previous_run"]) == 12
    assert closing(demo["current_run"]) == 14
    assert [event["event"] for event in demo["memory_audit"]] == [
        "add", "review", "revoke",
    ]
    # The example exposes a complete JSON-serializable audit and both runs.
    assert json.loads(json.dumps(demo))["approved_routes"] == demo["approved_routes"]


def test_inventory_rejects_a_previous_answer_on_current_evidence() -> None:
    evidence = inventory.make_evidence(20, 3, 9)
    candidate = Candidate("cached-answer", inventory.ACTION, inventory.TARGET,
                          "closing_count=12", tuple(item.id for item in evidence))
    verdict = inventory.InventoryVerifier().verify(
        candidate, State(), inventory.InventoryDomain().rebuild(State()), evidence,
    )
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("arithmetic_claim_mismatch",)
