"""Goal handling separates equivalent notation from useful verified transitions."""
import ast
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from resimind import Decision, Task, Verdict
from resimind.domains import adaptive_polynomial as module
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.domains.algebra import AlgebraVerifier, verify_identity
from resimind.domains.polynomial_learning import DOMAIN, PolynomialProblem
from resimind.integrations.lean import LeanPolynomialBackend, LeanProofResult, polynomial_binding_digest
from resimind.knowledge import KnowledgeCandidate, KnowledgeLibrary


def library():
    return KnowledgeLibrary({DOMAIN: AlgebraVerifier()})


def same(left, right):
    return ast.dump(ast.parse(left, mode="eval")) == ast.dump(ast.parse(right, mode="eval"))


def solve(expression, complete=None, *, stats=None, store=None, learn=False, **options):
    stats = AdaptiveStats() if stats is None else stats
    store = library() if store is None else store
    # Exercise model proposals deterministically; scheduling itself is tested in
    # test_cost_aware_scheduling.py. Goal handling remains enabled by default.
    options.setdefault("cost_aware_scheduling", False)
    outcome = build_adaptive_learning_agent(PolynomialProblem(expression, ("z",)), store,
                                            complete=complete, stats=stats, **options).run(
        Task("public-goal-progress", "Expand", DOMAIN), learn=learn)
    return outcome, stats, store


@pytest.fixture
def lean(monkeypatch, tmp_path):
    """Typed integration fixture; separate tests execute the real Lean compiler."""
    calls, statuses = [], []

    def check(self, before, after, variables, *, tactic="grind"):
        calls.append({"before": before, "after": after, "tactic": tactic})
        status = statuses.pop(0) if statuses else "verified"
        return LeanProofResult(
            status=status, tactic=tactic, target="v0 = v0", actual_target="v0 : Rat\n⊢ v0 = v0",
            goals=() if status == "verified" else ("v0 : Rat\n⊢ v0 = v0",),
            diagnostics=() if status == "verified" else ("Synthetic incomplete proof",),
            binding_digest=polynomial_binding_digest(before, after, variables), source_digest="a" * 64,
            proof_digest="b" * 64 if status == "verified" else None,
            axioms=("propext", "Classical.choice", "Quot.sound"), lean_version="4.29.0",
            variable_mapping=tuple((name, f"v{index}") for index, name in enumerate(variables)),
        )

    monkeypatch.setattr(LeanPolynomialBackend, "check", check)
    return LeanPolynomialBackend(tmp_path / "unused-typed-lean-fixture"), calls, statuses


def test_cosmetic_sign_rewrite_preserves_state_and_lean_budget_then_feedback_repairs(lean):
    backend, lean_calls, _ = lean
    prompts = []
    expression = "(3*z+(-4))**2"

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"after":"(3*z-4)**2"}'
        assert prompt["state_revision"] == 0
        assert prompt.get("progress_feedback")
        assert "goal" in prompt and "residual" in prompt
        return '{"after":"9*z**2-24*z+16"}'

    outcome, stats, store = solve(expression, complete, lean_backend=backend,
                                  cost_aware_scheduling=True, max_local_work=0,
                                  max_model_calls=2, max_steps=2, learn=True)
    run = outcome.result.run_result
    assert run.status == "solved" and len(prompts) == stats.model_calls == 2
    first = run.trace[0]
    assert first.decision is Decision.DEFER and first.after == first.before
    assert first.after.facts == ()
    assert verify_identity(expression, json.loads(first.candidate.claim)["after"], ("z",)).status == "verified"
    assert stats.lean_checks == len(lean_calls) == 1
    assert stats.progress_no_progress == 1 and stats.progress_complete == 1
    assert stats.progress_detours == 0
    assert len(run.state.facts) == len(outcome.admissions[0].candidate.derivation) == 1
    assert len(store.lookup(DOMAIN)) == 1
    assert same(run.state.facts[0].value[1], "9*z**2-24*z+16")


