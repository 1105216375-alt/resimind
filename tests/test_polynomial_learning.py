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


def _model_rewrite(before, after, attempt, *, action="rewrite_polynomial", rule_id="", refs=None):
    return json.dumps({"id": f"attempt-{attempt}", "action": action, "target": TARGET,
                       "claim": json.dumps({"before": before, "after": after, "rule_id": rule_id}),
                       "refs": [INPUT_ID] if refs is None else refs})


def test_structured_guidance_is_opt_in_and_strategy_is_validated():
    prompts = []

    def complete(prompt):
        prompts.append(json.loads(prompt))
        return _model_rewrite("(x+1)**2", "x*x+2*x+1", 1)

    run(PolynomialProblem("(x+1)**2", ("x",)), complete=complete)
    assert "polynomial_guidance" not in prompts[0]
    with pytest.raises(ValueError, match="guidance"):
        build_learning_agent(PolynomialProblem("x", ("x",)), library(), guidance="automatic-solver")


def test_structured_guidance_exposes_ast_paths_without_computing_answers(monkeypatch):
    from resimind import Residual
    from resimind.domains import polynomial_learning as module

    def forbidden(*args, **kwargs):
        raise AssertionError("proposal guidance must not invoke a checker or primitive solver")

    prompts = []
    problem = PolynomialProblem("(x+1)**2*(x-1)", ("x",))

    def complete(prompt):
        prompts.append(json.loads(prompt))
        return _model_rewrite(problem.expression, "999", 1)

    proposer = module._StructuredPolynomialProposer(
        complete, problem=problem, allowed_actions=module.ACTIONS)
    monkeypatch.setattr(module, "verify_identity", forbidden)
    monkeypatch.setattr(module, "_primitive", forbidden)
    candidates = proposer.propose(State(), Residual(goals=(TARGET,)))
    assert len(candidates) == 1
    assert json.loads(candidates[0].claim)["after"] == "999"
    guidance = prompts[0]["polynomial_guidance"]
    subterms = guidance["unresolved_subexpressions"]
    assert subterms == {
        "items": [{"path": "$.left", "operator": "**", "expression": "(x + 1) ** 2",
                   "expression_truncated": False},
                  {"path": "$", "operator": "*", "expression": "(x + 1) ** 2 * (x - 1)",
                   "expression_truncated": False}],
        "total": 2, "omitted": 0}
    assert "only to its named monomial" in guidance["instructions"]
    assert guidance["rejected_rewrites_at_current_state"] == []


def test_structured_repetition_ignores_candidate_ids_and_expression_formatting():
    problem = PolynomialProblem("(x+1)**2", ("x",))
    prompts = []
    counts = WorkCounts()
    store = library()

    def complete(prompt):
        prompts.append(json.loads(prompt))
        after = "x*x+1" if len(prompts) % 2 else " ((x * x) + 1) "
        return _model_rewrite(problem.expression, after, len(prompts))

    result = run(problem, store, complete=complete, guidance="structured", max_steps=3, counts=counts)
    guidance = prompts[2]["polynomial_guidance"]
    assert len(guidance["rejected_rewrites_at_current_state"]) == 1
    assert guidance["rejected_rewrites_at_current_state"][0]["occurrences"] == 2
    assert guidance["repeated_rewrite_count"] == 1
    execution = result.result.run_result
    assert execution.state.facts == ()
    assert all(event.decision is Decision.REJECT for event in execution.trace)
    assert execution.residual.pending == (TARGET,)
    assert counts.proposal_calls == counts.identity_checks == 3
    assert store.lookup(DOMAIN) == ()


def test_structured_rejection_history_does_not_leak_across_accepted_state():
    problem = PolynomialProblem("(x+1)**2+(x+2)**2", ("x",))
    partial = "x*x+2*x+1+(x+2)**2"
    prompts = []

    def complete(prompt):
        payload = json.loads(prompt)
        prompts.append(payload)
        index = len(prompts)
        before = partial if index == 3 else problem.expression
        after = {1: "999", 2: partial, 3: "x*x+2*x+1+x*x+4*x+4"}[index]
        refs = [INPUT_ID, "polynomial:step:1"] if index == 3 else [INPUT_ID]
        return _model_rewrite(before, after, index, refs=refs)

    result = run(problem, complete=complete, guidance="structured", max_steps=3)
    assert prompts[1]["polynomial_guidance"]["rejected_rewrites_at_current_state"]
    guidance = prompts[2]["polynomial_guidance"]
    assert guidance["state_revision"] == 1
    assert guidance["rejected_rewrites_at_current_state"] == []
    assert guidance["unresolved_subexpressions"]["items"][0]["path"] == "$.right"
    assert result.result.run_result.status == "solved"
    assert len(result.admissions[0].candidate.derivation) == 2


