"""Adversarial certificate checks, independent of the active-set search."""
from dataclasses import replace
from fractions import Fraction
import json

import pytest

from resimind import Decision, State, Task
from resimind.domains import optimization as qp


@pytest.fixture(scope="module")
def demo():
    return qp.run_demo().run_result


def accepted(demo, stage):
    return next(event for event in demo.trace if event.candidate.action == qp.ACTIONS[(0, 1, 1, 2)[stage]]
                and event.decision is Decision.ACCEPT)


def mutate(demo, stage, changes):
    event = accepted(demo, stage)
    payload = json.loads(event.candidate.claim)
    if stage in (1, 2):
        payload["primal" if stage == 1 else "dual"].update(changes)
    else:
        payload.update(changes)
    candidate = replace(event.candidate, claim=json.dumps(payload))
    return qp.OptimizationVerifier(qp.demo_problem()).verify(
        candidate, event.before, event.residual_before, demo.evidence)


def step(problem, state, stage, payload):
    candidate = qp.Candidate(f"step-{stage}", qp.ACTIONS[stage], qp.TARGETS[stage],
                             json.dumps(payload), qp.EVIDENCE_IDS + qp.FACT_IDS[:stage])
    evidence = qp.OptimizationTool(problem).collect(Task("check", "check", qp.DOMAIN))
    verdict = qp.OptimizationVerifier(problem).verify(
        candidate, state, qp.OptimizationDomain(problem).rebuild(state), evidence)
    return verdict, State(state.revision + 1, state.facts + verdict.facts) if verdict.facts else state


def test_demo_requires_atomic_witness_and_global_certificate(demo):
    assert demo.status == "solved"
    assert [event.decision.value for event in demo.trace] == ["accept", "reject", "accept", "accept"]
    assert [event.residual_after.measure for event in demo.trace] == [2, 2, 1, 0]
    rejected = demo.trace[1]
    assert json.loads(rejected.candidate.claim)["primal"]["x"] == ["26/15", "1/5", "16/15"]
    assert rejected.before == rejected.after
    assert rejected.reasons == ("primal_inequality_violation",)
    assert json.loads(demo.state.facts[1].value) == {
        "x": ["1", "3/4", "5/4"], "objective": "-73/8", "slack": ["1", "3/4", "5/4", "0"]}
    assert json.loads(demo.state.facts[2].value) == {"lambda": ["1/2"], "mu": ["0", "0", "0", "11/4"]}
    assert not demo.trace[-2].residual_after.solved  # KKT is not the final requested proof artifact.


@pytest.mark.parametrize("changes,reason", [
    ({"d": ["4", "-7/4", "2"]}, "ldl_diagonal_not_positive"),
    ({"d": ["4", "0", "2"]}, "ldl_diagonal_not_positive"),
    ({"d": ["4", "2", "2"]}, "ldl_factorization_mismatch"),
    ({"l": [["1", "1", "0"], ["1/4", "1", "0"], ["0", "0", "1"]]},
     "ldl_factor_must_be_unit_lower_triangular"),
])
def test_invalid_positive_definiteness_certificate(demo, changes, reason):
    verdict = mutate(demo, 0, changes)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == (reason,)


@pytest.mark.parametrize("changes,reason", [
    ({"x": ["1", "1", "1/2"]}, "primal_equality_violation"),
    ({"x": ["2", "0", "1"]}, "primal_inequality_violation"),
    ({"slack": ["1", "3/4", "5/4", "1"]}, "primal_slack_mismatch"),
    ({"objective": "-10"}, "objective_value_mismatch"),
])
def test_primal_is_checked_against_original_problem(demo, changes, reason):
    verdict = mutate(demo, 1, changes)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == (reason,)


@pytest.mark.parametrize("changes,reason", [
    ({"mu": ["0", "0", "0", "-11/4"]}, "negative_inequality_multiplier"),
    ({"mu": ["0", "0", "0", "0"]}, "stationarity_mismatch"),
    ({"lambda": ["1"]}, "stationarity_mismatch"),
])
def test_kkt_needs_dual_feasibility_and_exact_stationarity(demo, changes, reason):
    verdict = mutate(demo, 2, changes)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == (reason,)


