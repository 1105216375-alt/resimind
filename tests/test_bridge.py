"""Physical certificates, complete pattern envelopes, and evidence boundaries."""
from dataclasses import replace
from fractions import Fraction as F
import json

import pytest

from resimind.agent import Task
from resimind.core import Candidate, Decision, State
from resimind.domains.bridge import (
    ACTIONS, DOMAIN, ENVELOPE_TARGET, BridgeDomain, BridgeProposer, BridgeVerifier,
    ContinuousBridgeProblem, LoadCombination, build_agent, demo_problem, make_evidence,
    run_demo, verified_case_results, verified_summary,
)


def run(problem, **kwargs):
    return build_agent(problem, **kwargs).run(Task("bridge-test", "Check bridge certificate", DOMAIN))


def verify(problem, candidate, state, evidence=None):
    return BridgeVerifier(problem).verify(candidate, state, BridgeDomain(problem).rebuild(state),
                                          make_evidence(problem) if evidence is None else evidence)


@pytest.fixture
def demo():
    return run_demo()


def test_demo_is_a_staged_agent_with_actual_feedback_and_all_eight_cases(demo):
    result = demo.run_result
    assert result.status == "solved"
    assert result.residual.solved
    assert len(result.trace) == 12
    assert result.trace[1].decision is Decision.REJECT
    assert result.trace[1].reasons == ("pier_rotation_continuity_failed",)
    assert result.trace[1].before == result.trace[1].after
    assert result.trace[1].residual_before == result.trace[1].residual_after
    assert len(verified_case_results(demo)) == 8
    assert len(result.state.facts) == 11
    assert list(verified_case_results(demo)) == list(demo_problem().case_ids)
    assert all(verified_summary(demo)["comparisons"].values())
    json.loads(demo.to_json())


def test_correction_requires_observing_the_actual_rejection(demo):
    problem = demo_problem()
    proposer = BridgeProposer(problem, make_evidence(problem))
    state = demo.run_result.trace[0].after
    residual = BridgeDomain(problem).rebuild(state)
    wrong = proposer.propose(state, residual)[0]
    assert proposer.propose(state, residual)[0].claim == wrong.claim
    proposer.observe(demo.run_result.trace[1])
    correct = proposer.propose(state, residual)[0]
    assert correct.claim != wrong.claim
    assert verify(problem, correct, state).decision is Decision.ACCEPT


def test_classic_equal_span_uniform_load_benchmark():
    p = replace(demo_problem(), spans=(20,20), flexural_rigidities=(30000000000,30000000000),
                dead_loads=(40,40), live_loads=(0,0), combinations=(LoadCombination("equal"),))
    result = run(p)
    assert result.run_result.status == "solved"
    row = verified_case_results(result)["equal:none"]
    q,l,ei = F(40000),F(20),F(30000000000)
    assert row["pier_moment"] == -q*l*l/8
    assert row["reactions"] == (3*q*l/8, 5*q*l/4, 3*q*l/8)
    for coefficients in (row["v1"],row["v2"]):
        # Known continuous two-span midpoint deflection: q L^4 / (192 EI).
        midpoint = -sum(c*(l/2)**i for i,c in enumerate(coefficients,1))
        assert midpoint == q*l**4/(192*ei)
    env = verified_summary(result)["envelope"]
    assert [F(v["value"]) for v in env["positive_max"]] == [9*q*l*l/128]*2
    assert [F(v["x"]) for v in env["positive_max"]] == [3*l/8,5*l/8]


def test_asymmetric_benchmark_from_independent_rotation_compatibility():
    p = replace(demo_problem(), spans=(12,18), flexural_rigidities=(24000000000,36000000000),
                dead_loads=(20,30), live_loads=(0,0), combinations=(LoadCombination("asym"),))
    result = run(p)
    row = verified_case_results(result)["asym:none"]
    # Direct hand benchmark, deliberately neither equal spans nor equal EI.
    assert row["pier_moment"] == -F(787500)
    assert row["reactions"] == (F(54375),F(499375),F(226250))
    assert result.run_result.status == "solved"


