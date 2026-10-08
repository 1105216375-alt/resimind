"""Semantic checks for exact quantities, scope, assumptions, and staged reasoning."""
from dataclasses import replace
import json

import pytest

from resimind.agent import Task
from resimind.core import Candidate, Decision, State
from resimind.domains.engineering import (
    DOMAIN, LIMIT_ACTION, LIMIT_TARGET, STRESS_ACTION, STRESS_INPUTS, STRESS_TARGET,
    AxialBarDomain, AxialBarProblem, AxialBarProposer, AxialBarVerifier, build_agent, make_evidence, run_demo,
)


def problem(**changes):
    return replace(AxialBarProblem(axial_static=True, is_uniform=True, no_local_effects=True),
                   **changes)


def run(value):
    return build_agent(value).run(Task("engineering-test", "Check the synthetic bar", DOMAIN)).run_result


def stress_candidate(evidence, claim="nominal_stress=100 MPa"):
    return Candidate("stress-proposal", STRESS_ACTION, STRESS_TARGET, claim,
                     tuple(item.id for item in evidence if item.metric in STRESS_INPUTS))


def test_complete_agent_rejects_then_resolves_two_distinct_obligations():
    result = run_demo()
    assert result.run_result.status == "solved"
    trace = result.run_result.trace
    assert [event.decision.value for event in trace] == ["reject", "accept", "accept"]
    assert [event.residual_after.measure for event in trace] == [2, 1, 0]
    assert trace[0].reasons == ("stress_claim_mismatch",)
    assert trace[0].after.facts == ()
    assert trace[1].after.facts[0].metric == "nominal_stress"
    assert trace[2].candidate.refs == ("fact:nominal_stress", "input:allowable_stress")
    assert trace[2].after.facts[-1].value is True
    assert result.tool_events[0].status == "collected"
    assert json.loads(result.to_json())["run_result"]["state"]["revision"] == 2


def test_offline_correction_requires_verifier_feedback():
    declared = problem()
    proposer = AxialBarProposer(make_evidence(declared))
    residual = AxialBarDomain(declared).rebuild(State())
    first = proposer.propose(State(), residual)[0]
    assert proposer.propose(State(), residual)[0].claim == first.claim
    proposer.observe(run(declared).trace[0])
    assert proposer.propose(State(), residual)[0].claim == "nominal_stress=100000000 Pa"


@pytest.mark.parametrize("changes", [
    {"force_value": 100000, "force_unit": "N", "area_value": "0.001", "area_unit": "m2"},
    {"allowable_stress_value": "0.15", "allowable_stress_unit": "GPa"},
    {"force_value": "200/2", "area_value": "2000/2"},
])
def test_cross_unit_and_rational_inputs_produce_identical_results(changes):
    result = run(problem(**changes))
    assert result.status == "solved"
    assert [(f.metric, f.value, f.unit) for f in result.state.facts] == [
        ("nominal_stress", "100000000", "Pa"), ("within_allowable_stress", True, "")]


def test_exceeded_user_limit_is_a_completed_false_comparison():
    result = run(problem(allowable_stress_value=80))
    assert result.status == "solved"
    assert result.residual.solved
    assert result.state.facts[-1].value is False


def test_exact_equality_is_within_the_supplied_limit():
    assert run(problem(allowable_stress_value=100)).state.facts[-1].value is True


def test_nonterminating_stress_remains_exact_in_the_committed_fact():
    result = run(problem(force_value=1, force_unit="N", area_value=3, area_unit="m2",
                         allowable_stress_value="1/3", allowable_stress_unit="Pa"))
    assert result.status == "solved"
    assert result.state.facts[0].value == "1/3"
    assert result.state.facts[-1].value is True


@pytest.mark.parametrize("field", ["axial_static", "is_uniform", "no_local_effects", "model"])
def test_missing_model_assumptions_defer_and_leave_no_stress_fact(field):
    result = run(problem(**{field: None}))
    assert result.status == "stalled"
    assert result.state.facts == ()
    assert f"missing:{field}" in result.residual.unknowns
    assert all(event.decision is Decision.DEFER for event in result.trace)