def test_structured_history_is_bounded_and_keeps_most_recent_distinct_rewrites():
    problem = PolynomialProblem("(x+1)**2", ("x",))
    prompts = []

    def complete(prompt):
        prompts.append(json.loads(prompt))
        return _model_rewrite(problem.expression, str(1000 + len(prompts)), len(prompts))

    run(problem, complete=complete, guidance="structured", max_steps=12)
    history = prompts[-1]["polynomial_guidance"]["rejected_rewrites_at_current_state"]
    assert len(history) == 8
    assert [item["after"] for item in history] == [str(index) for index in range(1004, 1012)]
    assert all(item["occurrences"] == 1 for item in history)


def test_structured_subterm_context_has_explicit_bounds():
    from resimind.domains.polynomial_learning import _unresolved_subexpressions

    expression = "+".join("(x+1)**2" for _ in range(15))
    subterms = _unresolved_subexpressions(expression)
    assert len(subterms["items"]) == 12
    assert subterms["total"] == 15
    assert subterms["omitted"] == 3
    assert all(len(item["expression"]) <= 512 for item in subterms["items"])
    assert _unresolved_subexpressions("x*x+2*x+1") == {"items": [], "total": 0, "omitted": 0}


def test_structured_repeat_keys_preserve_action_and_rule_id():
    prompts = []
    problem = PolynomialProblem("(x+1)**2", ("x",))

    def complete(prompt):
        prompts.append(json.loads(prompt))
        index = len(prompts)
        return _model_rewrite(problem.expression, "999", index,
                              action="certify_expansion" if index == 2 else "rewrite_polynomial",
                              rule_id="different-rule" if index == 3 else "")

    run(problem, complete=complete, guidance="structured", max_steps=4)
    guidance = prompts[-1]["polynomial_guidance"]
    assert len(guidance["rejected_rewrites_at_current_state"]) == 3
    assert guidance["repeated_rewrite_count"] == 0


def test_structured_history_is_advisory_and_allows_corrected_evidence_refs():
    prompts = []
    problem = PolynomialProblem("(x+1)**2", ("x",))

    def complete(prompt):
        prompts.append(json.loads(prompt))
        return _model_rewrite(problem.expression, "x*x+2*x+1", len(prompts),
                              refs=[] if len(prompts) == 1 else [INPUT_ID])

    result = run(problem, complete=complete, guidance="structured", max_steps=2)
    assert result.result.run_result.status == "solved"
    assert prompts[1]["polynomial_guidance"]["rejected_rewrites_at_current_state"][0]["reasons"] == [
        "polynomial_evidence_mismatch"]
    assert [event.decision for event in result.result.run_result.trace] == [Decision.REJECT, Decision.ACCEPT]


@pytest.mark.parametrize("claim", ["not json", '{"before": 3}', "x" * 32769])
def test_structured_history_handles_malformed_claim_without_observer_errors(claim):
    prompts = []

    def complete(prompt):
        prompts.append(json.loads(prompt))
        return json.dumps({"id": f"invalid-{len(prompts)}", "action": "rewrite_polynomial",
                           "target": TARGET, "claim": claim, "refs": [INPUT_ID]})

    result = run(PolynomialProblem("(x+1)**2", ("x",)), complete=complete,
                 guidance="structured", max_steps=2)
    assert result.result.run_result.state.facts == ()
    assert len(result.result.run_result.trace) == 2
    assert prompts[-1]["polynomial_guidance"]["rejected_rewrites_at_current_state"] == []