def test_curvature_supports_and_rotation_continuity_hold_exactly_for_every_case(demo):
    problem = demo_problem()
    for row in verified_case_results(demo).values():
        l1,l2 = map(F,problem.spans)
        q1,q2 = row["q"]
        ra,rb,rc = row["reactions"]
        assert ra+rb+rc == q1*l1+q2*l2
        assert rb*l1+rc*(l1+l2) == q1*l1*l1/2 + q2*l2*(l1+l2/2)
        for i,(l,ei) in enumerate(zip(problem.spans,problem.flexural_rigidities),1):
            cs = row[f"v{i}"]
            assert sum(c*F(l)**power for power,c in enumerate(cs,1)) == 0
            assert 2*F(ei)*cs[1] == (0 if i == 1 else row["pier_moment"])
            assert 24*F(ei)*cs[3] == -row["q"][i-1]
        assert sum(i*c*l1**(i-1) for i,c in enumerate(row["v1"],1)) == row["v2"][0]


def test_partial_live_loading_controls_sagging_and_midspan_envelopes(demo):
    env = verified_summary(demo)["envelope"]
    assert env["pier_min"]["case"] == "amplified:both"
    assert [row["case"] for row in env["positive_max"]] == ["amplified:left","amplified:right"]
    assert [row["case"] for row in env["midspan_abs_max"]] == ["amplified:left","amplified:right"]
    assert F(env["positive_max"][0]["x"]) != 12
    assert F(env["positive_max"][1]["x"]) != 15


def test_dimensionally_equivalent_inputs_preserve_responses(demo):
    p = replace(demo_problem(), spans=(24000,30000), span_unit="mm",
                flexural_rigidities=(36000000,48000000), rigidity_unit="kNm2",
                dead_loads=(35000,35000), live_loads=(25000,25000), load_unit="Npm",
                moment_limit=9000000, moment_unit="Nm", midspan_deflection_limit="0.04", deflection_unit="m")
    result = run(p)
    assert result.run_result.status == "solved"
    assert verified_case_results(result) == verified_case_results(demo)
    assert verified_summary(result) == verified_summary(demo)


def test_completed_overload_and_deflection_comparisons_can_be_false():
    result = run(replace(demo_problem(), moment_limit=100, midspan_deflection_limit=1))
    assert result.run_result.status == "solved"
    assert verified_summary(result)["comparisons"] == {
        "moment_within_supplied_limit":False, "midspans_within_supplied_limit":False,
        "all_support_reactions_nonnegative":True}


def test_asymmetric_load_reports_uplift_instead_of_clipping_or_calling_it_safe():
    p = replace(demo_problem(), spans=(20,20), flexural_rigidities=(30000000000,30000000000),
                dead_loads=(0,0), live_loads=(0,40), combinations=(LoadCombination("uplift"),))
    result = run(p)
    row = verified_case_results(result)["uplift:right"]
    assert result.run_result.status == "solved"
    assert row["reactions"][0] == -50000
    assert verified_summary(result)["comparisons"]["all_support_reactions_nonnegative"] is False
    assert F(verified_summary(result)["envelope"]["reaction_min"][0]["value"]) == -50000


def test_zero_loads_are_valid_and_tied_envelope_uses_first_case():
    result = run(replace(demo_problem(), dead_loads=(0,0), live_loads=(0,0)))
    assert result.run_result.status == "solved"
    env = verified_summary(result)["envelope"]
    assert env["moment_abs_max"] == {"case":"baseline:none", "value":"0"}
    assert all(F(row["value"]) == 0 for row in env["midspan_abs_max"])


@pytest.mark.parametrize("changes,reason", [
    ({"spans":(0,30)}, "invalid_sign:L1"),
    ({"flexural_rigidities":(-1,48000000000)}, "invalid_sign:EI1"),
    ({"dead_loads":(-1,35)}, "invalid_sign:dead1"),
    ({"live_loads":(25.0,25)}, "invalid_quantity:live1"),
    ({"spans":(True,30)}, "invalid_quantity:L1"),
    ({"span_unit":"N"}, "wrong_dimension:L1"),
    ({"rigidity_unit":"Nm"}, "wrong_dimension:EI1"),
    ({"load_unit":"kN"}, "wrong_dimension:dead1"),
    ({"moment_unit":"MPa"}, "wrong_dimension:moment_limit"),
    ({"deflection_unit":"banana"}, "invalid_quantity:midspan_deflection_limit"),
    ({"linear_elastic":False}, "unsupported_assumption:linear_elastic"),
    ({"continuous_at_pier":False}, "unsupported_assumption:continuous_at_pier"),
    ({"supports":"two_independent_simple_spans"}, "unsupported_assumption:supports"),
    ({"model":"timoshenko"}, "unsupported_assumption:model"),
    ({"combinations":(LoadCombination("bad",-1,1),)}, "invalid_sign:bad:dead_factor"),
])
def test_invalid_models_quantities_and_signs_leave_all_goals_unresolved(changes,reason):
    result = run(replace(demo_problem(),**changes)).run_result
    assert result.status == "stalled"
    assert not result.state.facts
    assert reason in result.residual.hard_constraints
    assert result.trace[0].decision is Decision.REJECT


