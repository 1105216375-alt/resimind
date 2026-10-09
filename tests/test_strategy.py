"""Strategy choices cannot reset budgets, authorize facts, or hide repetition."""
from dataclasses import FrozenInstanceError
import json

import pytest

from resimind.strategy import FailureKind, StrategyController, candidate_fingerprint


STRATEGIES = ("whole_model", "local_model", "verified_rule", "primitive")


def close(controller, strategy, candidate="x", *, state="expr:A", **kwargs):
    options = dict(accepted=False, progress=False, reasons=("identity_mismatch",),
                   failure_kind=FailureKind.IDENTITY_MATH)
    options.update(kwargs)
    return controller.record(state, strategy, candidate, **options)


def test_identity_failure_rotates_through_distinct_available_strategies():
    controller = StrategyController(STRATEGIES)
    for index, expected in enumerate(STRATEGIES):
        assert controller.select("expr:A", STRATEGIES) == expected
        event = close(controller, expected, f"candidate:{index}")
        assert event.failure_kind is FailureKind.IDENTITY_MATH
    assert controller.select("expr:A", STRATEGIES) == "whole_model"
    assert [event.strategy for event in controller.events if event.event == "selected"] == [*STRATEGIES, "whole_model"]


def test_failure_limit_is_per_semantic_state_and_cannot_be_reset_by_revision_or_branch():
    controller = StrategyController(("rule",), max_attempts=9, max_attempts_per_strategy=9,
                                    max_failures_per_strategy=2, branch_budgets={"left": 5, "right": 5})
    for revision, branch in ((1, "left"), (900, "right")):
        assert controller.select("same-expression", ("rule",), branch=branch) == "rule"
        controller.record("same-expression", "rule", f"bad:{revision}", accepted=False,
                          progress=False, revision=revision)
    assert controller.select("same-expression", ("rule",), branch="left") is None
    assert controller.events[-1].reasons == ("state_strategy_budgets_exhausted",)
    assert controller.select("different-expression", ("rule",), branch="right") == "rule"
    controller.abandon()
    assert controller.snapshot()["attempts"] == 3


def test_global_limit_survives_different_states_branches_and_checkpoints():
    controller = StrategyController(("rule",), max_attempts=2,
                                    branch_budgets={"one": 2, "two": 2})
    for state, branch in (("A", "one"), ("B", "two")):
        assert controller.select(state, ("rule",), branch=branch, checkpoint={"saved": "A"}) == "rule"
        controller.record(state, "rule", state, accepted=True, progress=True)
    assert controller.select("fresh", ("rule",), branch="two", checkpoint={"saved": "other"}) is None
    assert controller.events[-1].reasons == ("global_attempt_budget_exhausted",)
    assert controller.snapshot()["attempts"] == controller.snapshot()["progress"] == 2


def test_branch_budget_is_shared_across_strategies_and_states():
    controller = StrategyController(STRATEGIES, branch_budgets={"limited": 1, "other": 3})
    controller.select("A", STRATEGIES, branch="limited")
    controller.abandon(failure_kind=FailureKind.RESOURCE)
    assert controller.select("B", ("primitive",), branch="limited") is None
    assert controller.events[-1].reasons == ("branch_attempt_budget_exhausted",)
    assert controller.select("B", ("primitive",), branch="other") == "primitive"
    assert controller.snapshot()["branch_attempts"] == {"limited": 1, "other": 1}


def test_successful_attempts_still_exhaust_strategy_and_global_budgets():
    controller = StrategyController(("rule",), max_attempts_per_strategy=2)
    for rank in (5, 3):
        assert controller.select("A", ("rule",)) == "rule"
        controller.record("A", "rule", f"equivalent:{rank}", accepted=True, progress=True, rank=rank)
    assert controller.select("A", ("rule",)) is None
    assert controller.snapshot()["states"]["A"]["strategies"]["rule"] == {
        "attempts": 2, "failures": 0, "progress": 2}


def test_available_filter_can_request_smaller_fallback_without_spending_an_extra_attempt():
    controller = StrategyController(STRATEGIES)
    controller.select("A", STRATEGIES)
    controller.abandon(failure_kind=FailureKind.RESOURCE)
    assert controller.select("A", ("primitive", "verified_rule")) == "primitive"
    assert controller.snapshot()["attempts"] == 2


