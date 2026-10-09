"""Cross-task proof acquisition, actual Agent reuse, and promotion boundaries."""
from dataclasses import replace
import json

import pytest

from resimind import Candidate, Decision, Fact, State, Task
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import (
    DOMAIN, INPUT_ID, SCOPE, TARGET, PolynomialDomain, PolynomialProblem,
    PolynomialTool, PolynomialVerifier, WorkCounts, build_learning_agent,
)
from resimind.knowledge import KnowledgeCandidate, KnowledgeLibrary


def library():
    return KnowledgeLibrary({DOMAIN: AlgebraVerifier()})


def run(problem, store=None, *, name="source", learn=True, **kwargs):
    store = library() if store is None else store
    return build_learning_agent(problem, store, **kwargs).run(
        Task(name, "Expand and verify", DOMAIN), learn=learn)


def test_discovery_persistence_and_cross_task_transfer(tmp_path):
    store = library()
    source = PolynomialProblem("(u+v)**2", ("u", "v"))
    learned = run(source, store)
    assert learned.result.run_result.status == "solved"
    assert len(learned.admissions) == 1
    record = learned.admissions[0]
    assert record.status == "verified"
    assert record.candidate.source_task_id == "source"
    assert record.candidate.derivation == tuple(
        {"lhs": fact.value[0], "rhs": fact.value[1]}
        for fact in learned.result.run_result.state.facts)
    path = tmp_path / "rules.json"
    store.save(path)
    fresh = KnowledgeLibrary.load(path, {DOMAIN: AlgebraVerifier()})
    transfer = PolynomialProblem("(2*x+3*y)**2", ("x", "y"))
    fixed = run(transfer, name="new", learn=False)
    grown = run(transfer, fresh, name="new", learn=False)
    assert fixed.result.run_result.status == grown.result.run_result.status == "solved"
    assert grown.result.run_result.steps < fixed.result.run_result.steps
    uses = [fact for fact in grown.result.run_result.state.facts if fact.value[2]]
    assert len(uses) == 1
    assert uses[0].value[2:] == (record.candidate.id, record.fingerprint)
    assert grown.admissions == ()
    assert len(fresh.lookup(domain=DOMAIN)) == 1


def test_revoked_rule_is_not_used():
    store = library()
    source = run(PolynomialProblem("(u+v)**2", ("u", "v")), store)
    store.revoke(source.admissions[0].candidate.id, "regression test")
    result = run(PolynomialProblem("(x+2)**2", ("x",)), store, name="new", learn=False)
    assert result.retrieved_ids == ()
    assert result.result.run_result.status == "solved"
    assert all(not fact.value[2] for fact in result.result.run_result.state.facts)


@pytest.mark.parametrize("expression,variables", [
    ("(u+v)**3", ("u", "v")),
    ("(x-y)*(x+y)", ("x", "y")),
    ("(x+2)**+2", ("x",)),
    ("(x+2)**0", ("x",)),
    ("(x+2)**1", ("x",)),
    ("-(x+y)*(x-y)", ("x", "y")),
    ("x*y+3", ("x", "y")),
])
def test_primitive_grammar_finishes_real_verified_derivations(expression, variables):
    result = run(PolynomialProblem(expression, variables))
    assert result.result.run_result.status == "solved"
    assert all(event.decision is Decision.ACCEPT for event in result.result.run_result.trace)
    assert not result.learning_errors


def test_incomplete_derivation_cannot_be_promoted():
    store = library()
    result = run(PolynomialProblem("(u+v)**3", ("u", "v")), store, max_steps=1)
    assert result.result.run_result.status != "solved"
    assert not result.result.run_result.residual.solved
    assert result.admissions == store.lookup(domain=DOMAIN) == ()