def test_formatted_before_preserves_committed_chain_distillation_and_reload(tmp_path):
    problem = PolynomialProblem("(x+1)**2+(x+2)**2", ("x",))
    partial = "x*x+2*x+1+(x+2)**2"
    final = "x*x+2*x+1+x*x+4*x+4"
    proposals = [
        _model_rewrite(" ((x + 1) ** 2) + ((x + 2) ** 2) ", partial, 1),
        _model_rewrite(" ((x * x + 2 * x) + 1) + ((x + 2) ** 2) ", final, 2,
                       refs=[INPUT_ID, "polynomial:step:1"]),
    ]
    store = library()
    result = run(problem, store, complete=lambda _: proposals.pop(0), guidance="structured", max_steps=2)
    execution = result.result.run_result
    assert execution.status == "solved"
    assert [fact.value[:2] for fact in execution.state.facts] == [
        (problem.expression, partial), (partial, final)]
    assert PolynomialDomain(problem, WorkCounts()).rebuild(execution.state).solved
    assert result.admissions[0].candidate.derivation == (
        {"lhs": problem.expression, "rhs": partial}, {"lhs": partial, "rhs": final})
    path = tmp_path / "formatted-rules.json"
    store.save(path)
    reloaded = KnowledgeLibrary.load(path, {DOMAIN: AlgebraVerifier()})
    assert reloaded.lookup(DOMAIN) == store.lookup(DOMAIN)
    assert reloaded.lookup(DOMAIN)[0].verification.status == "verified"


@pytest.mark.parametrize("before", ["(1+x)**2", "x*x+2*x+1"])
def test_before_requires_same_ast_not_just_algebraic_equivalence(before):
    problem = PolynomialProblem("(x+1)**2", ("x",))
    candidate = Candidate("wrong-input-tree", "rewrite_polynomial", TARGET, json.dumps({
        "before": before, "after": "x*x+2*x+1", "rule_id": ""}), (INPUT_ID,))
    counts = WorkCounts()
    state = State()
    evidence = PolynomialTool(problem).collect(Task("test", "Verify", DOMAIN))
    verdict = PolynomialVerifier(problem, (), counts).verify(
        candidate, state, PolynomialDomain(problem, counts).rebuild(state), evidence)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons[0] == "malformed_or_unsupported_rewrite"
    assert verdict.reasons[1].startswith("rewrite_before_mismatch:")
    assert verdict.facts == ()


def test_structured_history_recognizes_formatted_before_as_current():
    prompts = []
    problem = PolynomialProblem("(x+1)**2", ("x",))

    def complete(prompt):
        prompts.append(json.loads(prompt))
        return _model_rewrite(" ((x + 1) ** 2) ", "999", len(prompts))

    result = run(problem, complete=complete, guidance="structured", max_steps=2)
    history = prompts[1]["polynomial_guidance"]["rejected_rewrites_at_current_state"]
    assert history[0]["before_matches_current"] is True
    assert history[0]["reasons"][0] == "polynomial_identity_not_proved"
    assert result.result.run_result.state.facts == ()


@pytest.mark.parametrize("guidance", ["default", "structured"])
def test_model_recovers_from_nested_json_rejection_without_accepting_malformed_claim(guidance):
    prompts = []
    problem = PolynomialProblem("(x+1)**2", ("x",))

    def complete(prompt):
        payload = json.loads(prompt)
        prompts.append(payload)
        if len(prompts) == 1:
            return json.dumps({"id": "malformed", "action": "rewrite_polynomial", "target": TARGET,
                               "claim": "before: (x+1)**2; after: x*x+2*x+1; rule_id: ",
                               "refs": [INPUT_ID]})
        assert payload["state"]["facts"] == []
        assert payload["last_feedback"]["reasons"][1].startswith("rewrite_claim_json:")
        return _model_rewrite(problem.expression, "x*x+2*x+1", 2)

    result = run(problem, complete=complete, guidance=guidance, max_steps=2)
    execution = result.result.run_result
    assert execution.status == "solved"
    assert [event.decision for event in execution.trace] == [Decision.REJECT, Decision.ACCEPT]
    assert execution.trace[0].before == execution.trace[0].after == State()
    assert len(execution.state.facts) == len(result.admissions[0].candidate.derivation) == 1
    instruction = prompts[0]["task_instruction"]
    assert "Serialization-only first-step example" in instruction
    assert '\\"before\\"' in instruction
    assert "YOUR_EQUIVALENT_REWRITE" in instruction


