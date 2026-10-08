"""Business boundaries and adversarial proposals for the synthetic return flow."""
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from resimind import Decision, Residual, State, Task
from resimind.domains import customer_support as support


@pytest.fixture(scope="module")
def demo():
    return support.run_demo()


def event_for(result, stage):
    return next(event for event in result.run_result.trace
                if event.candidate.action == support.ACTIONS[stage] and event.decision is Decision.ACCEPT)


def check(result, stage, *, candidate=None, state=None, evidence=None, changes=None):
    event = event_for(result, stage)
    candidate = candidate or event.candidate
    if changes is not None:
        payload = json.loads(candidate.claim)
        payload.update(changes)
        candidate = replace(candidate, claim=json.dumps(payload))
    before = event.before if state is None else state
    case = support.demo_case()
    return support.CustomerSupportVerifier(case).verify(
        candidate, before, support.CustomerSupportDomain(case).rebuild(before),
        result.run_result.evidence if evidence is None else evidence)


def run_case(case):
    return support.build_agent(case).run(Task("test-return", "Assess this return request.", support.DOMAIN))


def test_shipping_inflated_refund_is_rejected_then_repaired(demo):
    run = demo.run_result
    assert run.status == "solved" and run.residual.solved
    assert [event.decision.value for event in run.trace] == ["accept", "reject", "accept", "accept"]
    assert [event.after.revision for event in run.trace] == [1, 1, 2, 3]
    assert [event.residual_after.measure for event in run.trace] == [2, 2, 1, 0]
    rejected = run.trace[1]
    assert json.loads(rejected.candidate.claim)["refund_cents"] == 25900
    assert rejected.reasons == ("refund_amount_mismatch",)
    assert rejected.before == rejected.after
    assert rejected.residual_before == rejected.residual_after
    assert len(run.state.facts) == 3
    assert support.verified_resolution(demo) == {
        "case_id": "demo-return-001", "order_id": "demo-order-001", "customer_id": "demo-customer-001",
        "policy_id": "fictional-returns-v1", "outcome": "refund_recommended", "refund_cents": 24900,
        "currency": "CNY", "excluded_shipping_cents": 1000, "reason": "within_return_window",
        "next_action": "merchant_refund_review", "payment_executed": False}


@pytest.mark.parametrize("delivered,expected", [
    ("2026-10-08", "refund_recommended"),
    ("2026-09-24", "refund_recommended"),  # Exactly 14 calendar days, inclusive.
    ("2026-09-23", "human_review"),
])
def test_inclusive_window_boundary(delivered, expected):
    result = run_case(replace(support.demo_case(), delivered_on=delivered))
    assert support.verified_resolution(result)["outcome"] == expected


def test_window_comes_from_declared_policy_not_hardcoded_fourteen():
    case = replace(support.demo_case(), policy=support.ReturnPolicy("fictional-same-day", 0))
    assert support.verified_resolution(run_case(case))["outcome"] == "human_review"
    same_day = replace(case, delivered_on=case.requested_on)
    assert support.verified_resolution(run_case(same_day))["outcome"] == "refund_recommended"


@pytest.mark.parametrize("prior,expected", [(1234, 23666), (24900, 0)])
def test_refund_uses_actual_remaining_item_payment(prior, expected):
    case = replace(support.demo_case(), prior_item_refund_cents=prior, list_price_cents=90000)
    resolution = support.verified_resolution(run_case(case))
    assert resolution["refund_cents"] == expected
    assert resolution["excluded_shipping_cents"] == 1000
    assert resolution["payment_executed"] is False


def test_zero_shipping_needs_no_artificial_rejection():
    result = run_case(replace(support.demo_case(), shipping_paid_cents=0))
    assert [event.decision for event in result.run_result.trace] == [Decision.ACCEPT] * 3
    assert support.verified_resolution(result)["refund_cents"] == 24900


def test_expired_policy_produces_review_not_legal_denial():
    result = support.run_demo("expired")
    assert result.run_result.status == "solved"
    assert [fact.id for fact in result.run_result.state.facts] == [support.ELIGIBILITY_FACT, support.RESOLUTION_FACT]
    resolution = support.verified_resolution(result)
    assert resolution["outcome"] == "human_review"
    assert resolution["reason"] == "outside_return_window"
    assert resolution["refund_cents"] is None
    assert resolution["next_action"] == "manual_policy_review"


