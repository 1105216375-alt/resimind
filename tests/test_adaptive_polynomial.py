"""Adaptive control changes strategy; only independently checked facts commit."""
import ast
from dataclasses import asdict
import json

import pytest

from resimind import Decision, Task, Verdict
from resimind.domains import adaptive_polynomial as module
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import DOMAIN, PolynomialProblem, WorkCounts
from resimind.knowledge import KnowledgeLibrary


def store():
    return KnowledgeLibrary({DOMAIN: AlgebraVerifier()})


def solve(expression, variables=("x",), *, library=None, stats=None, **kwargs):
    library = store() if library is None else library
    stats = AdaptiveStats() if stats is None else stats
    # These regressions pin the original primitive/rollback controller path.
    # Default growth control is exercised separately in test_adaptive_growth.py.
    kwargs.setdefault("control_expression_growth", False)
    result = build_adaptive_learning_agent(PolynomialProblem(expression, variables), library,
                                            stats=stats, **kwargs).run(Task("source", "Expand", DOMAIN))
    return result, stats, library


def same(left, right):
    return ast.dump(ast.parse(left, mode="eval")) == ast.dump(ast.parse(right, mode="eval"))


@pytest.mark.parametrize("expression,variables", [
    ("(x+1)*(x+2)*(x+3)", ("x",)),
    ("(x+1)*(x+2)*(x+3)*(x+4)", ("x",)),
    ("(x+y+z)*(x-y+z)*(2*x+y-z)", ("x", "y", "z")),
])
def test_symbolic_control_finishes_three_and_four_factors_without_model(expression, variables):
    counts = WorkCounts()
    result, stats, _ = solve(expression, variables, counts=counts)
    run = result.result.run_result
    assert run.status == "solved"
    assert stats.model_calls == counts.proposal_calls == 0
    assert stats.primitive_accepts > 0
    assert all(event.decision is Decision.ACCEPT for event in run.trace)
    assert stats.action_attempts == run.steps <= 64
    assert len(run.state.facts) == len(result.admissions[0].candidate.derivation)
    assert not result.learning_errors


def test_bad_whole_answer_switches_to_a_real_local_rewrite_with_siblings_preserved():
    prompts = []

    def complete(prompt):
        value = json.loads(prompt)
        prompts.append(value)
        if value["strategy"] == "local_model" and value["selected_subexpression"]["path"] == "$.left":
            assert same(value["selected_subexpression"]["expression"], "(x+1)*(x+2)")
            return json.dumps({"after": "x*x+3*x+2"})
        return '{"after":"0"}'

    result, stats, _ = solve("(x+1)*(x+2)*(x+3)", complete=complete)
    run = result.result.run_result
    assert run.status == "solved"
    assert [item["strategy"] for item in prompts[:2]] == ["whole_model", "local_model"]
    assert same(run.state.facts[0].value[1], "(x*x+3*x+2)*(x+3)")
    assert stats.local_model_accepts == 1
    assert stats.primitive_accepts > 0
    assert stats.strategy_switches >= 2
    assert stats.diagnostic_counts["identity_math"] >= 1
    assert all("state" not in prompt and "facts" not in prompt for prompt in prompts)
    assert stats.action_attempts == len([event for event in run.trace if event.step > 0])


def test_formatting_equivalent_repeats_are_suppressed_before_more_verifier_work():
    calls = 0

    def complete(prompt):
        nonlocal calls
        calls += 1
        return json.dumps({"after": "0" if calls == 1 else " ((0)) "})

    result, stats, _ = solve("(x+1)*(x+2)", complete=complete)
    assert result.result.run_result.status == "solved"
    assert stats.duplicate_suppressions >= 1
    suppressed = [event for event in stats.events if event.get("reasons") == ["duplicate_or_cycle_candidate"]]
    assert suppressed
    trace = result.result.run_result.trace[suppressed[0]["step"] - 1]
    assert trace.candidate is None and trace.verdict is None
    assert stats.model_calls == calls == 2