def test_real_lean_only_checks_the_repaired_goal_directed_proposal(monkeypatch):
    backend = LeanPolynomialBackend()
    if backend.lean_path is None or not Path(backend.lean_path).is_file():
        pytest.skip("optional Lean 4.29.0 is unavailable")
    available = backend.check("0", "0", (), tactic="rfl")
    if available.status == "unavailable":
        pytest.skip("optional Lean 4.29.0 is unavailable")
    assert available.status == "verified", available
    original_check, actual_checks, prompts = backend.check, [], []

    def check(before, after, variables, *, tactic="grind"):
        actual_checks.append(tactic)
        return original_check(before, after, variables, tactic=tactic)

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"after":"(3*z-4)**2","tactic":"rfl"}'
        assert prompt["state_revision"] == 0 and prompt.get("progress_feedback")
        assert "proof_feedback" not in prompt
        return '{"after":"9*z**2-24*z+16","tactic":"grind"}'

    monkeypatch.setattr(backend, "check", check)
    outcome, stats, _ = solve("(3*z+(-4))**2", complete, lean_backend=backend,
                              cost_aware_scheduling=True, max_local_work=0,
                              max_model_calls=2, max_steps=2, learn=True)
    run = outcome.result.run_result
    assert run.status == "solved" and run.residual.solved
    assert run.trace[0].decision is Decision.DEFER and run.trace[0].after.facts == ()
    assert stats.progress_no_progress == 1
    assert actual_checks == ["grind"] and stats.lean_checks == stats.lean_verified == 1
    assert stats.model_calls == len(prompts) == 2
    assert len(run.state.facts) == len(outcome.admissions[0].candidate.derivation) == 1
    assert "lean_kernel_verified" in run.trace[1].reasons


@pytest.mark.parametrize("reply", ["0", "9*z**2-24*z+17", "z^2"])
def test_invalid_or_false_terminal_shape_cannot_skip_exact_verification(reply, lean):
    backend, lean_calls, _ = lean
    outcome, stats, store = solve("(3*z+(-4))**2", lambda _: json.dumps({"after": reply}),
                                  lean_backend=backend, max_model_calls=1, max_steps=1, learn=True)
    run = outcome.result.run_result
    assert run.status != "solved" and run.state.facts == ()
    assert stats.progress_checks == stats.lean_checks == len(lean_calls) == 0
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()
    assert run.trace[0].decision is Decision.REJECT


def test_unknown_exact_verifier_prevents_progress_credit_and_lean(monkeypatch, lean):
    backend, lean_calls, _ = lean

    def unknown(self, candidate, state, residual, evidence):
        return Verdict.for_candidate(candidate, state, Decision.DEFER, evidence=evidence,
                                      reasons=("synthetic_verification_unknown",))

    monkeypatch.setattr(module._StateBoundPolynomialVerifier, "verify", unknown)
    outcome, stats, store = solve("(z+2)*(z+7)", lambda _: '{"after":"z**2+9*z+14"}',
                                  lean_backend=backend, max_model_calls=1, max_steps=1, learn=True)
    assert outcome.result.run_result.state.facts == ()
    assert stats.progress_checks == stats.lean_checks == len(lean_calls) == 0
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_goal_complete_proposal_still_requires_lean_acceptance(lean):
    backend, lean_calls, statuses = lean
    statuses.append("unresolved")
    outcome, stats, store = solve("(z+2)*(z+7)", lambda _: '{"after":"z**2+9*z+14"}',
                                  lean_backend=backend, max_model_calls=1, max_steps=1, learn=True)
    assert outcome.result.run_result.status != "solved"
    assert outcome.result.run_result.state.facts == ()
    assert stats.lean_checks == len(lean_calls) == 1 and stats.lean_verified == 0
    assert stats.progress_detours == 0
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