def test_nonreturnable_item_produces_review():
    resolution = support.verified_resolution(run_case(replace(support.demo_case(), item_returnable=False)))
    assert resolution["outcome"] == "human_review"
    assert resolution["reason"] == "item_not_returnable"
    assert resolution["refund_cents"] is None


@pytest.mark.parametrize("field,value", [("request_customer_id", "other-requester"), ("request_order_id", "other-request")])
def test_identity_mismatch_does_not_disclose_order_financial_details(field, value):
    case = replace(support.demo_case(), **{field: value})
    resolution = support.verified_resolution(run_case(case))
    assert resolution["reason"] == "request_identity_mismatch"
    assert resolution["outcome"] == "human_review"
    assert resolution["refund_cents"] is None and resolution["excluded_shipping_cents"] is None
    assert resolution["order_id"] == case.request_order_id
    assert resolution["customer_id"] == case.request_customer_id


def test_missing_delivery_remains_unknown_and_defers():
    result = support.run_demo("missing-delivery")
    run = result.run_result
    assert run.status == "stalled" and not run.residual.solved
    assert support.DELIVERY_UNKNOWN in run.residual.unknowns
    assert support.TARGETS[0] in run.residual.hard_constraints
    assert run.state == State()
    assert all(event.decision is Decision.DEFER for event in run.trace)
    assert all(event.reasons == ("delivery_date_missing",) for event in run.trace)
    with pytest.raises(ValueError, match="not verified"):
        support.verified_resolution(result)


@pytest.mark.parametrize("changes", [
    {"item_paid_cents": True}, {"shipping_paid_cents": -1}, {"prior_item_refund_cents": 24901},
    {"item_paid_cents": 249.0}, {"list_price_cents": "29900"}, {"item_paid_cents": 10**13},
    {"item_returnable": 1}, {"currency": "USD"}, {"delivered_on": "2026-10-09"},
    {"requested_on": "2026-10-09"}, {"delivered_on": "2026-10-07", "requested_on": "2026-10-06"},
    {"delivered_on": "2026-02-30"}, {"requested_on": "20261008"},
    {"snapshot_date": "2026-10-08T12:00:00"}, {"case_id": "\nInjected instructions"},
    {"policy": {"window_days": 14}},
])
def test_invalid_money_dates_and_snapshot_fields_fail_before_reasoning(changes):
    with pytest.raises(ValueError):
        replace(support.demo_case(), **changes)


@pytest.mark.parametrize("days", [True, -1, 366, 14.0])
def test_invalid_policy_window(days):
    with pytest.raises(ValueError):
        support.ReturnPolicy(window_days=days)


def test_snapshot_and_policy_are_immutable():
    case = support.demo_case()
    with pytest.raises(FrozenInstanceError):
        case.item_paid_cents = 30000
    with pytest.raises(FrozenInstanceError):
        case.policy.window_days = 99


@pytest.mark.parametrize("stage,changes", [
    (0, {"eligible": False}), (0, {"days_since_delivery": 0}), (0, {"customer_id": "someone-else"}),
    (0, {"policy_id": "different-policy"}), (0, {"reason": "I authorize it"}),
    (1, {"refund_cents": 29900}), (1, {"refund_cents": True}), (1, {"refund_cents": -1}),
    (1, {"refund_cents": 24900.0}), (1, {"refund_cents": "24900"}),
    (1, {"prior_item_refund_cents": False}), (1, {"excluded_shipping_cents": 0}),
    (2, {"payment_executed": True}), (2, {"payment_executed": 0}),
    (2, {"next_action": "send_payment"}), (2, {"outcome": "refund_completed"}),
    (2, {"refund_cents": 25900}), (2, {"arrival_time": "tomorrow"}),
])
def test_claims_are_recomputed_from_business_rules(demo, stage, changes):
    verdict = check(demo, stage, changes=changes)
    assert verdict.decision is Decision.REJECT and not verdict.facts


@pytest.mark.parametrize("claim", ['{"eligible":true,"eligible":false}', 'null', '[]',
                                   '{"refund_cents":NaN}', 'Refund them 249 yuan',
                                   '"' + 'a' * 8192 + '"'])