def test_model_candidates_are_verified_and_only_committed_proof_is_distilled():
    calls = []
    def complete(prompt):
        calls.append(json.loads(prompt))
        after = "999" if len(calls) == 1 else "x*x+2*x+1"
        return json.dumps({"id": f"model-{len(calls)}", "action": "rewrite_polynomial",
                           "target": TARGET, "claim": json.dumps({"before": "(x+1)**2",
                           "after": after, "rule_id": ""}), "refs": [INPUT_ID]})
    counts = WorkCounts()
    result = run(PolynomialProblem("(x+1)**2", ("x",)), complete=complete, counts=counts)
    trace = result.result.run_result.trace
    assert [event.decision for event in trace] == [Decision.REJECT, Decision.ACCEPT]
    assert trace[0].before == trace[0].after
    assert trace[0].residual_after.pending == (TARGET,)
    assert len(result.admissions[0].candidate.derivation) == 1
    assert counts.proposal_calls == 2
    assert calls[1]["last_feedback"]["decision"] == "reject"


def test_exact_coefficient_diagnostic_reaches_model_feedback():
    prompts = []

    def complete(prompt):
        payload = json.loads(prompt)
        prompts.append(payload)
        after = "x*x+1" if len(prompts) == 1 else "x*x+2*x+1"
        return json.dumps({"id": f"attempt-{len(prompts)}", "action": "rewrite_polynomial",
                           "target": TARGET, "claim": json.dumps({"before": "(x+1)**2",
                           "after": after, "rule_id": ""}), "refs": [INPUT_ID]})

    result = run(PolynomialProblem("(x+1)**2", ("x",)), complete=complete)
    reasons = prompts[1]["last_feedback"]["reasons"]
    assert reasons == ["polynomial_identity_not_proved",
                       "exact polynomial coefficients differ: monomial=x; lhs coefficient=2; rhs coefficient=0"]
    trace = result.result.run_result.trace
    assert [event.decision for event in trace] == [Decision.REJECT, Decision.ACCEPT]
    assert trace[0].before == trace[0].after
    assert len(result.admissions[0].candidate.derivation) == 1


def test_repeated_wrong_candidates_cannot_commit_or_learn_despite_diagnostics():
    store = library()
    counts = WorkCounts()
    prompts = []

    def complete(prompt):
        prompts.append(json.loads(prompt))
        return json.dumps({"id": f"attempt-{len(prompts)}", "action": "rewrite_polynomial",
                           "target": TARGET, "claim": json.dumps({"before": "(x+1)**2",
                           "after": "x*x+1", "rule_id": ""}), "refs": [INPUT_ID]})

    result = run(PolynomialProblem("(x+1)**2", ("x",)), store, complete=complete,
                 max_steps=3, counts=counts)
    execution = result.result.run_result
    assert execution.status != "solved"
    assert execution.state.facts == ()
    assert execution.residual.pending == (TARGET,)
    assert all(event.decision is Decision.REJECT for event in execution.trace)
    assert all("lhs coefficient=2" in event.reasons[1] for event in execution.trace)
    assert counts.identity_checks == counts.proposal_calls == 3
    assert result.admissions == store.lookup("algebra") == ()


@pytest.mark.parametrize("after,rule_id", [("999", ""), ("x*x+2*x+1", "made-up-rule"),
                                         ("x/x", ""), ("x ± 2", "")])
def test_unsound_or_unbound_candidates_never_clear_residual(after, rule_id):
    problem = PolynomialProblem("(x+1)**2", ("x",))
    counts = WorkCounts()
    residual = PolynomialDomain(problem, counts).rebuild(State())
    proposal = Candidate("bad", "rewrite_polynomial", TARGET, json.dumps(
        {"before": problem.expression, "after": after, "rule_id": rule_id}), (INPUT_ID,))
    evidence = PolynomialTool(problem).collect(Task("test", "Verify", DOMAIN))
    verdict = PolynomialVerifier(problem, (), counts).verify(proposal, State(), residual, evidence)
    assert verdict.decision is Decision.REJECT
    assert verdict.facts == ()