@pytest.mark.parametrize("expression,after", [
    ("(z+2)**3", "(z+2)**2*(z+2)"),
    ("(z+2)*(z+7)", "z*(z+7)+2*(z+7)"),
    ("(z+2)**3+5*z", "(z+2)**2*(z+2)+5*z"),
])
def test_recognized_expansion_can_grow_before_finishing_without_detour_credit(expression, after, lean):
    backend, lean_calls, _ = lean
    assert sum(1 for _ in ast.walk(ast.parse(after))) > sum(1 for _ in ast.walk(ast.parse(expression)))
    outcome, stats, store = solve(expression, lambda _: json.dumps({"after": after}),
                                  lean_backend=backend, max_progress_detours=0,
                                  max_model_calls=1, max_steps=1, learn=True)
    run = outcome.result.run_result
    assert run.status != "solved" and run.trace[0].decision is Decision.ACCEPT
    assert len(run.state.facts) == stats.progress_structural == len(lean_calls) == 1
    assert stats.progress_detours == 0
    assert same(run.state.facts[0].value[1], after)
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_useful_primitive_sequence_finishes_when_detours_are_forbidden():
    outcome, stats, _ = solve("(z+2)**3", control_expression_growth=False,
                              max_progress_detours=0, max_model_calls=0)
    run = outcome.result.run_result
    assert run.status == "solved" and stats.model_calls == 0
    assert stats.primitive_accepts > 1 and stats.progress_detours == 0
    assert verify_identity("(z+2)**3", run.state.facts[-1].value[1], ("z",)).status == "verified"


def test_smaller_ast_alone_does_not_claim_goal_progress(lean):
    backend, lean_calls, _ = lean
    expression, after = "(z+1)*(z+5)+0", "(z+1)*(z+5)"
    assert sum(1 for _ in ast.walk(ast.parse(after))) < sum(1 for _ in ast.walk(ast.parse(expression)))
    outcome, stats, _ = solve(expression, lambda _: json.dumps({"after": after}),
                              lean_backend=backend, max_progress_detours=0,
                              max_model_calls=1, max_steps=1)
    assert outcome.result.run_result.trace[0].decision is Decision.DEFER
    assert outcome.result.run_result.state.facts == ()
    assert stats.lean_checks == len(lean_calls) == stats.progress_detours == 0


def test_reordering_can_use_a_detour_to_expose_a_verified_rule(lean):
    backend, lean_calls, _ = lean
    store = library()
    lhs, rhs = "(a+5)*(a+1)", "a*a+6*a+5"
    record = store.admit(KnowledgeCandidate(
        id="public-rule:ordered-product", domain=DOMAIN, kind="polynomial_identity",
        statement={"lhs": lhs, "rhs": rhs, "variables": ["a"]},
        derivation=({"lhs": lhs, "rhs": rhs},), source_task_id="public-ordered-discovery",
    ))
    assert record.status == "verified"
    outcome, stats, _ = solve("(z+1)*(z+5)", lambda _: '{"after":"(z+5)*(z+1)"}', store=store,
                              lean_backend=backend, max_progress_detours=1,
                              max_model_calls=1, max_steps=2)
    run = outcome.result.run_result
    assert run.status == "solved" and len(run.state.facts) == len(lean_calls) == 2
    assert stats.progress_detours == stats.rule_accepts == stats.model_calls == 1
    assert run.state.facts[0].value[2] == ""
    assert run.state.facts[1].value[2] == record.candidate.id