def test_stationarity_alone_does_not_establish_complementarity():
    problem = qp.QuadraticProgram(q=((2,),), c=(-1,), g=((1,),), h=(1,))
    first, state = step(problem, State(), 0, {"l": [["1"]], "d": ["2"]})
    assert first.decision is Decision.ACCEPT
    verdict, after = step(problem, state, 1, {
        "primal": {"x": ["0"], "objective": "0", "slack": ["1"]},
        "dual": {"lambda": [], "mu": ["1"]}})
    assert verdict.reasons == ("complementarity_mismatch",)
    assert after == state


@pytest.mark.parametrize("changes,reason", [
    ({"weights": ["0", "7/8", "1"]}, "square_weight_not_positive"),
    ({"weights": ["3", "7/8", "1"]}, "global_quadratic_coefficient_mismatch"),
    ({"offsets": ["0", "-3/4", "-5/4"]}, "global_linear_coefficient_mismatch"),
    ({"mu": ["0", "0", "0", "1"]}, "global_multipliers_mismatch"),
    ({"bound": "-10"}, "global_bound_mismatch"),
])
def test_global_certificate_expands_polynomial_not_just_status(demo, changes, reason):
    verdict = mutate(demo, 3, changes)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == (reason,)


def test_alternative_valid_square_certificate_is_accepted(demo):
    # Negating one square's direction and offset gives the same polynomial.
    event = accepted(demo, 3)
    proof = json.loads(event.candidate.claim)
    proof["directions"][0] = [str(-Fraction(value)) for value in proof["directions"][0]]
    proof["offsets"][0] = str(-Fraction(proof["offsets"][0]))
    assert mutate(demo, 3, proof).decision is Decision.ACCEPT


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_skipping_prerequisites_defers_even_an_exact_correct_certificate(demo, stage):
    event = accepted(demo, stage)
    candidate = replace(event.candidate, refs=qp.EVIDENCE_IDS)
    state = State()
    verdict = qp.OptimizationVerifier(qp.demo_problem()).verify(
        candidate, state, qp.OptimizationDomain(qp.demo_problem()).rebuild(state), demo.evidence)
    assert verdict.decision is Decision.DEFER
    assert verdict.reasons == ("missing_verified_prerequisite",)


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_required_certificate_refs_cannot_be_omitted(demo, stage):
    event = accepted(demo, stage)
    verdict = qp.OptimizationVerifier(qp.demo_problem()).verify(
        replace(event.candidate, refs=qp.EVIDENCE_IDS), event.before, event.residual_before, demo.evidence)
    assert verdict.reasons == ("missing_certificate_refs",)


@pytest.mark.parametrize("field,value", [("value", (("5", "1", "0"), ("1", "2", "0"), ("0", "0", "2"))),
                                          ("scope", "another-problem"), ("source", "model-says-so"),
                                          ("unit", "float"), ("subject", "different-qp")])
def test_evidence_binding_rejects_modified_problem_metadata(demo, field, value):
    event = accepted(demo, 0)
    evidence = (replace(demo.evidence[0], **{field: value}),) + demo.evidence[1:]
    verdict = qp.OptimizationVerifier(qp.demo_problem()).verify(
        event.candidate, event.before, event.residual_before, evidence)
    assert verdict.reasons == ("qp_evidence_mismatch",)


def test_evidence_from_another_valid_problem_cannot_authorize_current_certificate(demo):
    problem = replace(qp.demo_problem(), c=(-9, -3, -3))
    evidence = qp.OptimizationTool(problem).collect(Task("other", "other", qp.DOMAIN))
    event = accepted(demo, 0)
    verdict = qp.OptimizationVerifier(qp.demo_problem()).verify(
        event.candidate, event.before, event.residual_before, evidence)
    assert verdict.reasons == ("qp_evidence_mismatch",)