@pytest.mark.parametrize("field", ["axial_static", "is_uniform", "no_local_effects"])
def test_false_assumptions_reject_instead_of_using_an_inapplicable_model(field):
    result = run(problem(**{field: False}))
    assert result.status == "stalled"
    assert result.state.facts == ()
    assert f"unsupported_assumption:{field}" in result.residual.hard_constraints


@pytest.mark.parametrize("changes,reason", [
    ({"area_value": 0}, "nonpositive:area"),
    ({"area_value": -10}, "nonpositive:area"),
    ({"force_value": -10}, "nonpositive:force"),
    ({"force_value": 0}, "nonpositive:force"),
    ({"force_value": "NaN"}, "invalid_quantity:force"),
    ({"force_value": True}, "invalid_quantity:force"),
    ({"force_value": 100.0}, "invalid_quantity:force"),
    ({"force_unit": "MPa"}, "wrong_dimension:force"),
    ({"area_unit": "mm"}, "wrong_dimension:area"),
    ({"area_unit": "unknown"}, "invalid_quantity:area"),
    ({"model": "beam_bending"}, "unsupported_model"),
])
def test_bad_inputs_are_constraints_not_arithmetic_facts(changes, reason):
    result = run(problem(**changes))
    assert not result.residual.solved
    assert result.state.facts == ()
    assert reason in result.residual.hard_constraints
    assert result.trace[0].decision is Decision.REJECT
    assert result.trace[0].reasons == (reason,)


@pytest.mark.parametrize("changes,reason", [
    ({"allowable_stress_value": 0}, "nonpositive:allowable_stress"),
    ({"allowable_stress_unit": "N"}, "wrong_dimension:allowable_stress"),
])
def test_bad_limit_leaves_only_the_verified_stress(changes, reason):
    result = run(problem(**changes))
    assert not result.residual.solved
    assert [f.metric for f in result.state.facts] == ["nominal_stress"]
    assert reason in result.residual.hard_constraints


def test_missing_limit_preserves_the_intermediate_result_and_defers_comparison():
    result = run(problem(allowable_stress_value=None))
    assert [f.metric for f in result.state.facts] == ["nominal_stress"]
    assert result.residual.goals == (LIMIT_TARGET,)
    assert result.residual.unknowns == ("missing:allowable_stress",)
    assert result.trace[-1].decision is Decision.DEFER


@pytest.mark.parametrize("replacement", [{"subject": "another-bar"}, {"scope": "another-case"}])
def test_evidence_cannot_cross_members_or_load_cases(replacement):
    declared = problem()
    evidence = tuple(replace(item, **replacement) if item.metric == "force" else item
                     for item in make_evidence(declared))
    verdict = AxialBarVerifier(declared).verify(stress_candidate(evidence), State(),
                                               AxialBarDomain(declared).rebuild(State()), evidence)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("wrong_subject_or_scope:force",)


def test_conflicting_input_records_cannot_be_cherry_picked():
    declared = problem()
    evidence = make_evidence(declared)
    candidate = stress_candidate(evidence)
    evidence += (replace(evidence[0], id="other-force", value=200),)
    verdict = AxialBarVerifier(declared).verify(candidate, State(),
                                               AxialBarDomain(declared).rebuild(State()), evidence)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("ambiguous_input:force",)


def test_correct_stress_claim_with_force_units_is_rejected():
    declared = problem()
    evidence = make_evidence(declared)
    verdict = AxialBarVerifier(declared).verify(stress_candidate(evidence, "nominal_stress=100000000 N"),
                                               State(), AxialBarDomain(declared).rebuild(State()), evidence)
    assert verdict.reasons == ("wrong_claim_dimension",)


def test_silent_replacement_of_declared_input_is_rejected():
    declared = problem()
    evidence = tuple(replace(item, value=200) if item.metric == "force" else item
                     for item in make_evidence(declared))
    verdict = AxialBarVerifier(declared).verify(stress_candidate(evidence, "nominal_stress=200 MPa"),
                                               State(), AxialBarDomain(declared).rebuild(State()), evidence)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("input_differs_from_declared_problem",)