@pytest.mark.parametrize("reply,field", [
    ("null", "model_abstentions"),
    ("not json", "schema_failures"),
    ('{"after":"x + ("}', "schema_failures"),
    ('{"after":"999","path":"$.right"}', "schema_failures"),
    ('{"after":"0","after":"x"}', "schema_failures"),
])
def test_model_format_abstention_and_path_injection_fall_through_without_commit(reply, field):
    result, stats, _ = solve("(x+1)**2", complete=lambda _: reply, max_model_calls=1)
    assert result.result.run_result.status == "solved"
    assert getattr(stats, field) == 1
    assert stats.model_calls == 1
    assert stats.primitive_accepts > 0
    assert result.result.run_result.trace[0].after.facts == ()


def test_transport_failure_remains_visible_when_symbolic_fallback_succeeds():
    def complete(_):
        raise RuntimeError("PRIVATE_PROVIDER_SECRET_DO_NOT_RETAIN")

    result, stats, _ = solve("(x+1)**2", complete=complete, max_model_calls=1)
    assert result.result.run_result.status == "solved"
    assert stats.technical_failures == stats.model_failures == stats.model_calls == 1
    assert "PRIVATE_PROVIDER_SECRET" not in json.dumps(asdict(stats))
    assert stats.events[0]["reasons"] == ["model_transport_error", "RuntimeError"]


def test_distilled_proof_reloads_and_is_automatically_reused_on_new_task(tmp_path):
    original, _, library = solve("(u+v)**2", ("u", "v"), complete=lambda _: '{"after":"0"}',
                                  max_model_calls=1)
    assert original.admissions[0].status == "verified"
    path = tmp_path / "learned.json"
    library.save(path)
    reloaded = KnowledgeLibrary.load(path, {DOMAIN: AlgebraVerifier()})
    stats = AdaptiveStats()
    transferred = build_adaptive_learning_agent(
        PolynomialProblem("(2*x+3*y)**2", ("x", "y")), reloaded, stats=stats).run(
            Task("transfer", "Expand", DOMAIN), learn=False)
    assert transferred.result.run_result.status == "solved"
    assert stats.model_calls == 0
    assert stats.rule_attempts == stats.rule_accepts == 1
    assert stats.primitive_attempts == 0
    fact = transferred.result.run_result.state.facts[0]
    assert fact.value[2:] == (original.admissions[0].candidate.id, original.admissions[0].fingerprint)
    assert transferred.admissions == ()


def test_rule_revoked_during_model_call_is_not_automatically_executed():
    learned, _, library = solve("(u+v)**2", ("u", "v"))

    def complete(_):
        library.revoke(learned.admissions[0].candidate.id, "withdraw during task")
        return '{"after":"0"}'

    result, stats, _ = solve("(x+1)**2", library=library, complete=complete, max_model_calls=1)
    assert result.result.run_result.status == "solved"
    assert stats.rule_attempts == stats.rule_accepts == 0
    assert stats.primitive_accepts > 0
    assert all(not fact.value[2] for fact in result.result.run_result.state.facts)


def test_ancestor_replay_is_blocked_unless_controller_explicitly_rolls_back():
    calls = 0

    def complete(prompt):
        nonlocal calls
        calls += 1
        return json.dumps({"after": "(x+1)*(x+1)" if calls == 1 else "(x+1)**2"})

    result, stats, _ = solve("(x+1)**2", complete=complete, max_model_calls=2, max_rollbacks=0)
    assert result.result.run_result.status == "solved"
    assert stats.duplicate_suppressions >= 1
    assert stats.rollback_attempts == 0
    assert not any(same(fact.value[1], "(x+1)**2") for fact in result.result.run_result.state.facts)