@pytest.mark.parametrize("field,value", [("evidence_refs", (qp.EVIDENCE_IDS[0],)), ("unit", "float"),
                                          ("id", "fake-id"), ("scope", "wrong-scope")])
def test_residual_revalidates_provenance_and_canonical_record(demo, field, value):
    bad = replace(demo.state.facts[0], **{field: value})
    residual = qp.OptimizationDomain(qp.demo_problem()).rebuild(State(4, (bad,) + demo.state.facts[1:]))
    assert not residual.solved
    assert qp.TARGETS[0] in residual.hard_constraints


def test_residual_revalidates_algebra_after_fact_tampering(demo):
    bad = replace(demo.state.facts[2], value='{"lambda":["1/2"],"mu":["0","0","0","0"]}')
    residual = qp.OptimizationDomain(qp.demo_problem()).rebuild(State(4, demo.state.facts[:2] + (bad,) + demo.state.facts[3:]))
    assert qp.TARGETS[1] in residual.unknowns
    assert qp.TARGETS[2] in residual.goals


@pytest.mark.parametrize("bad", [True, 1.0, "1.1", "NaN", "1/0", "1" * 257, 2**2050, 10**300, Fraction(10**300, 3)])
def test_problem_rejects_inexact_or_unbounded_input(bad):
    with pytest.raises(ValueError):
        qp.QuadraticProgram(q=((2,),), c=(bad,))


@pytest.mark.parametrize("kwargs", [
    {"q": ((1, 2), (0, 1)), "c": (0, 0)},
    {"q": ((1,),), "c": ()},
    {"q": ((1,),), "c": (0, 0)},
    {"q": ((1,),), "c": (0,), "a": ((1,),), "b": ()},
    {"q": ((1,),), "c": (0,), "g": ((1,),) * 9, "h": (0,) * 9},
    {"q": ((1,),), "c": (0,), "a": ((1,), (1,)), "b": (0, 0)},
])
def test_problem_rejects_invalid_shape_or_asymmetry(kwargs):
    with pytest.raises(ValueError):
        qp.QuadraticProgram(**kwargs)


@pytest.mark.parametrize("diagonal", [0, -1])
def test_non_positive_definite_problem_never_claims_solved(diagonal):
    problem = qp.QuadraticProgram(q=((diagonal,),), c=(1,))
    verdict, _ = step(problem, State(), 0, {"l": [["1"]], "d": [str(diagonal)]})
    assert verdict.decision is Decision.REJECT
    run = qp.build_agent(problem).run(Task("nonconvex", "check", qp.DOMAIN)).run_result
    assert run.status == "stalled" and not run.residual.solved


@pytest.mark.parametrize("problem,x,objective", [
    (qp.QuadraticProgram(q=((2,),), c=(-6,)), ["3"], "-9"),
    (qp.QuadraticProgram(q=((2, 0), (0, 2)), c=(-8, -2), a=((1, 1),), b=(2,),
                         g=((-1, 0), (0, -1)), h=(0, 0)), ["2", "0"], "-12"),
    (qp.QuadraticProgram(q=((2,),), c=(0,), g=((-1,),), h=(-2,)), ["2"], "4"),
    (qp.QuadraticProgram(q=(("1/2",),), c=("-1/3",)), ["2/3"], "-1/9"),
])
def test_multiple_problem_families_and_different_active_constraints(problem, x, objective):
    run = qp.build_agent(problem).run(Task("variant", "prove", qp.DOMAIN)).run_result
    assert run.status == "solved"
    primal = json.loads(next(f.value for f in run.state.facts if f.id == qp.PRIMAL_FACT))
    assert primal["x"] == x
    assert primal["objective"] == objective


def test_infeasible_problem_stalls_without_false_infeasibility_certificate():
    problem = qp.QuadraticProgram(q=((2,),), c=(0,), g=((1,), (-1,)), h=(0, -1))
    run = qp.build_agent(problem).run(Task("empty", "prove", qp.DOMAIN)).run_result
    assert run.status == "stalled" and run.residual.measure == 3