@pytest.mark.parametrize("claim,diagnostic", [
    ("before=x; after=x; rule_id=", "rewrite_claim_json:"),
    (json.dumps({"before": "(x+1)**2", "after": 3, "rule_id": ""}), "rewrite_claim_schema:"),
    (json.dumps({"before": "(1+x)**2", "after": "x*x+2*x+1", "rule_id": ""}),
     "rewrite_before_mismatch:"),
    (json.dumps({"before": "(x+1)**2", "after": "sqrt(x)", "rule_id": ""}),
     "rewrite_after_unsupported:"),
])
def test_rewrite_format_diagnostics_are_specific_and_never_commit(claim, diagnostic):
    problem = PolynomialProblem("(x+1)**2", ("x",))
    candidate = Candidate("bad", "rewrite_polynomial", TARGET, claim, (INPUT_ID,))
    counts = WorkCounts()
    state = State()
    verdict = PolynomialVerifier(problem, (), counts).verify(
        candidate, state, PolynomialDomain(problem, counts).rebuild(state),
        PolynomialTool(problem).collect(Task("test", "Verify", DOMAIN)))
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons[0] == "malformed_or_unsupported_rewrite"
    assert verdict.reasons[1].startswith(diagnostic)
    assert verdict.facts == ()
    assert counts.identity_checks == 0


def _state_candidate(prompt, after=None, *, rule=None, action="rewrite_polynomial", change=None):
    data = json.loads(prompt) if isinstance(prompt, str) else prompt
    claim = {"state_revision": data["state_revision"], "state_fingerprint": data["state_fingerprint"],
             "rule_id": "" if rule is None else rule["id"]}
    if action == "apply_verified_rule":
        claim["rule_fingerprint"] = "" if rule is None else rule["fingerprint"]
    else:
        claim["after"] = after
    if change:
        claim.update(change)
    return json.dumps({"id": f"state-{data['state_revision']}", "action": action, "target": TARGET,
                       "claim": json.dumps(claim), "refs": data["required_reference_ids"]})


def _state_verdict(problem, store, *, after=None, rule=None, action="rewrite_polynomial",
                   change=None, state=None, refs=None, allow=True, counts=None):
    from resimind.domains.polynomial_learning import _StateBoundPolynomialVerifier, _state_fingerprint
    state = State() if state is None else state
    counts = WorkCounts() if counts is None else counts
    current = state.facts[-1].value[1] if state.facts else problem.expression
    data = {"state_revision": state.revision, "state_fingerprint": _state_fingerprint(problem, state, current),
            "required_reference_ids": [INPUT_ID] + ([state.facts[-1].id] if state.facts else [])}
    payload = json.loads(_state_candidate(data, after, rule=rule, action=action, change=change))
    candidate = Candidate(payload["id"], action, TARGET, payload["claim"],
                          tuple(payload["refs"]) if refs is None else refs)
    verifier = _StateBoundPolynomialVerifier(problem, store.lookup(DOMAIN), counts,
                                             rule_lookup=store.get, allow_rule_execution=allow)
    return verifier.verify(candidate, state, PolynomialDomain(problem, counts).rebuild(state),
                           PolynomialTool(problem).collect(Task("test", "Verify", DOMAIN)))


@pytest.mark.parametrize("guidance", ["default", "structured"])
def test_state_bound_prompt_updates_current_binding_and_examples_after_commit(guidance):
    prompts = []
    problem = PolynomialProblem("(x+1)**2+(x+2)**2", ("x",))
    partial = "x*x+2*x+1+(x+2)**2"

    def complete(prompt):
        data = json.loads(prompt)
        prompts.append(data)
        return _state_candidate(data, partial if len(prompts) == 1 else "x*x+2*x+1+x*x+4*x+4")

    result = run(problem, complete=complete, proposal_protocol="state_bound", guidance=guidance,
                 max_steps=2)
    assert result.result.run_result.status == "solved"
    first, second = prompts
    assert first["current_expression"] == problem.expression
    assert second["current_expression"] == partial
    assert second["state_revision"] == 1
    assert first["state_fingerprint"] != second["state_fingerprint"]
    assert second["required_reference_ids"] == [INPUT_ID, "polynomial:step:1"]
    assert second["candidate_schema"]["properties"]["refs"]["const"] == second["required_reference_ids"]
    example = second["current_state_serialization_examples"][0]
    assert example["refs"] == second["required_reference_ids"]
    assert json.loads(example["claim"])["state_fingerprint"] == second["state_fingerprint"]
    assert "before" not in json.loads(example["claim"])
    assert "apply_verified_rule" not in second["allowed_actions"]
    assert len(result.admissions[0].candidate.derivation) == 2


