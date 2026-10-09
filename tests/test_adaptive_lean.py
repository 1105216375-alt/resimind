"""Lean goals participate in reasoning; failed proof attempts never become facts."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from resimind import Decision, Task
from resimind.domains import adaptive_polynomial as module
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import DOMAIN, RULE_ACTION, PolynomialProblem, WorkCounts
from resimind.integrations.lean import LeanPolynomialBackend, LeanProofResult, polynomial_binding_digest
from resimind.knowledge import KnowledgeLibrary


EXPRESSION = "(x+1)*(x+2)"
EXPANDED = "x*x+3*x+2"


def store():
    return KnowledgeLibrary({DOMAIN: AlgebraVerifier()})


def solve(backend, *, library=None, complete=None, stats=None, **options):
    library = store() if library is None else library
    stats = AdaptiveStats() if stats is None else stats
    result = build_adaptive_learning_agent(
        PolynomialProblem(EXPRESSION, ("x",)), library, lean_backend=backend,
        complete=complete, stats=stats, **options).run(Task("lean-loop", "Expand", DOMAIN))
    return result, stats, library


def proof(before, after, variables, *, tactic="grind", status="verified"):
    """A typed integration fixture; actual kernel checks are tested separately."""
    return LeanProofResult(
        status=status, tactic=tactic, target="v0 = v0", actual_target="v0 : Rat\n⊢ v0 = v0",
        goals=() if status == "verified" else ("v0 : Rat\n⊢ v0 = v0",),
        diagnostics=() if status == "verified" else ("Tactic did not close the goal",),
        binding_digest=polynomial_binding_digest(before, after, variables), source_digest="a" * 64,
        proof_digest="b" * 64 if status == "verified" else None,
        axioms=("propext", "Classical.choice", "Quot.sound"), lean_version="4.29.0",
        variable_mapping=tuple((name, f"v{index}") for index, name in enumerate(variables)),
    )


@pytest.fixture(scope="module")
def real_lean():
    backend = LeanPolynomialBackend()
    if backend.lean_path is None or not Path(backend.lean_path).is_file():
        pytest.skip("optional Lean 4.29.0 is unavailable")
    available = backend.check("0", "0", (), tactic="rfl")
    if available.status == "unavailable":
        pytest.skip("optional Lean 4.29.0 is unavailable")
    assert available.status == "verified", available
    return backend


def test_real_goal_feedback_changes_model_tactic_before_commit_and_learning(real_lean):
    library, prompts = store(), []

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        assert library.lookup(DOMAIN) == ()
        assert prompt["state_revision"] == 0
        if len(prompts) == 1:
            assert "proof_feedback" not in prompt
            return json.dumps({"after": EXPANDED, "tactic": "rfl"})
        feedback = prompt["proof_feedback"]
        assert prompt["strategy"] == "proof_model"
        assert feedback["status"] == "unresolved" and feedback["tactic"] == "rfl"
        assert "⊢" in feedback["actual_target"]
        assert feedback["goals"] and feedback["diagnostics"]
        assert feedback["proposed_after"] == EXPANDED
        assert feedback["binding_digest"] == polynomial_binding_digest(EXPRESSION, EXPANDED, ("x",))
        return json.dumps({"after": feedback["proposed_after"], "tactic": "grind"})

    result, stats, _ = solve(real_lean, library=library, complete=complete, max_model_calls=2)
    run = result.result.run_result
    assert run.status == "solved"
    assert len(prompts) == stats.model_calls == 2
    assert stats.lean_checks == 2 and stats.lean_unresolved == stats.lean_verified == 1
    assert stats.proof_model_accepts == stats.lean_feedback_prompts == 1
    assert run.trace[0].decision is Decision.DEFER
    assert run.trace[0].before == run.trace[0].after
    assert run.trace[0].after.facts == ()
    assert len(run.state.facts) == 1
    assert "lean_kernel_verified" in run.trace[1].reasons
    assert result.admissions[0].status == "verified"
    assert len(result.admissions[0].candidate.derivation) == 1
    assert len(library.lookup(DOMAIN)) == 1


def test_real_host_retry_changes_tactic_without_an_extra_model_request(real_lean):
    calls = []

    def complete(raw):
        calls.append(raw)
        return json.dumps({"after": EXPANDED, "tactic": "rfl"})

    result, stats, _ = solve(real_lean, complete=complete, max_model_calls=1)
    assert result.result.run_result.status == "solved"
    assert len(calls) == stats.model_calls == 1
    assert stats.lean_retry_attempts == stats.lean_retry_accepts == 1
    assert stats.proof_model_attempts == 0
    assert stats.lean_checks == 2 and stats.lean_verified == stats.lean_unresolved == 1
    assert result.result.run_result.trace[0].after.facts == ()
    assert len(result.admissions) == 1


def test_missing_lean_cannot_commit_via_exact_only_fallback_or_learn(tmp_path):
    backend = LeanPolynomialBackend(tmp_path / "missing-lean")
    result, stats, library = solve(backend, complete=lambda _: json.dumps({"after": EXPANDED}))
    assert result.result.run_result.status != "solved"
    assert stats.stop_reason == "lean_unavailable"
    assert stats.lean_checks == 1 and stats.lean_verified == 0
    assert result.result.run_result.state.facts == ()
    assert result.admissions == () and library.lookup(DOMAIN) == ()


def test_one_check_budget_does_not_allow_retry_or_symbolic_bypass(monkeypatch):
    backend, calls = LeanPolynomialBackend(), []

    def check(before, after, variables, *, tactic):
        calls.append(tactic)
        return proof(before, after, variables, tactic=tactic, status="unresolved")

    monkeypatch.setattr(backend, "check", check)
    result, stats, library = solve(backend, complete=lambda _: json.dumps({"after": EXPANDED, "tactic": "rfl"}),
                                   max_lean_checks=1)
    assert calls == ["rfl"]
    assert stats.lean_budget_exhausted and stats.stop_reason == "lean_check_budget_exhausted"
    assert stats.lean_checks == 1 and stats.lean_retry_attempts == 0
    assert result.result.run_result.state.facts == ()
    assert not result.admissions and not library.lookup(DOMAIN)


def test_false_identity_is_rejected_before_starting_lean(monkeypatch):
    backend = LeanPolynomialBackend()
    monkeypatch.setattr(backend, "check", lambda *args, **kwargs: pytest.fail("false identity reached Lean"))
    result, stats, library = solve(backend, complete=lambda _: '{"after":"0","tactic":"grind"}', max_steps=1)
    assert stats.lean_checks == 0
    assert result.result.run_result.trace[0].decision is Decision.REJECT
    assert "polynomial_identity_not_proved" in result.result.run_result.trace[0].reasons
    assert not result.result.run_result.state.facts and not result.admissions and not library.lookup(DOMAIN)


@pytest.mark.parametrize("change", [
    {"binding_digest": "wrong-binding"}, {"tactic": "rfl"}, {"status": "rejected"},
    {"proof_digest": None}, {"proof_digest": "not-a-hash"}, {"proof_digest": "z" * 64},
    {"source_digest": ""}, {"source_digest": "z" * 64}, {"actual_target": ""},
    {"goals": ("⊢ unresolved",)}, {"axioms": ("sorryAx",)}, {"axioms": ("user.axiom",)},
    {"lean_version": "4.28.0"}, {"diagnostics": ("declaration uses sorry",)},
    {"actual_target": None}, {"goals": None}, {"axioms": None}, {"diagnostics": None},
])
def test_wrong_binding_or_invalid_proof_certificate_never_commits(monkeypatch, change):
    backend = LeanPolynomialBackend()
    monkeypatch.setattr(backend, "check", lambda before, after, variables, **kwargs:
                        replace(proof(before, after, variables, **kwargs), **change))
    result, stats, library = solve(backend, complete=lambda _: json.dumps({"after": EXPANDED}), max_steps=1)
    assert result.result.run_result.status != "solved"
    assert result.result.run_result.state.facts == () and stats.lean_verified == 0
    assert result.admissions == () and library.lookup(DOMAIN) == ()


def test_untyped_backend_result_and_backend_exception_fail_closed(monkeypatch):
    backend = LeanPolynomialBackend()
    monkeypatch.setattr(backend, "check", lambda *args, **kwargs: {"status": "verified"})
    result, stats, library = solve(backend, complete=lambda _: json.dumps({"after": EXPANDED}), max_steps=1)
    assert not result.result.run_result.state.facts and not result.admissions and stats.lean_errors == 1

    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_BACKEND_SECRET")

    monkeypatch.setattr(backend, "check", broken)
    result, stats, library = solve(backend, complete=lambda _: json.dumps({"after": EXPANDED}), max_steps=1)
    assert not result.result.run_result.state.facts and not result.admissions and stats.lean_errors == 1
    assert "PRIVATE_BACKEND_SECRET" not in json.dumps(stats.events)


def test_rule_revoked_before_a_lean_retry_is_checked_again(monkeypatch):
    library = store()
    learned = build_adaptive_learning_agent(PolynomialProblem(EXPRESSION, ("x",)), library).run(
        Task("seed", "Expand", DOMAIN))
    rule_id = learned.admissions[0].candidate.id
    backend = LeanPolynomialBackend()
    original = module._AdaptiveProposer.propose

    def first_rule_with_rfl(self, state, residual):
        candidates = original(self, state, residual)
        if self.attempts == 1 and candidates and candidates[0].action == RULE_ACTION:
            self.metadata[candidates[0].id]["lean_tactic"] = "rfl"
            self.pending["lean_tactic"] = "rfl"
        return candidates

    monkeypatch.setattr(module._AdaptiveProposer, "propose", first_rule_with_rfl)
    checks = []

    def check(before, after, variables, *, tactic):
        checks.append(tactic)
        assert tactic == "rfl", "revoked rule reached the second Lean call"
        library.revoke(rule_id, "withdraw before retry")
        return proof(before, after, variables, tactic=tactic, status="unresolved")

    monkeypatch.setattr(backend, "check", check)
    result, stats, _ = solve(backend, library=library, max_steps=2)
    assert checks == ["rfl"]
    assert stats.lean_retry_attempts == 1 and stats.lean_retry_accepts == 0
    assert result.result.run_result.state.facts == () and result.admissions == ()
    assert "rule_unavailable_or_changed" in result.result.run_result.trace[-1].reasons


def test_rule_revoked_during_successful_lean_check_cannot_commit(monkeypatch):
    library = store()
    learned = build_adaptive_learning_agent(PolynomialProblem(EXPRESSION, ("x",)), library).run(
        Task("seed-during", "Expand", DOMAIN))
    rule_id = learned.admissions[0].candidate.id
    backend = LeanPolynomialBackend()

    def check(before, after, variables, *, tactic):
        library.revoke(rule_id, "withdraw while Lean is running")
        return proof(before, after, variables, tactic=tactic)

    monkeypatch.setattr(backend, "check", check)
    result, stats, _ = solve(backend, library=library, max_steps=1)
    assert result.result.run_result.status != "solved"
    assert result.result.run_result.state.facts == () and result.admissions == ()
    assert "rule_unavailable_or_changed" in result.result.run_result.trace[0].reasons


def test_new_run_resets_lean_budget_but_preserves_cumulative_metrics(monkeypatch):
    backend = LeanPolynomialBackend()
    monkeypatch.setattr(backend, "check", lambda before, after, variables, **kwargs:
                        proof(before, after, variables, **kwargs, status="unresolved"))
    library, stats, counts = store(), AdaptiveStats(), WorkCounts()
    learner = build_adaptive_learning_agent(PolynomialProblem(EXPRESSION, ("x",)), library,
        lean_backend=backend, max_lean_checks=1, max_model_calls=1, stats=stats, counts=counts,
        complete=lambda _: json.dumps({"after": EXPANDED, "tactic": "rfl"}))
    for task_id in ("first", "second"):
        result = learner.run(Task(task_id, "Expand", DOMAIN))
        assert result.result.run_result.state.facts == () and result.admissions == ()
    assert stats.lean_checks == stats.model_calls == counts.proposal_calls == 2
    assert library.lookup(DOMAIN) == ()


@pytest.mark.parametrize("options", [{"lean_backend": object()}, {"max_lean_checks": 0},
                                    {"max_lean_checks": 65}, {"max_lean_checks": True}])
def test_invalid_lean_configuration_fails_before_running(options):
    with pytest.raises(ValueError):
        build_adaptive_learning_agent(PolynomialProblem(EXPRESSION, ("x",)), store(), **options)