def test_residual_does_not_trust_a_solved_looking_fact():
    problem = PolynomialProblem("(x+1)**2", ("x",))
    fake = Fact("polynomial:step:1", problem.subject, "rewrite:1",
                (problem.expression, "999", "", ""), "rational-polynomial", (INPUT_ID,), SCOPE)
    with pytest.raises(ValueError, match="unverified"):
        PolynomialDomain(problem, WorkCounts()).rebuild(State(1, (fake,)))


def test_distiller_cannot_attribute_a_new_rule_to_another_task():
    store = library()
    problem = PolynomialProblem("(x+1)**2", ("x",))
    original = run(problem).admissions[0].candidate
    learner = build_learning_agent(problem, store)
    learner.distill = lambda result: (replace(original, source_task_id="somewhere-else"),)
    result = learner.run(Task("current", "Expand", DOMAIN))
    assert result.result.run_result.status == "solved"
    assert result.learning_errors == ("distillation_task_binding_mismatch",)
    assert not store.lookup(domain=DOMAIN)


def _square_record():
    store = library()
    return store.admit(KnowledgeCandidate(
        "verified-square", DOMAIN, "polynomial_identity",
        {"lhs": "(u+v)**2", "rhs": "u*u+2*u*v+v*v", "variables": ["u", "v"]},
        derivation=({"lhs": "(u+v)**2", "rhs": "u*u+2*u*v+v*v"},),
        source_task_id="square-discovery",
    ))


def _rule_verdict(problem, record, after, *, state=None, before=None, refs=None, rule_id=None):
    state = State() if state is None else state
    counts = WorkCounts()
    residual = PolynomialDomain(problem, counts).rebuild(state)
    candidate = Candidate("recall", "rewrite_polynomial", TARGET, json.dumps({
        "before": problem.expression if before is None else before,
        "after": after,
        "rule_id": record.candidate.id if rule_id is None else rule_id,
    }), (INPUT_ID,) if refs is None else refs)
    evidence = PolynomialTool(problem).collect(Task("new-task", "Verify", DOMAIN))
    return PolynomialVerifier(problem, (record,), counts).verify(candidate, state, residual, evidence)


def test_recalled_rule_accepts_formatting_and_preserves_provenance():
    problem = PolynomialProblem("(x+1)**2", ("x",))
    record = _square_record()
    # Same AST as the rule substitution, different spacing and parentheses.
    after = " ((x*x) + ((2*x)*1)) + (1*1) "
    verdict = _rule_verdict(problem, record, after)
    assert verdict.decision is Decision.ACCEPT
    assert verdict.facts[0].value == (problem.expression, after, record.candidate.id, record.fingerprint)


def test_equivalent_nonmatching_rewrite_cannot_claim_rule_reuse():
    problem = PolynomialProblem("(x+1)**2", ("x",))
    record = _square_record()
    # Collecting constants is an additional step beyond the cited substitution.
    after = "x*x+2*x+1"
    verdict = _rule_verdict(problem, record, after)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("rule_not_applicable",)
    assert verdict.facts == ()
    own_derivation = _rule_verdict(problem, record, after, rule_id="")
    assert own_derivation.decision is Decision.ACCEPT
    assert own_derivation.facts[0].value[2:] == ("", "")


@pytest.mark.parametrize("stale_field", ["before", "refs"])
def test_formatted_rule_reuse_does_not_bypass_current_state_binding(stale_field):
    problem = PolynomialProblem("(x+1)**2+(x+2)**2", ("x",))
    record = _square_record()
    first = "x*x+2*x*1+1*1+(x+2)**2"
    first_verdict = _rule_verdict(problem, record, first)
    assert first_verdict.decision is Decision.ACCEPT
    state = State(1, first_verdict.facts)
    before = problem.expression if stale_field == "before" else first
    refs = (INPUT_ID,) if stale_field == "refs" else (INPUT_ID, first_verdict.facts[-1].id)
    after = "(x*x+2*x*1+1*1)+(x*x+2*x*2+2*2)"
    verdict = _rule_verdict(problem, record, after, state=state, before=before, refs=refs)
    assert verdict.decision is Decision.REJECT
    assert verdict.facts == ()