def test_real_branch_failure_rolls_back_then_finishes_alternative_without_erasing_proof(monkeypatch):
    initial = "(x+1)**2*(x+2)**2"
    branch = "((x+1)*(x+1))*((x+2)**2)"
    calls = 0

    def complete(_):
        nonlocal calls
        calls += 1
        return json.dumps({"after": branch if calls == 1 else "0"})

    original_verify = module._AdaptiveVerifier.verify
    blocked_once = False

    def verify(self, candidate, state, residual, evidence):
        nonlocal blocked_once
        metadata = self.proposer.metadata.get(candidate.id, {})
        if (not blocked_once and metadata.get("strategy") == "primitive" and state.facts
                and same(state.facts[-1].value[1], branch)):
            blocked_once = True
            return Verdict.for_candidate(candidate, state, Decision.REJECT, evidence=evidence,
                                          reasons=("test_resource_limit",))
        return original_verify(self, candidate, state, residual, evidence)

    monkeypatch.setattr(module._AdaptiveVerifier, "verify", verify)
    result, stats, _ = solve(initial, complete=complete, max_model_calls=2, max_rollbacks=1)
    execution = result.result.run_result
    assert execution.status == "solved"
    assert blocked_once
    assert stats.rollback_attempts == stats.rollback_accepts == 1
    assert stats.model_calls == calls == 2
    rollback = next(event for event in stats.events if event.get("strategy") == "rollback")
    fact = execution.trace[rollback["step"] - 1].after.facts[-1]
    assert same(fact.value[0], branch) and fact.value[1] == initial
    assert execution.trace[rollback["step"] - 1].after.revision == execution.trace[rollback["step"] - 1].before.revision + 1
    assert len(execution.state.facts) == len(result.admissions[0].candidate.derivation)
    # Failed edge is remembered after rollback: a different factor is expanded.
    following = execution.state.facts[2]
    assert not same(following.value[1], branch)
    assert stats.duplicate_suppressions >= 1
    assert stats.action_attempts <= 64


def test_exhaustion_interrupts_instead_of_spinning_empty_steps_to_global_cap(monkeypatch):
    monkeypatch.setattr(module, "_primitive", lambda *_: None)
    result, stats, library = solve("(x+1)**2", max_model_calls=0)
    assert result.result.run_result.status != "solved"
    assert stats.stop_reason == "strategy_exhausted"
    assert stats.action_attempts == 2
    assert result.result.run_result.trace[-1].decision is Decision.INTERRUPT
    assert result.result.run_result.trace[-1].reasons == ("strategy_exhausted",)
    assert result.result.run_result.state.facts == ()
    assert library.lookup(DOMAIN) == ()


def test_unknown_verifier_never_yields_facts_or_knowledge_despite_all_fallbacks(monkeypatch):
    def unknown(self, candidate, state, residual, evidence):
        return Verdict.for_candidate(candidate, state, Decision.DEFER, evidence=evidence,
                                      reasons=("verification_unknown",))

    monkeypatch.setattr(module._StateBoundPolynomialVerifier, "verify", unknown)
    result, stats, library = solve("(x+1)**2", complete=lambda _: '{"after":"x*x+2*x+1"}')
    assert result.result.run_result.status != "solved"
    assert result.result.run_result.state.facts == ()
    assert not result.admissions and not library.lookup(DOMAIN)
    assert stats.primitive_attempts > 0


def test_same_learner_reuse_has_fresh_task_state_and_separate_budgets():
    stats = AdaptiveStats()
    learner = build_adaptive_learning_agent(PolynomialProblem("(x+1)**2", ("x",)), store(),
                                             complete=lambda _: '{"after":"0"}', stats=stats,
                                             max_model_calls=1)
    first = learner.run(Task("first", "Expand", DOMAIN), learn=False)
    second = learner.run(Task("second", "Expand", DOMAIN), learn=False)
    assert first.result.run_result.status == second.result.run_result.status == "solved"
    assert stats.model_calls == 2
    starts = [event for event in stats.events if event.get("step") == 1]
    assert [event["strategy"] for event in starts] == ["whole_model", "whole_model"]
    assert [event["model_calls_in_task"] for event in starts] == [1, 1]


def test_ast_path_cannot_escape_selected_expression():
    with pytest.raises(ValueError, match="AST path"):
        module._replace_path("(x+1)*(x+2)", ("__class__",), "0")
    assert same(module._replace_path("(x+1)*(x+2)", ("left",), "x+1"), "(x+1)*(x+2)")


@pytest.mark.parametrize("option", [
    {"max_model_calls": -1}, {"max_model_calls": True}, {"max_steps": 0}, {"max_steps": 65},
    {"max_rollbacks": -1}, {"max_no_progress": 0}, {"complete": "not callable"},
])
def test_invalid_budgets_and_callbacks_fail_before_task(option):
    with pytest.raises(ValueError):
        build_adaptive_learning_agent(PolynomialProblem("x", ("x",)), store(), **option)