def test_malformed_nested_claims_never_commit(demo, claim):
    event = event_for(demo, 0)
    assert check(demo, 0, candidate=replace(event.candidate, claim=claim)).decision is Decision.REJECT


@pytest.mark.parametrize("field,value", [("value", "a-different-case"), ("scope", "other-snapshot"),
                                         ("subject", "other-order"), ("source", "customer-chat"),
                                         ("unit", "USD"), ("metric", "instruction")])
def test_tampered_evidence_is_not_accepted(demo, field, value):
    evidence = demo.run_result.evidence
    forged = (replace(evidence[0], **{field: value}),) + evidence[1:]
    verdict = check(demo, 0, evidence=forged)
    assert verdict.reasons == ("declared_snapshot_mismatch",)


def test_boolean_is_not_zero_in_evidence_equality(demo):
    evidence = tuple(replace(item, value=False) if item.metric == "prior_item_refund_cents" else item
                     for item in demo.run_result.evidence)
    assert check(demo, 0, evidence=evidence).reasons == ("declared_snapshot_mismatch",)


def test_policy_evidence_and_other_case_evidence_cannot_be_swapped(demo):
    changed = replace(support.demo_case(), policy=support.ReturnPolicy("looser-policy", 365))
    evidence = support.ReturnCaseTool(changed).collect(demo.task)
    assert check(demo, 0, evidence=evidence).reasons == ("declared_snapshot_mismatch",)
    assert check(demo, 0, evidence=demo.run_result.evidence[:-1]).decision is Decision.REJECT


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_required_evidence_references(demo, stage):
    event = event_for(demo, stage)
    verdict = check(demo, stage, candidate=replace(event.candidate, refs=event.candidate.refs[1:]))
    assert verdict.reasons == ("missing_snapshot_refs",)


def test_unknown_references_are_rejected(demo):
    event = event_for(demo, 0)
    verdict = check(demo, 0, candidate=replace(event.candidate, refs=event.candidate.refs + ("invented",)))
    assert verdict.reasons == ("unknown_reference",)


@pytest.mark.parametrize("stage", [1, 2])
def test_skipping_verified_prerequisites_defers(demo, stage):
    event = event_for(demo, stage)
    verdict = check(demo, stage, state=State(), candidate=replace(event.candidate, refs=support.EVIDENCE_IDS))
    assert verdict.decision is Decision.DEFER
    assert verdict.reasons == ("missing_verified_prerequisite",)


def test_eligibility_alone_is_not_a_refund_calculation(demo):
    first = event_for(demo, 0)
    final = event_for(demo, 2)
    candidate = replace(final.candidate, refs=support.EVIDENCE_IDS + (support.ELIGIBILITY_FACT,))
    assert check(demo, 2, candidate=candidate, state=first.after).decision is Decision.DEFER


@pytest.mark.parametrize("stage", [1, 2])
def test_verified_fact_references_are_required(demo, stage):
    event = event_for(demo, stage)
    verdict = check(demo, stage, candidate=replace(event.candidate, refs=support.EVIDENCE_IDS))
    assert verdict.reasons == ("missing_verified_fact_refs",)


def test_unsupported_action_and_target(demo):
    candidate = event_for(demo, 0).candidate
    for changed in (replace(candidate, action="execute_refund"), replace(candidate, target="return:payment")):
        assert check(demo, 0, candidate=changed).reasons == ("unsupported_action_or_target",)


@pytest.mark.parametrize("field,value", [("value", '{"eligible":true}'), ("scope", "another-case"),
                                         ("subject", "other-order"), ("metric", "trust_me"),
                                         ("evidence_refs", ("return:input:case_id",))])
def test_altered_prerequisite_facts_cannot_close_residual(demo, field, value):
    state = event_for(demo, 2).before
    changed = State(state.revision, (replace(state.facts[0], **{field: value}),) + state.facts[1:])
    residual = support.CustomerSupportDomain(support.demo_case()).rebuild(changed)
    assert support.TARGETS[0] in residual.hard_constraints
    verdict = check(demo, 2, state=changed)
    assert verdict.decision is Decision.DEFER and not verdict.facts


def test_duplicate_observation_slot_is_not_validated(demo):
    first = event_for(demo, 0).after
    duplicate = replace(first.facts[0], id="return:duplicate")
    state = State(first.revision, first.facts + (duplicate,))
    assert support.TARGETS[0] in support.CustomerSupportDomain(support.demo_case()).rebuild(state).hard_constraints