@pytest.mark.parametrize("allowance,accepted", [(0, False), (1, True)])
def test_uncertain_correct_intermediate_uses_explicit_detour_allowance(allowance, accepted, lean, monkeypatch):
    backend, lean_calls, _ = lean
    original_record, recorded = module.StrategyController.record, []

    def record(self, *args, **kwargs):
        event = original_record(self, *args, **kwargs)
        recorded.append(event)
        return event

    monkeypatch.setattr(module.StrategyController, "record", record)
    outcome, stats, store = solve("(z+1)*(z+5)", lambda _: '{"after":"(z+3)**2-4"}',
                                  lean_backend=backend, max_progress_detours=allowance,
                                  max_model_calls=1, max_steps=1, learn=True)
    run = outcome.result.run_result
    assert run.status != "solved"
    assert len(run.state.facts) == stats.progress_detours == len(lean_calls) == int(accepted)
    assert (run.trace[0].decision is Decision.ACCEPT) is accepted
    assert len(recorded) == 1 and recorded[0].accepted is accepted
    assert recorded[0].progress is False
    assert stats.progress_events[0]["classification"] == "uncertain"
    assert stats.progress_events[0]["goal_progress"] is False
    assert stats.progress_events[0]["actual_goal_complete"] is False
    assert stats.progress_events[0]["detour_charged"] is accepted
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_detour_allowance_does_not_reset_after_an_accepted_new_state(lean):
    backend, lean_calls, _ = lean
    prompts = []

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"after":"(z+3)**2-4"}'
        assert prompt["state_revision"] == 1
        feedback = prompt["progress_feedback"]
        assert feedback["exploratory_step_accepted"] is True
        assert feedback["actual_goal_complete"] is False
        assert feedback["detours_used_in_task"] == feedback["max_progress_detours"] == 1
        assert feedback["state_revision"] == prompt["state_revision"]
        assert feedback["state_fingerprint"] == prompt["state_fingerprint"]
        assert feedback["current_unexpanded_subexpressions"]["items"]
        selected = prompt["selected_subexpression"]
        if selected is not None:
            assert same(selected["expression"], "(z+3)**2")
        return json.dumps({"after": "(z+2)*(z+4)+1" if selected is not None else "(z+2)*(z+4)-3"})

    outcome, stats, _ = solve("(z+1)*(z+5)", complete, lean_backend=backend,
                              max_progress_detours=1, max_model_calls=2, max_steps=2)
    run = outcome.result.run_result
    assert len(prompts) == stats.model_calls == 2
    assert len(run.state.facts) == stats.progress_detours == len(lean_calls) == 1
    assert stats.progress_detour_rejections == 1
    assert run.trace[1].decision is Decision.DEFER and run.trace[1].before == run.trace[1].after


def test_proof_retry_only_charges_detour_when_the_intermediate_actually_commits(lean):
    backend, lean_calls, statuses = lean
    statuses.extend(("unresolved", "verified"))
    prompts = []

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"after":"(z+3)**2-4","tactic":"rfl"}'
        assert prompt["strategy"] == "proof_model" and prompt["state_revision"] == 0
        return json.dumps({"after": prompt["proof_feedback"]["proposed_after"], "tactic": "grind"})

    outcome, stats, _ = solve("(z+1)*(z+5)", complete, lean_backend=backend,
                              max_progress_detours=1, max_model_calls=2, max_steps=2)
    run = outcome.result.run_result
    assert run.trace[0].decision is Decision.DEFER and run.trace[0].after.facts == ()
    assert run.trace[1].decision is Decision.ACCEPT and len(run.state.facts) == 1
    assert stats.progress_detours == 1 and stats.progress_detour_rejections == 0
    assert stats.lean_checks == len(lean_calls) == stats.model_calls == len(prompts) == 2


def test_rollback_remains_checked_and_does_not_refund_detour_allowance(monkeypatch, lean):
    backend, lean_calls, _ = lean
    stats, prompts = AdaptiveStats(), []
    # Make local tools unavailable after the exploratory branch, so the real
    # bounded controller must exercise its existing explicit checkpoint return.
    monkeypatch.setattr(module._AdaptiveProposer, "_growth_candidates", lambda *args: (None, None))
    monkeypatch.setattr(module, "_primitive", lambda *args: None)

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        if len(prompts) == 1:
            return '{"after":"(z+3)**2-4"}'
        if len(prompts) == 2:
            return "null"
        assert prompt["state_revision"] == 2
        assert stats.progress_detours == 1
        return '{"after":"(z+2)*(z+4)-3"}'

    expression = "(z+1)*(z+5)"
    outcome, stats, _ = solve(expression, complete, stats=stats, lean_backend=backend,
                              max_progress_detours=1, max_rollbacks=1,
                              max_model_calls=3, max_steps=8)
    run = outcome.result.run_result
    assert stats.rollback_accepts == 1
    assert stats.progress_detours == stats.progress_detour_rejections == 1
    assert len(run.state.facts) == len(lean_calls) == 2
    assert same(run.state.facts[0].value[1], "(z+3)**2-4")
    assert run.state.facts[1].value[0] == run.state.facts[0].value[1]
    assert same(run.state.facts[1].value[1], expression)
    assert stats.model_calls == len(prompts) == 3 and stats.action_attempts <= 8
    assert run.status != "solved"