def test_stress_cannot_omit_assumption_provenance():
    declared = problem()
    evidence = make_evidence(declared)
    candidate = replace(stress_candidate(evidence), refs=("input:force", "input:area"))
    verdict = AxialBarVerifier(declared).verify(candidate, State(),
                                               AxialBarDomain(declared).rebuild(State()), evidence)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("stress_references_mismatch",)


def test_comparison_cannot_skip_the_verified_stress_step():
    declared = problem()
    evidence = make_evidence(declared)
    candidate = Candidate("early-comparison", LIMIT_ACTION, LIMIT_TARGET,
                          "within_allowable_stress=true", tuple(item.id for item in evidence))
    verdict = AxialBarVerifier(declared).verify(candidate, State(),
                                               AxialBarDomain(declared).rebuild(State()), evidence)
    assert verdict.decision is Decision.DEFER
    assert verdict.reasons == ("verified_stress_required",)


def test_comparison_must_reference_the_intermediate_fact():
    declared = problem()
    complete = run(declared)
    state = State(1, complete.state.facts[:1])
    candidate = Candidate("uncited-comparison", LIMIT_ACTION, LIMIT_TARGET,
                          "within_allowable_stress=true", ("input:allowable_stress",))
    verdict = AxialBarVerifier(declared).verify(candidate, state,
                                               AxialBarDomain(declared).rebuild(state), complete.evidence)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("comparison_requires_stress_fact_reference",)


def test_false_pass_claim_is_rejected_after_a_valid_stress_calculation():
    declared = problem(allowable_stress_value=80)
    complete = run(declared)
    state = State(1, complete.state.facts[:1])
    candidate = Candidate("false-pass", LIMIT_ACTION, LIMIT_TARGET,
                          "within_allowable_stress=true", ("fact:nominal_stress", "input:allowable_stress"))
    verdict = AxialBarVerifier(declared).verify(candidate, state,
                                               AxialBarDomain(declared).rebuild(state), complete.evidence)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("limit_claim_mismatch",)
    assert verdict.facts == ()


def test_domain_reconstructs_goals_and_rejects_forged_output_values():
    declared = problem()
    complete = run(declared)
    fake_stress = replace(complete.state.facts[0], value="1")
    state = State(2, (fake_stress, complete.state.facts[1]))
    residual = AxialBarDomain(declared).rebuild(state)
    assert residual.goals == (STRESS_TARGET, LIMIT_TARGET)
    assert residual.hard_constraints == ("invalid_committed_fact:fact:nominal_stress",
                                         "invalid_committed_fact:fact:within_allowable_stress")


def test_domain_does_not_conflate_boolean_comparison_with_numeric_one():
    declared = problem()
    complete = run(declared)
    state = State(2, (complete.state.facts[0], replace(complete.state.facts[1], value=1)))
    residual = AxialBarDomain(declared).rebuild(state)
    assert residual.goals == (LIMIT_TARGET,)
    assert residual.hard_constraints == ("invalid_committed_fact:fact:within_allowable_stress",)


def test_provider_callback_uses_the_same_verified_agent_path():
    prompts = []

    def complete(prompt):
        payload = json.loads(prompt)
        prompts.append(payload)
        facts = payload["state"]["facts"]
        if not facts:
            return json.dumps({"id": "model-stress", "action": STRESS_ACTION,
                               "target": STRESS_TARGET, "claim": "nominal_stress=0.1 GPa",
                               "refs": [f"input:{metric}" for metric in STRESS_INPUTS]})
        return json.dumps({"id": "model-limit", "action": LIMIT_ACTION, "target": LIMIT_TARGET,
                           "claim": "within_allowable_stress=true",
                           "refs": ["fact:nominal_stress", "input:allowable_stress"]})

    result = build_agent(problem(), complete).run(Task("provider", "Check bar", DOMAIN)).run_result
    assert result.status == "solved"
    assert len(prompts) == 2
    assert prompts[1]["last_feedback"]["decision"] == "accept"


def test_tool_rejects_wrong_task_scope_before_reasoning():
    result = build_agent(problem()).run(Task("wrong-task", "Check bar", DOMAIN,
                                             (("scope", "other-case"),)))
    assert result.run_result.stop_reason == "tool_error"
    assert result.run_result.state.facts == ()