def test_same_reservation_is_idempotent_but_cannot_change_state_or_metadata():
    controller = StrategyController(STRATEGIES)
    checkpoint = {"path": ["root"], "remaining": 2}
    selected = controller.select("A", STRATEGIES, checkpoint=checkpoint)
    assert controller.select("A", STRATEGIES, checkpoint=dict(checkpoint)) == selected
    assert controller.snapshot()["attempts"] == 1
    assert len(controller.events) == 1
    with pytest.raises(ValueError, match="pending"):
        controller.select("B", STRATEGIES)
    checkpoint["path"].append("changed")
    with pytest.raises(ValueError, match="pending"):
        controller.select("A", STRATEGIES, checkpoint=checkpoint)
    assert controller.snapshot()["pending"]["checkpoint"]["path"] == ["root"]


def test_checkpoint_and_snapshot_are_detached_audit_data():
    controller = StrategyController(("rule",), branch_budgets={"main": 2})
    checkpoint = {"branch": ["root"], "facts_digest": "abc"}
    controller.select("A", ("rule",), checkpoint=checkpoint)
    checkpoint["branch"].append("mutated")
    event = controller.record("A", "rule", "proof", accepted=True, progress=True)
    data = event.to_dict()
    data["checkpoint"]["branch"].append("again")
    snapshot = controller.snapshot()
    json.dumps(snapshot, allow_nan=False)
    snapshot["states"]["A"]["strategies"]["rule"]["attempts"] = 99
    snapshot["branch_budgets"]["main"] = 99
    snapshot["events"].clear()
    assert event.to_dict()["checkpoint"]["branch"] == ["root"]
    assert controller.snapshot()["states"]["A"]["strategies"]["rule"]["attempts"] == 1
    assert controller.snapshot()["branch_budgets"] == {"main": 2}
    assert len(controller.events) == 2
    with pytest.raises(FrozenInstanceError):
        event.progress = False


def test_normalized_json_candidate_duplicates_are_state_scoped_not_revision_scoped():
    controller = StrategyController(STRATEGIES)
    first = '{"from":"x+x", "to":"2*x"}'
    reordered = ' { "to": "2*x", "from": "x+x" } '
    controller.select("A", STRATEGIES)
    controller.record("A", "whole_model", first, accepted=False, progress=False, revision=1)
    assert controller.is_duplicate("A", reordered)
    assert not controller.is_duplicate("B", reordered)
    assert controller.select("A", STRATEGIES) == "local_model"
    event = controller.record("A", "local_model", reordered, accepted=False, progress=False, revision=100)
    assert event.duplicate and event.failure_kind is FailureKind.NO_PROGRESS
    assert "repeated_candidate" in event.reasons
    assert controller.snapshot()["states"]["A"]["seen_candidates"] == [candidate_fingerprint(first)]


def test_candidate_fingerprint_does_not_invent_algebraic_equivalence_or_strip_nested_ids():
    assert candidate_fingerprint(" x + x ") == candidate_fingerprint("x + x")
    assert candidate_fingerprint("x + x") != candidate_fingerprint("2*x")
    assert candidate_fingerprint('{"id":"A","value":1}') != candidate_fingerprint('{"id":"B","value":1}')
    assert candidate_fingerprint('{"x":1,"x":2}') != candidate_fingerprint('{"x":2}')
    assert candidate_fingerprint("NaN") != candidate_fingerprint("null")


def test_duplicate_cannot_receive_progress_credit_and_bad_record_can_be_abandoned():
    controller = StrategyController(STRATEGIES)
    controller.select("A", STRATEGIES)
    close(controller, "whole_model", "same", state="A")
    controller.select("A", STRATEGIES)
    before = controller.snapshot()
    with pytest.raises(ValueError, match="duplicate"):
        controller.record("A", "local_model", "same", accepted=True, progress=True)
    assert controller.snapshot() == before
    event = controller.abandon(reasons=("adapter_exception",), failure_kind=FailureKind.UNKNOWN_ERROR)
    assert event.failure_kind is FailureKind.UNKNOWN_ERROR
    assert controller.snapshot()["pending"] is None
    assert controller.snapshot()["attempts"] == 2