@pytest.mark.parametrize("changes,reason", [
    ({"linear_elastic":None}, "missing:linear_elastic"),
    ({"no_support_settlement":None}, "missing:no_support_settlement"),
    ({"flexural_rigidities":(None,48000000000)}, "missing:EI1"),
    ({"moment_limit":None}, "missing:moment_limit"),
])
def test_missing_inputs_defer_without_silently_selecting_assumptions(changes,reason):
    result = run(replace(demo_problem(),**changes)).run_result
    assert result.status == "stalled"
    assert reason in result.residual.unknowns
    assert not result.state.facts
    assert result.trace[0].decision is Decision.DEFER


@pytest.mark.parametrize("replacement", [{"subject":"another-bridge"},{"scope":"another-survey"},{"value":99},{"source":"unregistered"}])
def test_evidence_is_bound_to_subject_scope_value_and_source(demo,replacement):
    p = demo_problem()
    evidence = tuple(replace(item,**replacement) if item.metric == "dead1" else item for item in make_evidence(p))
    candidate = demo.run_result.trace[0].candidate
    verdict = verify(p,candidate,State(),evidence)
    assert verdict.decision is Decision.REJECT
    assert not verdict.facts


def test_conflicting_evidence_cannot_be_cherry_picked(demo):
    p = demo_problem()
    evidence = make_evidence(p)
    evidence += (replace(next(x for x in evidence if x.metric == "dead1"),id="alternative-dead",value=1),)
    assert verify(p,demo.run_result.trace[0].candidate,State(),evidence).reasons == ("ambiguous_input:dead1",)


def test_wrong_moment_sign_reaction_curvature_support_and_case_are_rejected(demo):
    event = demo.run_result.trace[2]
    original = json.loads(event.candidate.claim)
    changes = [
        {"pier_moment":str(-F(original["pier_moment"]))},
        {"reactions":["0",*original["reactions"][1:]]},
        {"v1":[original["v1"][0],"1",*original["v1"][2:]]},
        {"v1":["1",*original["v1"][1:]]},
        {"case":"baseline:both"},
        {"q":["1",original["q"][1]]},
    ]
    for change in changes:
        candidate = replace(event.candidate,claim=json.dumps({**original,**change}))
        assert verify(demo_problem(),candidate,event.before).decision is Decision.REJECT


@pytest.mark.parametrize("claim", [
    "not-json", "[]", "null", '{"case":"baseline:none","case":"baseline:none"}',
    '{"verified":true}',
])
def test_malformed_certificates_never_create_facts(demo,claim):
    event = demo.run_result.trace[2]
    verdict = verify(demo_problem(),replace(event.candidate,claim=claim),event.before)
    assert verdict.decision is Decision.REJECT
    assert verdict.facts == ()


def test_missing_one_live_pattern_blocks_envelope_and_residual_completion(demo):
    event = demo.run_result.trace[-2]
    reduced = State(event.before.revision, tuple(fact for fact in event.before.facts if fact.id != "bridge:solution:amplified:left"))
    verdict = verify(demo_problem(),event.candidate,reduced)
    assert verdict.decision is Decision.DEFER
    assert verdict.reasons == ("all_load_combinations_and_patterns_required",)
    residual = BridgeDomain(demo_problem()).rebuild(reduced)
    assert "bridge:case:amplified:left" in residual.goals
    assert ENVELOPE_TARGET in residual.goals


def test_envelope_cannot_omit_pattern_or_replace_stationary_point_with_midpoint(demo):
    event = demo.run_result.trace[-2]
    data = json.loads(event.candidate.claim)
    for change in ({"cases":data["cases"][:-1]}, {"positive_max":[{**data["positive_max"][0],"x":"12"},data["positive_max"][1]]}):
        verdict = verify(demo_problem(),replace(event.candidate,claim=json.dumps({**data,**change})),event.before)
        assert verdict.decision is Decision.REJECT
        assert verdict.reasons == ("envelope_claim_mismatch",)


def test_copied_certificate_from_another_scope_cannot_clear_residual(demo):
    state = demo.run_result.state
    facts = tuple(replace(fact,scope="foreign") if fact.metric == "case_solution" else fact for fact in state.facts)
    residual = BridgeDomain(demo_problem()).rebuild(State(state.revision,facts))
    assert not residual.solved
    assert all(f"bridge:case:{case}" in residual.goals for case in demo_problem().case_ids)
    assert residual.hard_constraints


