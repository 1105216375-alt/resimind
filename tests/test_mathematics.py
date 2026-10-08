"""The equation verifier, evidence binding, and multistep proof contract."""

from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
import json

import pytest

from residual_agent import Candidate, Decision, State
from residual_agent.agent import Task
from residual_agent.domains.mathematics import (
    ACTIONS, CHECK_TARGET, COEFFICIENT_FACT, COEFFICIENT_TARGET, DOMAIN,
    EVIDENCE_IDS, NORMALIZED_FACT, NORMALIZE_TARGET, SOLUTION_FACT, SOLVE_TARGET,
    EquationTool, LinearEquation, MathematicsDomain, MathematicsVerifier, build_agent, run_demo,
)


def task():
    return Task("equation-test", "Solve and check the configured equation", DOMAIN)


def fact(result, metric):
    return next(item for item in result.run_result.state.facts if item.metric == metric)


def candidate(stage, claim, refs=None):
    return Candidate("test-proposal", ACTIONS[stage],
                     (NORMALIZE_TARGET, SOLVE_TARGET, CHECK_TARGET)[stage], claim,
                     EVIDENCE_IDS if refs is None else refs)


def verify(problem, proposal, state=State(), evidence=None):
    if evidence is None:
        evidence = EquationTool(problem).collect(task())
    return MathematicsVerifier(problem).verify(proposal, state, MathematicsDomain(problem).rebuild(state), evidence)


def normalized(problem):
    a, b, c = (Fraction(getattr(problem, key)) for key in ("a", "b", "c"))
    verdict = verify(problem, candidate(0, f"{a}*{problem.variable}={c-b}"))
    assert verdict.decision is Decision.ACCEPT
    return State(1, verdict.facts)


def test_demo_records_real_rejection_correction_and_substitution():
    result = run_demo()
    run = result.run_result
    assert run.status == "solved"
    assert run.residual.solved
    assert [event.decision for event in run.trace] == [
        Decision.ACCEPT, Decision.REJECT, Decision.ACCEPT, Decision.ACCEPT,
    ]
    assert [event.residual_after.measure for event in run.trace] == [2, 2, 1, 0]
    assert run.trace[1].reasons == ("solution_claim_mismatch",)
    assert run.trace[1].before == run.trace[1].after
    assert run.trace[1].candidate.claim == "x=7/3"
    assert run.trace[2].candidate.claim == "x=4/3"
    assert fact(result, "solution").value == ("unique", "4/3")
    assert fact(result, "checked_solution").value == ("unique", "4/3", "5/3", "5/3")
    assert len(result.tool_events) == 1
    assert len(run.evidence) == 3
    assert json.loads(result.to_json())["run_result"]["status"] == "solved"


@pytest.mark.parametrize("a,b,c,expected", [
    (2, 3, 11, "4"),
    (-2, 3, 11, "-4"),
    (7, -3, -3, "0"),
    ("1/3", "-2/5", "7/11", "171/55"),
    (Fraction(-3, 4), Fraction(1, 2), Fraction(2, 3), "-2/9"),
    ("4/-6", "-2/-4", "+1/+3", "1/4"),
    (10**30, 1, 2, f"1/{10**30}"),
])
def test_reusable_equations_use_exact_arithmetic(a, b, c, expected):
    result = build_agent(LinearEquation(a, b, c, variable="velocity")).run(task())
    assert result.run_result.status == "solved"
    assert fact(result, "solution").value == ("unique", expected)
    checked = fact(result, "checked_solution")
    assert checked.value[2] == checked.value[3]
    assert all(item.subject == "linear-equation:velocity" for item in result.run_result.state.facts)


@pytest.mark.parametrize("b,c,classification", [(2, 3, "no_solution"), (2, 2, "all_rationals"),
                                               ("1/3", "2/6", "all_rationals"),
                                               ("-1/3", "1/3", "no_solution")])