@pytest.mark.parametrize("accepted,key,reason", [
    (False, None, "empty_proposal"),
    (True, "valid_but_unhelpful", "no_progress"),
])
def test_empty_or_accepted_without_progress_rotates_and_spends_failure_budget(accepted, key, reason):
    controller = StrategyController(STRATEGIES)
    controller.select("A", STRATEGIES)
    event = controller.record("A", "whole_model", key, accepted=accepted, progress=False)
    assert event.failure_kind is FailureKind.NO_PROGRESS
    assert reason in event.reasons
    assert controller.select("A", STRATEGIES) == "local_model"
    assert controller.snapshot()["progress"] == 0


def test_failure_mapping_is_adapter_owned_and_does_not_guess_math_meanings():
    mapping = {"wrong_identity": FailureKind.IDENTITY_MATH, "wrong_binding": FailureKind.SCHEMA_BINDING}
    controller = StrategyController(STRATEGIES, reason_kinds=mapping)
    mapping["wrong_identity"] = FailureKind.RESOURCE
    for name, reason, kind in (
        ("whole_model", "wrong_identity", FailureKind.IDENTITY_MATH),
        ("local_model", "wrong_binding", FailureKind.SCHEMA_BINDING),
        ("verified_rule", "unrecognized_reason", FailureKind.UNKNOWN_ERROR),
    ):
        controller.select("A", STRATEGIES)
        event = controller.record("A", name, reason, accepted=False, progress=False, reasons=(reason,))
        assert event.failure_kind is kind


def test_unavailable_rules_are_auditable_and_do_not_consume_proposal_attempt():
    controller = StrategyController(STRATEGIES)
    assert controller.select("A", ()) is None
    assert controller.events[-1].failure_kind is FailureKind.UNAVAILABLE_RULE
    assert controller.snapshot()["attempts"] == 0
    assert controller.select("A", ("primitive",)) == "primitive"


def test_explicit_unavailable_rule_failure_rotates_after_reserved_attempt():
    controller = StrategyController(STRATEGIES)
    controller.select("A", ("verified_rule", "primitive"))
    event = controller.record("A", "verified_rule", None, accepted=False, progress=False,
                              failure_kind=FailureKind.UNAVAILABLE_RULE, reasons=("rule_unavailable",))
    assert event.failure_kind is FailureKind.UNAVAILABLE_RULE
    assert controller.select("A", ("verified_rule", "primitive")) == "primitive"


def test_accepted_progress_can_use_rank_but_rank_never_replaces_acceptance():
    controller = StrategyController(("rule",), max_attempts_per_strategy=5)
    controller.select("A", ("rule",))
    with pytest.raises(ValueError, match="accepted"):
        controller.record("A", "rule", "wrong", accepted=False, progress=True, rank=1)
    controller.record("A", "rule", "proof1", accepted=True, progress=True, rank=4)
    controller.select("A", ("rule",))
    with pytest.raises(ValueError, match="strictly decrease"):
        controller.record("A", "rule", "proof2", accepted=True, progress=True, rank=4)
    controller.record("A", "rule", "proof2", accepted=True, progress=True, rank=2)
    assert controller.snapshot()["states"]["A"]["best_rank"] == 2


@pytest.mark.parametrize("kwargs", [
    {"max_attempts": 0}, {"max_attempts": True}, {"max_attempts": 1.5},
    {"max_attempts_per_strategy": 0}, {"max_attempts_per_strategy": False},
    {"max_failures_per_strategy": -1}, {"max_failures_per_strategy": 1.5},
    {"branch_budgets": []}, {"branch_budgets": {"main": 0}},
    {"branch_budgets": {"main": True}}, {"branch_budgets": {"": 1}},
    {"reason_kinds": []}, {"reason_kinds": {"x": "identity_math"}},
])
def test_constructor_rejects_invalid_limits_and_mappings(kwargs):
    with pytest.raises(ValueError):
        StrategyController(STRATEGIES, **kwargs)


@pytest.mark.parametrize("strategies", [[], (), ("a", "a"), ("",), (1,), "rule"])
def test_constructor_rejects_invalid_strategy_registry(strategies):
    with pytest.raises(ValueError):
        StrategyController(strategies)


@pytest.mark.parametrize("kwargs", [
    {"state_key": ""}, {"available": ["whole_model"]},
    {"available": ("whole_model", "whole_model")}, {"available": ("unknown",)},
    {"branch": ""}, {"checkpoint": []}, {"checkpoint": {"value": float("nan")}},
    {"checkpoint": {1: "invalid key"}}, {"checkpoint": {"value": object()}},
])
def test_selection_validation_cannot_spend_budget(kwargs):
    controller = StrategyController(STRATEGIES)
    options = dict(state_key="A", available=STRATEGIES)
    options.update(kwargs)
    with pytest.raises(ValueError):
        controller.select(**options)
    assert controller.snapshot()["attempts"] == 0
    assert controller.events == ()