@pytest.mark.parametrize("change,reason", [
    ({"state_revision": True}, "malformed_or_unsupported_rewrite"),
    ({"state_revision": 0.0}, "malformed_or_unsupported_rewrite"),
    ({"state_revision": -1}, "polynomial_state_mismatch"),
    ({"state_fingerprint": "forged"}, "polynomial_state_mismatch"),
    ({"before": "(x+1)**2"}, "malformed_or_unsupported_rewrite"),
    ({"after": "999"}, "polynomial_identity_not_proved"),
])
def test_state_bound_rejects_forged_binding_or_answer_without_committing(change, reason):
    verdict = _state_verdict(PolynomialProblem("(x+1)**2", ("x",)), library(),
                             after="x*x+2*x+1", change=change)
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons[0] == reason
    assert verdict.facts == ()


def test_state_bound_rejects_cross_problem_fingerprint_and_stale_refs():
    from resimind.domains.polynomial_learning import _state_fingerprint
    problem = PolynomialProblem("(x+1)**2+(x+2)**2", ("x",))
    other = PolynomialProblem("(x+2)**2+(x+1)**2", ("x",))
    first = _state_verdict(problem, library(), after="x*x+2*x+1+(x+2)**2")
    state = State(1, first.facts)
    for change, refs in [({"state_revision": 0}, None),
                         ({"state_fingerprint": _state_fingerprint(other, State(), other.expression)}, None),
                         ({}, (INPUT_ID,))]:
        verdict = _state_verdict(problem, library(), state=state, after="x*x+2*x+1+x*x+4*x+4",
                                 change=change, refs=refs)
        assert verdict.decision is Decision.REJECT
        assert verdict.facts == ()


def test_explicit_rule_application_materializes_verifies_and_distills_actual_trace(tmp_path):
    store = library()
    record = store.admit(_square_record().candidate)
    problem = PolynomialProblem("(x+1)**2", ("x",))
    prompts = []
    counts = WorkCounts()

    def complete(prompt):
        data = json.loads(prompt)
        prompts.append(data)
        return _state_candidate(data, rule=data["available_verified_identities"][0],
                                action="apply_verified_rule")

    result = run(problem, store, complete=complete, proposal_protocol="state_bound",
                 allow_rule_execution=True, counts=counts, max_steps=1)
    execution = result.result.run_result
    assert execution.status == "solved"
    fact = execution.state.facts[0]
    assert fact.value[0] == problem.expression
    assert fact.value[1] == "x * x + 2 * x * 1 + 1 * 1"
    assert fact.value[2:] == (record.candidate.id, record.fingerprint)
    assert counts.proposal_calls == counts.pattern_attempts == 1
    assert counts.primitive_node_visits == 0
    assert counts.identity_checks >= 2
    assert result.admissions[0].candidate.derivation == ({"lhs": problem.expression, "rhs": fact.value[1]},)
    path = tmp_path / "executable-rules.json"
    store.save(path)
    reloaded = KnowledgeLibrary.load(path, {DOMAIN: AlgebraVerifier()})
    assert reloaded.lookup(DOMAIN) == store.lookup(DOMAIN)
    assert "after" not in json.loads(execution.trace[0].candidate.claim)


@pytest.mark.parametrize("rule,change,allow", [
    ({"id": "missing", "fingerprint": "missing"}, None, True),
    (None, None, True),
    ({"id": "verified-square", "fingerprint": "forged"}, None, True),
    (None, {"after": "999"}, True),
    (None, None, False),
])
def test_rule_execution_rejects_missing_forged_or_unsupported_action(rule, change, allow):
    store = library()
    store.admit(_square_record().candidate)
    verdict = _state_verdict(PolynomialProblem("(x+1)**2", ("x",)), store,
                             action="apply_verified_rule", rule=rule, change=change, allow=allow)
    assert verdict.decision is Decision.REJECT
    assert verdict.facts == ()