def test_zero_coefficient_is_classified_without_division_or_fake_unique_solution(b, c, classification):
    result = build_agent(LinearEquation(0, b, c)).run(task())
    assert result.run_result.status == "solved"
    assert fact(result, "solution").value == (classification,)
    assert fact(result, "checked_solution").value[0] == classification
    assert result.run_result.trace[1].decision is Decision.REJECT


@pytest.mark.parametrize("value", [True, False, 1.0, float("nan"), float("inf"), "1.2", "1e2",
                                   " 2", "2 ", "", "1/0", "1/-0", "1 /2", "a", None, [], {}])
def test_coefficient_boundary_rejects_inexact_or_invalid_values(value):
    with pytest.raises(ValueError):
        LinearEquation(value, 0, 1)


@pytest.mark.parametrize("variable", ["", " x", "x ", "x+y", "3x", "x.y", True, None])
def test_variable_boundary(variable):
    with pytest.raises(ValueError):
        LinearEquation(1, 0, 1, variable)


def test_problem_is_immutable_and_normalized():
    problem = LinearEquation("+04/06", "1/-2", 3)
    assert (problem.a, problem.b, problem.c) == ("2/3", "-1/2", "3")
    with pytest.raises(FrozenInstanceError):
        problem.a = 2


@pytest.mark.parametrize("field,value", [("id", "other:a"), ("subject", "other-equation"),
                                       ("metric", "other"), ("value", "999"), ("value", True),
                                       ("unit", "float"), ("source", "model-output"),
                                       ("scope", "other-scope")])
def test_verifier_rejects_mismatched_input_evidence(field, value):
    problem = LinearEquation(2, 3, 11)
    evidence = EquationTool(problem).collect(task())
    changed = (replace(evidence[0], **{field: value}),) + evidence[1:]
    verdict = verify(problem, candidate(0, "2*x=8"), evidence=changed)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("equation_evidence_mismatch",)
    assert not verdict.facts


def test_missing_duplicate_or_extra_evidence_is_rejected():
    problem = LinearEquation(2, 3, 11)
    evidence = EquationTool(problem).collect(task())
    variants = (evidence[:2], (evidence[0], evidence[0], evidence[2]),
                evidence + (replace(evidence[0], id="extra"),))
    for changed in variants:
        assert verify(problem, candidate(0, "2*x=8"), evidence=changed).decision is Decision.REJECT


@pytest.mark.parametrize("stage,claim", [(1, "x=4"), (2, "substitution:11=11")])
def test_correct_final_answer_cannot_skip_derivation(stage, claim):
    verdict = verify(LinearEquation(2, 3, 11), candidate(stage, claim))
    assert verdict.decision is Decision.DEFER
    assert verdict.reasons == ("missing_verified_derivation",)
    assert not verdict.facts


def test_solution_needs_original_and_intermediate_references():
    problem = LinearEquation(2, 3, 11)
    state = normalized(problem)
    verdict = verify(problem, candidate(1, "x=4"), state)
    assert verdict.reasons == ("missing_derivation_refs",)
    assert verdict.decision is Decision.REJECT
    verdict = verify(problem, candidate(1, "x=4", (NORMALIZED_FACT, COEFFICIENT_FACT)), state)
    assert verdict.reasons == ("missing_original_equation_refs",)
    assert verdict.decision is Decision.REJECT
    refs = EVIDENCE_IDS + (NORMALIZED_FACT, COEFFICIENT_FACT)
    verdict = verify(problem, candidate(1, "x=4", refs), state)
    assert verdict.decision is Decision.ACCEPT
    assert verdict.facts[0].evidence_refs == EVIDENCE_IDS


@pytest.mark.parametrize("claim", ["x=999", "x=4.0", "verified=true", "solved", "x=8/2"])
def test_untrusted_solution_claim_does_not_control_the_verifier(claim):
    problem = LinearEquation(2, 3, 11)
    refs = EVIDENCE_IDS + (NORMALIZED_FACT, COEFFICIENT_FACT)
    verdict = verify(problem, candidate(1, claim, refs), normalized(problem))
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("solution_claim_mismatch",)
    assert not verdict.facts