def test_verifier_and_domain_do_not_call_proposer_search(demo, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("verifier called proposer solver")
    for name in ("_solve", "_ldl", "_stationary", "_active_set_solution", "_offline_completion"):
        monkeypatch.setattr(qp, name, forbidden)
    verifier = qp.OptimizationVerifier(qp.demo_problem())
    for stage in range(4):
        event = accepted(demo, stage)
        assert verifier.verify(event.candidate, event.before, event.residual_before, demo.evidence).decision is Decision.ACCEPT
    assert qp.OptimizationDomain(qp.demo_problem()).rebuild(demo.state).solved


@pytest.mark.parametrize("claim", ['{"l":[],"l":[],"d":[]}', '{"l":NaN,"d":[]}', '[]', 'null', 'x', '"' + 'a' * 17000 + '"'])
def test_malformed_certificate_rejected(demo, claim):
    event = accepted(demo, 0)
    verdict = qp.OptimizationVerifier(qp.demo_problem()).verify(
        replace(event.candidate, claim=claim), event.before, event.residual_before, demo.evidence)
    assert verdict.decision is Decision.REJECT and not verdict.facts


def test_injected_completion_gets_actual_rejection_feedback_and_can_supply_certificates(demo):
    proposals = iter(event.candidate.to_dict() for event in demo.trace)
    prompts = []
    def complete(prompt):
        prompts.append(json.loads(prompt))
        return json.dumps(next(proposals))
    run = qp.build_agent(qp.demo_problem(), complete=complete).run(Task("injected", "prove", qp.DOMAIN)).run_result
    assert run.status == "solved"
    assert prompts[2]["last_feedback"]["reasons"] == ["primal_inequality_violation"]
    assert prompts[2]["last_feedback"]["before_revision"] == prompts[2]["last_feedback"]["after_revision"]


def test_feasible_nonoptimal_proposal_is_atomic_and_corrected_from_feedback(demo):
    exact = accepted(demo, 1).candidate.to_dict()
    feasible_wrong = dict(exact)
    payload = json.loads(exact["claim"])
    payload["primal"] = {"x": ["1", "1", "1"], "objective": "-9", "slack": ["1", "1", "1", "0"]}
    feasible_wrong["claim"] = json.dumps(payload)
    feasible_wrong["id"] = "feasible-but-nonoptimal"
    prompts = []
    def complete(prompt):
        data = json.loads(prompt)
        prompts.append(data)
        if len(prompts) == 1:
            return json.dumps(accepted(demo, 0).candidate.to_dict())
        if len(prompts) == 2:
            return json.dumps(feasible_wrong)
        if len(prompts) == 3:
            assert data["last_feedback"]["reasons"] == ["stationarity_mismatch"]
            assert [f["id"] for f in data["state"]["facts"]] == [qp.CONVEXITY_FACT]
            return json.dumps(exact)
        return json.dumps(accepted(demo, 3).candidate.to_dict())
    run = qp.build_agent(qp.demo_problem(), complete).run(Task("correct", "prove", qp.DOMAIN)).run_result
    assert run.status == "solved"
    assert run.trace[1].decision is Decision.REJECT
    assert run.trace[1].before == run.trace[1].after
    assert len(run.trace[2].verdict.facts) == 2  # primal and dual commit together


def test_scaled_equivalent_square_certificate_accepted(demo):
    payload = json.loads(accepted(demo, 3).candidate.claim)
    payload["directions"][0] = [str(2 * Fraction(x)) for x in payload["directions"][0]]
    payload["offsets"][0] = str(2 * Fraction(payload["offsets"][0]))
    payload["weights"][0] = str(Fraction(payload["weights"][0]) / 4)
    assert mutate(demo, 3, payload).decision is Decision.ACCEPT


def test_optional_demonstration_failure_can_be_disabled():
    run = qp.build_agent(qp.demo_problem(), wrong_first=False).run(Task("direct", "prove", qp.DOMAIN)).run_result
    assert run.status == "solved"
    assert [event.decision for event in run.trace] == [Decision.ACCEPT] * 3