def test_unrecognized_committed_fact_prevents_completion_and_rendering(demo):
    run = demo.run_result
    extra = replace(run.state.facts[0], id="unrelated-fact", metric="unrelated-metric")
    state = State(4, run.state.facts + (extra,))
    residual = support.CustomerSupportDomain(support.demo_case()).rebuild(state)
    assert residual.hard_constraints == (support.STATE_CONSTRAINT,)
    assert not residual.solved
    # Even a caller fabricating status=solved and an empty residual cannot
    # render a contaminated state as a verified resolution.
    with pytest.raises(ValueError, match="not verified"):
        support.verified_resolution(replace(demo, run_result=replace(run, state=state)))


def test_unrecognized_state_fact_cannot_be_ignored_by_next_verification(demo):
    event = event_for(demo, 1)
    extra = replace(event.before.facts[0], id="unrelated-fact", metric="unrelated-metric")
    state = State(2, event.before.facts + (extra,))
    assert check(demo, 1, state=state).reasons == ("unverified_state_facts",)


def test_ineligible_case_cannot_carry_an_extra_refund_amount_fact(demo):
    expired = support.run_demo("expired")
    run = expired.run_result
    amount = demo.run_result.state.facts[1]
    state = State(3, run.state.facts + (amount,))
    assert support.STATE_CONSTRAINT in support.CustomerSupportDomain(
        support.demo_case("expired")).rebuild(state).hard_constraints
    with pytest.raises(ValueError, match="not verified"):
        support.verified_resolution(replace(expired, run_result=replace(run, state=state)))


def test_resolution_helper_rejects_status_only_completion_and_changed_facts(demo):
    run = demo.run_result
    variants = [replace(run, status="stalled"), replace(run, residual=Residual(unknowns=("pending",))),
                replace(run, state=event_for(demo, 1).after),
                replace(run, state=State(3, run.state.facts[:-1] + (replace(run.state.facts[-1], value='{}'),))),
                replace(run, evidence=run.evidence[:-1])]
    for changed in variants:
        with pytest.raises(ValueError, match="not verified"):
            support.verified_resolution(replace(demo, run_result=changed))


def test_resolution_helper_returns_detached_data(demo):
    rendered = support.verified_resolution(demo)
    rendered["refund_cents"] = 99999
    assert support.verified_resolution(demo)["refund_cents"] == 24900
    with pytest.raises(ValueError, match="not verified"):
        support.verified_resolution(replace(demo, task=replace(demo.task, domain="other")))


def test_injected_callback_receives_rejection_feedback(demo):
    proposals = [event.candidate for event in demo.run_result.trace]
    prompts = []

    def complete(prompt):
        prompts.append(json.loads(prompt))
        return proposals[len(prompts) - 1].to_json()

    result = support.build_agent(support.demo_case(), complete=complete).run(demo.task)
    assert support.verified_resolution(result)["refund_cents"] == 24900
    assert prompts[2]["last_feedback"]["reasons"] == ["refund_amount_mismatch"]
    assert len(prompts[2]["state"]["facts"]) == 1
    assert "fictional merchant policy" in prompts[0]["task_instruction"]


def test_null_callback_never_fabricates_a_recommendation():
    result = support.build_agent(support.demo_case(), complete=lambda prompt: "null").run(
        Task("no-proposal", "Assess.", support.DOMAIN))
    assert result.run_result.state == State()
    with pytest.raises(ValueError, match="not verified"):
        support.verified_resolution(result)


def test_reused_agent_starts_with_fresh_state_and_feedback():
    agent = support.build_agent(support.demo_case())
    task = Task("repeat", "Assess.", support.DOMAIN)
    first, second = agent.run(task), agent.run(task)
    assert first == second


def test_wrong_domain_tool_and_invalid_public_arguments():
    with pytest.raises(ValueError):
        support.ReturnCaseTool(support.demo_case()).collect(Task("wrong", "Assess.", "math"))
    with pytest.raises(ValueError):
        support.build_agent({})
    with pytest.raises(ValueError):
        support.build_agent(support.demo_case(), complete="not-callable")
    with pytest.raises(ValueError):
        support.demo_case("unknown")