def test_empty_library_cannot_execute_a_rule_or_generate_a_fallback():
    counts = WorkCounts()
    verdict = _state_verdict(PolynomialProblem("(x+1)**2", ("x",)), library(),
                             action="apply_verified_rule", rule={"id": "missing", "fingerprint": "missing"},
                             counts=counts)
    assert verdict.decision is Decision.REJECT
    assert verdict.facts == ()
    assert counts.pattern_attempts == counts.primitive_node_visits == counts.identity_checks == 0


@pytest.mark.parametrize("mutation", ["revoke", "new_rule_after_retrieval"])
def test_rule_execution_rechecks_current_library_and_initial_retrieval(mutation):
    store = library()
    record = store.admit(_square_record().candidate)

    def complete(prompt):
        data = json.loads(prompt)
        rule = data["available_verified_identities"][0]
        if mutation == "revoke":
            store.revoke(record.candidate.id, "withdrawn while model was proposing")
        else:
            added = store.admit(replace(record.candidate, id="added-late"))
            rule = {"id": added.candidate.id, "fingerprint": added.fingerprint}
        return _state_candidate(data, rule=rule, action="apply_verified_rule")

    result = run(PolynomialProblem("(x+1)**2", ("x",)), store, complete=complete,
                 proposal_protocol="state_bound", allow_rule_execution=True, max_steps=1, learn=False)
    assert result.result.run_result.state.facts == ()
    assert result.result.run_result.trace[0].reasons == ("rule_unavailable_or_changed",)


def test_state_bound_manual_rule_citation_preserves_exact_macro_contract():
    store = library()
    record = store.admit(_square_record().candidate)
    rule = {"id": record.candidate.id, "fingerprint": record.fingerprint}
    problem = PolynomialProblem("(x+1)**2", ("x",))
    exact = _state_verdict(problem, store, after="x*x+2*x*1+1*1", rule=rule, allow=False)
    simplified = _state_verdict(problem, store, after="x*x+2*x+1", rule=rule, allow=False)
    own = _state_verdict(problem, store, after="x*x+2*x+1", allow=False)
    assert exact.decision is own.decision is Decision.ACCEPT
    assert exact.facts[0].value[2:] == (record.candidate.id, record.fingerprint)
    assert simplified.decision is Decision.REJECT
    assert simplified.reasons == ("rule_not_applicable",)
    assert simplified.facts == ()


def test_selected_macro_size_is_bounded_before_materialization(monkeypatch):
    from itertools import product
    from resimind.domains import polynomial_learning as module
    rhs = "+".join("*".join(term) for term in product(("u", "v"), repeat=4))
    store = library()
    record = store.admit(KnowledgeCandidate(
        "fourth-power", DOMAIN, "polynomial_identity",
        {"lhs": "(u+v)**4", "rhs": rhs, "variables": ["u", "v"]},
        derivation=({"lhs": "(u+v)**4", "rhs": rhs},), source_task_id="source"))
    assert record.status == "verified"
    left, right = "+".join(["x"] * 20), "+".join(["y"] * 20)
    problem = PolynomialProblem(f"(({left})+({right}))**4", ("x", "y"))

    def forbidden(*args, **kwargs):
        raise AssertionError("oversized substitution must be rejected before AST materialization")

    monkeypatch.setattr(module, "_substitute", forbidden)
    verdict = _state_verdict(problem, store, action="apply_verified_rule",
                             rule={"id": record.candidate.id, "fingerprint": record.fingerprint})
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("rule_result_unsupported",)
    assert verdict.facts == ()


@pytest.mark.parametrize("kwargs", [
    {"proposal_protocol": "unknown"}, {"proposal_protocol": "state_bound"},
    {"allow_rule_execution": True}, {"allow_rule_execution": 1},
])
def test_new_protocol_options_require_explicit_valid_opt_in(kwargs):
    with pytest.raises(ValueError):
        build_learning_agent(PolynomialProblem("x", ("x",)), library(), **kwargs)