def test_named_branch_budget_cannot_be_bypassed_by_new_branch_name():
    controller = StrategyController(STRATEGIES, branch_budgets={"registered": 1})
    with pytest.raises(ValueError, match="registered"):
        controller.select("A", STRATEGIES, branch="invented")
    assert controller.snapshot()["attempts"] == 0


@pytest.mark.parametrize("kwargs", [
    {"state_key": "B"}, {"strategy": "local_model"}, {"candidate_key": ""},
    {"candidate_key": {}}, {"accepted": 1}, {"progress": 0},
    {"reasons": []}, {"reasons": ("x", "x")}, {"failure_kind": "resource"},
    {"rank": True}, {"rank": -1}, {"revision": False}, {"revision": -1},
    {"candidate_key": None, "accepted": True},
    {"accepted": True, "progress": True, "failure_kind": FailureKind.RESOURCE},
])
def test_invalid_record_preserves_pending_attempt_and_can_be_abandoned(kwargs):
    controller = StrategyController(STRATEGIES)
    controller.select("A", STRATEGIES)
    before = controller.snapshot()
    options = dict(state_key="A", strategy="whole_model", candidate_key="x", accepted=False, progress=False)
    options.update(kwargs)
    with pytest.raises(ValueError):
        controller.record(**options)
    assert controller.snapshot() == before
    controller.abandon()
    assert controller.snapshot()["attempts"] == 1
    assert controller.snapshot()["pending"] is None


def test_record_and_abandon_require_a_reserved_attempt():
    controller = StrategyController(STRATEGIES)
    with pytest.raises(ValueError, match="reserved"):
        controller.record("A", "whole_model", "x", accepted=False, progress=False)
    with pytest.raises(ValueError, match="reserved"):
        controller.abandon()


def test_stopped_events_are_bounded_without_losing_attempt_outcomes():
    controller = StrategyController(("rule",), max_attempts=2)
    for index in range(100):
        assert controller.select(f"unavailable:{index}", ()) is None
    assert len(controller.events) == 3
    assert controller.snapshot()["suppressed_stop_events"] == 97
    for index in range(2):
        assert controller.select(f"feasible:{index}", ("rule",)) == "rule"
        controller.record(f"feasible:{index}", "rule", f"proof:{index}", accepted=True, progress=True)
    for index in range(100):
        assert controller.select(f"budget:{index}", ("rule",)) is None
    snapshot = controller.snapshot()
    assert len(controller.events) == snapshot["max_audit_events"] == 7
    assert snapshot["suppressed_stop_events"] == 197
    assert snapshot["last_stop"]["reasons"] == ["global_attempt_budget_exhausted"]
    assert snapshot["last_stop"]["state_key"] == "budget:99"
    assert len([event for event in controller.events if event.event == "recorded"]) == 2


def test_identical_stops_are_coalesced_before_the_stop_event_cap():
    controller = StrategyController(STRATEGIES)
    for _ in range(20):
        assert controller.select("A", ()) is None
    assert len(controller.events) == 1
    assert controller.snapshot()["suppressed_stop_events"] == 19


@pytest.mark.parametrize("metadata", [
    {"large": "x" * 16385}, {"many": [0] * 513},
])
def test_checkpoint_metadata_size_limits(metadata):
    controller = StrategyController(STRATEGIES)
    with pytest.raises(ValueError, match="16384"):
        controller.select("A", STRATEGIES, checkpoint=metadata)
    assert controller.snapshot()["attempts"] == 0


def test_checkpoint_metadata_depth_and_cycles_are_rejected():
    controller = StrategyController(STRATEGIES)
    nested = current = {}
    for _ in range(17):
        current["next"] = {}
        current = current["next"]
    with pytest.raises(ValueError, match="16 levels"):
        controller.select("A", STRATEGIES, checkpoint=nested)
    cycle = {}
    cycle["self"] = cycle
    with pytest.raises(ValueError, match="16 levels"):
        controller.select("A", STRATEGIES, checkpoint=cycle)