def test_final_residual_requires_original_equation_check():
    result = run_demo().run_result
    before_check = result.trace[-1].before
    residual = MathematicsDomain(LinearEquation("3/2", "-1/3", "5/3")).rebuild(before_check)
    assert residual.pending == (CHECK_TARGET,)
    assert not residual.solved
    assert result.trace[-1].candidate.refs == EVIDENCE_IDS + (
        NORMALIZED_FACT, COEFFICIENT_FACT, SOLUTION_FACT,
    )


def test_wrong_substitution_cannot_complete_the_proof():
    problem = LinearEquation(2, 3, 11)
    before_check = build_agent(problem).run(task()).run_result.trace[-1].before
    refs = EVIDENCE_IDS + (NORMALIZED_FACT, COEFFICIENT_FACT, SOLUTION_FACT)
    verdict = verify(problem, candidate(2, "substitution:999=11", refs), before_check)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("substitution_claim_mismatch",)


def test_verifier_and_domain_check_intermediate_fact_value_and_provenance():
    problem = LinearEquation(2, 3, 11)
    state = normalized(problem)
    refs = EVIDENCE_IDS + (NORMALIZED_FACT, COEFFICIENT_FACT)
    for replacement in (replace(state.facts[0], value=("2", "999")),
                        replace(state.facts[0], evidence_refs=(EVIDENCE_IDS[0],)),
                        replace(state.facts[0], unit="unrelated")):
        corrupt = State(1, (replacement, state.facts[1]))
        verdict = verify(problem, candidate(1, "x=4", refs), corrupt)
        assert verdict.decision is Decision.DEFER
        assert NORMALIZE_TARGET in MathematicsDomain(problem).rebuild(corrupt).pending


def test_unverified_or_wrong_classification_facts_do_not_empty_residual():
    problem = LinearEquation(2, 3, 11)
    complete = build_agent(problem).run(task()).run_result.state
    for record in complete.facts:
        forged = replace(record, value="solved")
        state = State(complete.revision, tuple(forged if item.id == record.id else item for item in complete.facts))
        assert not MathematicsDomain(problem).rebuild(state).solved
    missing_coefficients = State(3, tuple(item for item in complete.facts if item.id != COEFFICIENT_FACT))
    assert COEFFICIENT_TARGET in MathematicsDomain(problem).rebuild(missing_coefficients).pending


def test_custom_model_callback_receives_feedback_and_cannot_self_certify():
    prompts = []

    def complete(prompt):
        payload = json.loads(prompt)
        prompts.append(payload)
        return json.dumps({"id": f"wrong-{len(prompts)}", "action": ACTIONS[0],
                           "target": NORMALIZE_TARGET, "claim": "2*x=999", "refs": list(EVIDENCE_IDS)})

    result = build_agent(LinearEquation(2, 3, 11), complete=complete).run(task())
    assert result.run_result.status == "stalled"
    assert result.run_result.state.facts == ()
    assert prompts[0]["last_feedback"] is None
    assert prompts[1]["last_feedback"]["reasons"] == ["normalization_claim_mismatch"]
    assert "normalize_equation" in prompts[0]["task_instruction"]


def test_reusing_agent_starts_fresh_and_repeats_the_auditable_demo():
    agent = build_agent(LinearEquation(2, 3, 11))
    first, second = agent.run(task()), agent.run(task())
    assert first.to_dict() == second.to_dict()
    assert len(second.run_result.trace) == 4


def test_wrong_task_domain_stops_during_collection():
    result = build_agent(LinearEquation(2, 3, 11)).run(Task("other", "Other task", "other"))
    assert result.run_result.status == "error"
    assert result.run_result.state.facts == ()
    assert result.tool_events[0].status == "error"


def test_adapter_constructor_types_are_checked():
    for factory in (EquationTool, MathematicsDomain, MathematicsVerifier, build_agent):
        with pytest.raises(ValueError):
            factory((2, 3, 11))
    with pytest.raises(ValueError):
        build_agent(LinearEquation(2, 3, 11), complete="not callable")