def test_explicit_rule_execution_independently_rechecks_even_a_false_verified_label():
    from resimind.knowledge import Verification

    class IncorrectAdmission:
        def verify(self, candidate):
            return Verification("verified", "incorrect trusted admission for this regression test", "test")

    store = KnowledgeLibrary({DOMAIN: IncorrectAdmission()})
    record = store.admit(KnowledgeCandidate(
        "false-square", DOMAIN, "polynomial_identity",
        {"lhs": "(u+v)**2", "rhs": "u*u+v*v", "variables": ["u", "v"]},
        derivation=({"lhs": "(u+v)**2", "rhs": "u*u+v*v"},), source_task_id="source"))
    counts = WorkCounts()
    verdict = _state_verdict(PolynomialProblem("(x+1)**2", ("x",)), store,
                             action="apply_verified_rule", counts=counts,
                             rule={"id": record.candidate.id, "fingerprint": record.fingerprint})
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons[0] == "polynomial_identity_not_proved"
    assert verdict.facts == ()
    assert counts.identity_checks == 1


def test_rule_execution_rejects_changed_record_after_initial_retrieval():
    from resimind.domains.polynomial_learning import _StateBoundPolynomialVerifier, _state_fingerprint
    first_store, changed_store = library(), library()
    initial = first_store.admit(_square_record().candidate)
    changed = changed_store.admit(replace(initial.candidate, source_task_id="changed-source"))
    problem = PolynomialProblem("(x+1)**2", ("x",))
    state, counts = State(), WorkCounts()
    context = {"state_revision": 0, "state_fingerprint": _state_fingerprint(problem, state, problem.expression),
               "required_reference_ids": [INPUT_ID]}
    payload = json.loads(_state_candidate(context, action="apply_verified_rule", rule={
        "id": initial.candidate.id, "fingerprint": initial.fingerprint}))
    candidate = Candidate(payload["id"], payload["action"], TARGET, payload["claim"], (INPUT_ID,))
    verifier = _StateBoundPolynomialVerifier(problem, (initial,), counts,
                                             rule_lookup=lambda _: changed, allow_rule_execution=True)
    verdict = verifier.verify(candidate, state, PolynomialDomain(problem, counts).rebuild(state),
                               PolynomialTool(problem).collect(Task("test", "Verify", DOMAIN)))
    assert verdict.decision is Decision.REJECT
    assert verdict.reasons == ("rule_unavailable_or_changed",)
    assert verdict.facts == ()


def test_state_bound_proposal_context_does_not_run_verifiers_or_materialize_rules(monkeypatch):
    from resimind import Residual
    from resimind.domains import polynomial_learning as module
    problem = PolynomialProblem("(x+1)**2", ("x",))
    record = _square_record()

    def forbidden(*args, **kwargs):
        raise AssertionError("proposal context cannot compute or verify a rewrite")

    def complete(prompt):
        data = json.loads(prompt)
        return _state_candidate(data, rule=data["available_verified_identities"][0],
                                action="apply_verified_rule")

    proposer = module._StateBoundPolynomialProposer(
        complete, problem=problem, records=(record,), structured=True,
        allowed_actions=module.ACTIONS + (module.RULE_ACTION,))
    monkeypatch.setattr(module, "verify_identity", forbidden)
    monkeypatch.setattr(module, "_macro", forbidden)
    monkeypatch.setattr(module, "_bounded_macro", forbidden)
    candidate, = proposer.propose(State(), Residual(goals=(TARGET,)))
    assert candidate.action == "apply_verified_rule"
    assert "after" not in json.loads(candidate.claim)


def test_conditional_rule_selection_preference_is_only_in_executable_mode():
    instructions = {}
    for enabled in (False, True):
        def complete(prompt):
            instructions[enabled] = json.loads(prompt)["task_instruction"]
            return "null"
        run(PolynomialProblem("(x+1)**2", ("x",)), complete=complete, max_steps=1,
            proposal_protocol="state_bound", allow_rule_execution=enabled)
    assert "prefer apply_verified_rule" not in instructions[False]
    assert "If a listed identity matches an unexpanded subexpression of current_expression" in instructions[True]
    assert "prefer apply_verified_rule over manually reproducing that expansion; otherwise use your own rewrite" in instructions[True]
    assert "No rule is automatically selected or applied" in instructions[True]