def test_disabling_goal_handling_preserves_equivalent_intermediate_acceptance(lean):
    backend, lean_calls, _ = lean
    outcome, stats, _ = solve("(3*z+(-4))**2", lambda _: '{"after":"(3*z-4)**2"}',
                              lean_backend=backend, goal_directed=False, max_progress_detours=0,
                              max_model_calls=1, max_steps=1)
    run = outcome.result.run_result
    assert run.trace[0].decision is Decision.ACCEPT and len(run.state.facts) == 1
    assert stats.lean_checks == len(lean_calls) == 1 and stats.progress_detours == 0
    assert stats.progress_checks == 0


def test_reused_learner_has_fresh_detours_and_cumulative_observations(lean):
    backend, lean_calls, _ = lean
    stats = AdaptiveStats()
    learner = build_adaptive_learning_agent(PolynomialProblem("(z+1)*(z+5)", ("z",)), library(),
        complete=lambda _: '{"after":"(z+3)**2-4"}', stats=stats, lean_backend=backend,
        cost_aware_scheduling=False, max_progress_detours=1, max_model_calls=1, max_steps=1)
    for name in ("public-detour-first", "public-detour-second"):
        outcome = learner.run(Task(name, "Expand", DOMAIN), learn=False)
        assert len(outcome.result.run_result.state.facts) == 1
        assert outcome.result.run_result.status != "solved"
    assert stats.progress_detours == stats.model_calls == len(lean_calls) == 2
    assert {entry["task_id"] for entry in stats.progress_events} == {"public-detour-first", "public-detour-second"}
    json.dumps(asdict(stats), allow_nan=False)


def test_progress_observations_are_bounded_and_cannot_authorize_a_cosmetic_step(lean):
    backend, lean_calls, _ = lean
    stats = AdaptiveStats()
    stats.progress_events = [{"classification": "complete", "accepted": True} for _ in range(512)]
    outcome, stats, store = solve("(3*z+(-4))**2", lambda _: '{"after":"(3*z-4)**2"}',
                                  stats=stats, lean_backend=backend,
                                  max_model_calls=1, max_steps=1, learn=True)
    assert outcome.result.run_result.state.facts == ()
    assert stats.lean_checks == len(lean_calls) == 0
    assert len(stats.progress_events) == 512 and stats.progress_events_omitted == 1
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_repeated_cosmetic_variants_do_not_spend_detours_or_proof_budget(lean):
    backend, lean_calls, _ = lean
    replies = iter(("(3*z-4)**2", "(-3*z+4)**2"))
    outcome, stats, _ = solve("(3*z+(-4))**2", lambda _: json.dumps({"after": next(replies)}),
                              lean_backend=backend, cost_aware_scheduling=True, max_local_work=0,
                              max_progress_detours=1, max_model_calls=2, max_steps=2)
    assert outcome.result.run_result.state.facts == ()
    assert stats.progress_no_progress == stats.model_calls == stats.action_attempts == 2
    assert stats.progress_detours == stats.lean_checks == len(lean_calls) == 0


@pytest.mark.parametrize("options", [
    {"goal_directed": 1}, {"goal_directed": "true"}, {"goal_directed": None},
    {"max_progress_detours": -1}, {"max_progress_detours": True},
    {"max_progress_detours": 1.5}, {"max_progress_detours": None}, {"max_progress_detours": 65},
])
def test_goal_options_reject_non_explicit_or_invalid_limits(options):
    with pytest.raises(ValueError):
        build_adaptive_learning_agent(PolynomialProblem("(z+1)*(z+5)", ("z",)), library(), **options)