def test_evidence_reference_cannot_be_replaced_with_unrelated_fact(demo):
    event = demo.run_result.trace[2]
    verdict = verify(demo_problem(),replace(event.candidate,refs=()),event.before)
    assert verdict.reasons == ("case_requires_model_reference",)


def test_real_model_interface_receives_rejection_and_still_verifies_each_certificate(demo):
    # Scripted provider interface test, not an actual neural-model benchmark.
    queue = iter(event.candidate for event in demo.run_result.trace)
    feedback = []
    def complete(prompt):
        body = json.loads(prompt)
        feedback.append(body["last_feedback"])
        return next(queue).to_json()
    result = run(demo_problem(),complete=complete)
    assert result.run_result.status == "solved"
    assert feedback[2]["decision"] == "reject"
    assert feedback[2]["reasons"] == ["pier_rotation_continuity_failed"]
    assert verified_case_results(result) == verified_case_results(demo)


def test_explicit_problem_rejects_nonunique_combinations_and_bad_shapes():
    with pytest.raises(ValueError):
        replace(demo_problem(),combinations=(LoadCombination("dup"),LoadCombination("dup")))
    with pytest.raises(ValueError):
        replace(demo_problem(),spans=(1,2,3))
    with pytest.raises(ValueError):
        LoadCombination("not:a:case")


def test_task_scope_mismatch_fails_collection():
    result = build_agent(demo_problem()).run(Task("wrong", "test", DOMAIN, (("scope","foreign"),)))
    assert result.run_result.status == "error"
    assert result.run_result.stop_reason == "tool_error"


def _released_support_virtual_work(l1,l2,ei1,ei2,w1,w2):
    """Independent force-method oracle: release B, then enforce its displacement.

    Integrate M0*m/EI and m*m/EI exactly as polynomials. This uses neither the
    three-moment equation nor the adapter's solver/certificate helpers.
    """
    l1,l2,ei1,ei2,w1,w2 = map(F,(l1,l2,ei1,ei2,w1,w2))
    length = l1+l2
    ra0 = (w1*l1*(length-l1/2)+w2*l2*l2/2)/length
    base = ((F(0),ra0,-w1/2), (ra0*l1-w1*l1*l1/2,ra0-w1*l1,-w2/2))
    unit = ((F(0),l2/length), (l1*l2/length,-l1/length))
    def product_integral(a,b,L):
        return sum(x*y*L**(i+j+1)/(i+j+1) for i,x in enumerate(a) for j,y in enumerate(b))
    displacement = sum(product_integral(m,u,L)/EI for m,u,L,EI in zip(base,unit,(l1,l2),(ei1,ei2)))
    flexibility = sum(product_integral(u,u,L)/EI for u,L,EI in zip(unit,(l1,l2),(ei1,ei2)))
    rb = displacement/flexibility
    ra = ra0-rb*l2/length
    rc = w1*l1+w2*l2-ra-rb
    return ra*l1-w1*l1*l1/2, (ra,rb,rc)


@pytest.mark.parametrize("spans,rigidities,dead,live", [
    ((14,29),(20000000000,47000000000),(11,23),(31,17)),
    ((31,13),(51000000000,19000000000),(0,0),(0,41)),
    ((15,27),(23000000000,57000000000),(7,0),(29,0)),
    ((25,19),(60000000000,30000000000),(9,12),(0,0)),
    ((18,37),(29000000000,43000000000),(0,0),(0,0)),
])
def test_every_pattern_matches_independent_released_support_virtual_work(spans,rigidities,dead,live):
    p = replace(demo_problem(),spans=spans,flexural_rigidities=rigidities,dead_loads=dead,live_loads=live)
    result = run(p)
    assert result.run_result.status == "solved"
    for row in verified_case_results(result).values():
        mb, reactions = _released_support_virtual_work(*spans,*rigidities,*row["q"])
        assert row["pier_moment"] == mb
        assert row["reactions"] == reactions


def test_deeply_nested_json_is_a_rejected_proposal_not_an_engine_failure(demo):
    event = demo.run_result.trace[2]
    verdict = verify(demo_problem(),replace(event.candidate,claim="["*1200+"0"+"]"*1200),event.before)
    assert verdict.decision is Decision.REJECT
    assert verdict.facts == ()


@pytest.mark.parametrize("kwargs", [{"complete":True},{"wrong_first":1}])
def test_provider_and_offline_options_are_validated(kwargs):
    with pytest.raises(TypeError):
        build_agent(demo_problem(),**kwargs)
