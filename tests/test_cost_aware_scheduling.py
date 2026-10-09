"""Public synthetic scheduling regressions; estimates never authorize facts."""
import ast
from dataclasses import asdict
import json

import pytest

from resimind import Decision, Task, Verdict
from resimind.domains import adaptive_polynomial as module
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.domains.algebra import AlgebraVerifier, verify_identity
from resimind.domains.polynomial_learning import (
    DOMAIN, RULE_ACTION, PolynomialProblem, WorkCounts, is_expanded,
)
from resimind.integrations.lean import LeanPolynomialBackend
from resimind.knowledge import KnowledgeCandidate, KnowledgeLibrary
from resimind.strategy import StrategyController


QUARTIC = "a**4+4*a**3*b+6*a**2*b**2+4*a*b**3+b**4"
SQUARE = "a**2+2*a*b+b**2"


def library():
    return KnowledgeLibrary({DOMAIN: AlgebraVerifier()})


def admit(store, name, lhs, rhs):
    """Construct independently checked identities, without training the scheduler."""
    record = store.admit(KnowledgeCandidate(
        id=name, domain=DOMAIN, kind="polynomial_identity",
        statement={"lhs": lhs, "rhs": rhs, "variables": ["a", "b"]},
        derivation=({"lhs": lhs, "rhs": rhs},), source_task_id="public-synthetic-rule",
        evidence_refs=("public-exact-derivation",),
    ))
    assert record.status == "verified"
    return record


def solve(expression, *, store=None, variables=("z",), learn=False, stats=None,
          task_id="public-cost-regression", **options):
    store = library() if store is None else store
    stats = AdaptiveStats() if stats is None else stats
    counts = WorkCounts()
    problem = PolynomialProblem(expression, variables)
    outcome = build_adaptive_learning_agent(problem, store, stats=stats, counts=counts,
                                            **options).run(Task(task_id, "Expand", DOMAIN), learn=learn)
    return outcome, stats, counts, store


def assert_checked(expression, variables, outcome):
    run = outcome.result.run_result
    previous = expression
    for fact in run.state.facts:
        assert fact.value[0] == previous
        assert verify_identity(previous, fact.value[1], variables).status == "verified"
        previous = fact.value[1]
    if run.status == "solved":
        assert run.residual.solved and run.state.facts and is_expanded(previous)
    assert len(run.state.facts) == sum(event.decision is Decision.ACCEPT for event in run.trace)


def no_model(_):
    pytest.fail("A sufficient inexpensive candidate must not spend a model call")


@pytest.mark.parametrize("expression", ["(z-4)*(z+6)", "(3*z+8)**2", "(z+9)*(z-2)*(z+4)"])
def test_available_symbols_finish_with_configured_model_but_zero_calls(expression):
    outcome, stats, counts, _ = solve(expression, complete=no_model)
    assert outcome.result.run_result.status == "solved"
    assert stats.model_calls == counts.proposal_calls == 0
    assert stats.scheduling_decisions
    assert_checked(expression, ("z",), outcome)


def test_verified_terminal_rule_beats_smaller_intermediates_without_model():
    expression = "(5*z+7)**4"
    cold, cold_stats, _, _ = solve(expression)
    store = library()
    record = admit(store, "rule:terminal-quartic", "(a+b)**4", QUARTIC)
    warm, stats, counts, _ = solve(expression, store=store, complete=no_model)
    assert warm.result.run_result.status == cold.result.run_result.status == "solved"
    assert stats.action_attempts == stats.rule_accepts == 1
    assert stats.model_calls == counts.proposal_calls == 0
    assert stats.action_attempts < cold_stats.action_attempts
    # Finishing is better here even though this valid final form is larger.
    assert stats.peak_expression_nodes > cold_stats.peak_expression_nodes
    assert warm.result.run_result.state.facts[0].value[2:] == (record.candidate.id, record.fingerprint)
    assert_checked(expression, ("z",), warm)


def test_compacting_nested_sums_avoids_rule_first_regression():
    expression = "((r+2)+(3*r+5))**2"
    cold, cold_stats, _, _ = solve(expression, variables=("r",))
    store = library()
    admit(store, "rule:nested-square", "(a+b)**2", "a*a+2*a*b+b*b")
    warm, stats, _, _ = solve(expression, variables=("r",), store=store, complete=no_model)
    assert warm.result.run_result.status == cold.result.run_result.status == "solved"
    assert stats.action_attempts <= cold_stats.action_attempts
    assert stats.peak_expression_nodes <= cold_stats.peak_expression_nodes
    assert warm.result.run_result.state.facts[0].value[2] == ""
    # A bounded distributive batch can also collect the inner sums and finish
    # directly. The requirement is avoiding the expensive recalled expansion.
    assert stats.scheduling_decisions[0]["selected_strategy"] in ("simplify", "distribute")
    assert_checked(expression, ("r",), warm)


def test_comparison_considers_later_better_matching_rule():
    store = library()
    bloated = admit(store, "rule:first-bloated", "(a+b)**4",
                    "a*a*a*a+4*a*a*a*b+6*a*a*b*b+4*a*b*b*b+b*b*b*b")
    compact = admit(store, "rule:second-compact", "(a+b)**4", QUARTIC)
    assert [record.candidate.id for record in store.lookup(DOMAIN)] == [
        bloated.candidate.id, compact.candidate.id]
    outcome, stats, _, _ = solve("(5*z+7)**4", store=store, complete=no_model)
    assert outcome.result.run_result.status == "solved" and stats.rule_accepts == 1
    assert outcome.result.run_result.state.facts[0].value[2] == compact.candidate.id
    decision = stats.scheduling_decisions[0]
    assert decision["selected_rule_id"] == compact.candidate.id
    assert {item["rule_id"] for item in decision["candidates"] if item["strategy"] == "verified_rule"} == {
        bloated.candidate.id, compact.candidate.id}


def test_overflow_charge_binds_to_selected_rule_instead_of_first_matching_rule():
    store = library()
    first = admit(store, "rule:first-bloated", "(a+b)**4",
                  "a*a*a*a+4*a*a*a*b+6*a*a*b*b+4*a*b*b*b+b*b*b*b")
    selected = admit(store, "rule:second-compact", "(a+b)**4", QUARTIC)
    outcome, stats, _, _ = solve("(5*z+7)**4", store=store, complete=no_model,
                                  max_local_work=0, max_local_work_overflow=1000)
    assert outcome.result.run_result.status == "solved"
    assert outcome.result.run_result.state.facts[0].value[2] == selected.candidate.id
    estimates = {entry["rule_id"]: entry["estimated_local_work"]
                 for entry in stats.scheduling_decisions[0]["candidates"] if entry["rule_id"]}
    assert estimates[first.candidate.id] > estimates[selected.candidate.id]
    assert stats.local_work_overflow_spent == estimates[selected.candidate.id]
    grants = [event for event in stats.events if event.get("event") == "local_work_overflow_granted"]
    assert len(grants) == 1 and grants[0]["rule_id"] == selected.candidate.id
    assert grants[0]["overflow_cost"] == estimates[selected.candidate.id]


def test_overflow_denials_are_deduplicated_across_repeated_previews(monkeypatch):
    original = module._AdaptiveProposer._available_cost_aware
    observed = []

    def twice(self, *args):
        first = original(self, *args)
        denials = self.stats.local_work_overflow_denials
        original(self, *args)
        assert self.stats.local_work_overflow_denials == denials
        observed.append(denials)
        return first

    monkeypatch.setattr(module._AdaptiveProposer, "_available_cost_aware", twice)
    outcome, stats, _, _ = solve("(3*z+8)**4", max_local_work=0,
                                  max_local_work_overflow=1)
    assert observed and observed[0] > 0
    assert outcome.result.run_result.state.facts == ()
    assert stats.local_work_overflow_spent == stats.local_work_overflow_grants == 0


def test_overflow_cannot_admit_a_false_local_candidate(monkeypatch):
    monkeypatch.setattr(module, "bounded_distribute", lambda *args: "0")
    outcome, stats, _, store = solve("(z-4)*(z+6)", max_local_work=0,
                                      max_local_work_overflow=1, learn=True)
    assert outcome.result.run_result.trace[0].decision is Decision.REJECT
    assert outcome.result.run_result.state.facts == ()
    assert stats.local_work_overflow_spent == stats.local_work_overflow_grants == 0
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_one_rejected_rule_does_not_disable_another_rule(monkeypatch):
    store = library()
    first = admit(store, "rule:first-short", "(a+b)**4", QUARTIC)
    second = admit(store, "rule:second-long", "(a+b)**4",
                   "a*a*a*a+4*a*a*a*b+6*a*a*b*b+4*a*b*b*b+b*b*b*b")
    original = module._AdaptiveVerifier.verify
    rejected = []

    def reject_one(self, candidate, state, residual, evidence):
        if candidate.action == RULE_ACTION and json.loads(candidate.claim)["rule_id"] == first.candidate.id:
            rejected.append(candidate.id)
            return Verdict.for_candidate(candidate, state, Decision.REJECT, evidence=evidence,
                                          reasons=("synthetic_rule_resource_limit",))
        return original(self, candidate, state, residual, evidence)

    monkeypatch.setattr(module._AdaptiveVerifier, "verify", reject_one)
    outcome, stats, _, _ = solve("(5*z+7)**4", store=store, complete=no_model)
    assert len(rejected) == 1
    assert outcome.result.run_result.status == "solved"
    assert stats.rule_attempts == 2 and stats.rule_accepts == 1
    assert outcome.result.run_result.state.facts[0].value[2] == second.candidate.id
    assert [entry["accepted"] for entry in stats.rule_usage] == [False, True]


def test_rule_revoked_after_preview_is_rechecked_before_commit(monkeypatch):
    store = library()
    rule = admit(store, "rule:revoke-before-commit", "(a+b)**4", QUARTIC)
    original = module._AdaptiveVerifier.verify
    revoked = []

    def revoke_before_verification(self, candidate, state, residual, evidence):
        if candidate.action == RULE_ACTION and not revoked:
            revoked.append(store.revoke(rule.candidate.id, "synthetic mid-action revocation"))
        return original(self, candidate, state, residual, evidence)

    monkeypatch.setattr(module._AdaptiveVerifier, "verify", revoke_before_verification)
    outcome, stats, _, _ = solve("(5*z+7)**4", store=store, complete=no_model)
    run = outcome.result.run_result
    assert revoked and run.status == "solved"
    assert stats.rule_attempts == 1 and stats.rule_accepts == 0
    assert all(fact.value[2] == "" for fact in run.state.facts)
    assert run.trace[0].decision is Decision.REJECT and run.trace[0].after.facts == ()
    assert "rule_unavailable_or_changed" in run.trace[0].reasons
    assert stats.rule_usage[0]["accepted"] is False
    assert_checked("(5*z+7)**4", ("z",), outcome)


@pytest.mark.parametrize("helper", ["compact_expression", "bounded_distribute"])
def test_terminal_looking_false_candidate_cannot_skip_exact_gate(monkeypatch, helper):
    monkeypatch.setattr(module, helper, lambda *args, **kwargs: "0")
    outcome, stats, _, store = solve("(z-4)*(z+6)", max_steps=1, learn=True)
    run = outcome.result.run_result
    assert run.status != "solved" and run.state.facts == ()
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()
    assert run.trace[0].decision is Decision.REJECT
    assert "polynomial_identity_not_proved" in run.trace[0].reasons
    assert stats.action_attempts == 1


def test_unknown_verifier_cannot_be_overridden_by_good_cost_or_terminal_shape(monkeypatch):
    def unknown(self, candidate, state, residual, evidence):
        return Verdict.for_candidate(candidate, state, Decision.DEFER, evidence=evidence,
                                      reasons=("synthetic_verification_unknown",))

    monkeypatch.setattr(module._StateBoundPolynomialVerifier, "verify", unknown)
    outcome, _, _, store = solve("(3*z+8)**2", max_steps=4, learn=True)
    assert outcome.result.run_result.status != "solved"
    assert outcome.result.run_result.state.facts == ()
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_exhausted_cheap_strategy_cannot_hide_available_model_fallback(monkeypatch):
    original = module._AdaptiveVerifier.verify
    rejected, prompts = [], []

    def transient_local_limit(self, candidate, state, residual, evidence):
        strategy = self.proposer.metadata.get(candidate.id, {}).get("strategy")
        if strategy == "primitive" and len(rejected) < 2:
            rejected.append(candidate.id)
            return Verdict.for_candidate(candidate, state, Decision.DEFER, evidence=evidence,
                                          reasons=("synthetic_resource_limit",))
        return original(self, candidate, state, residual, evidence)

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        selected = prompt["selected_subexpression"]
        if selected is not None:
            assert ast.dump(ast.parse(selected["expression"], mode="eval")) == ast.dump(
                ast.parse("z*(z+2)", mode="eval"))
        return json.dumps({"after": "z*z+2*z" if selected is not None else "3*z*z+12*z"})

    monkeypatch.setattr(module._AdaptiveVerifier, "verify", transient_local_limit)
    expression = "z*(z+2)+z*(z+4)+z*(z+6)"
    outcome, stats, _, _ = solve(expression, complete=complete, control_expression_growth=False,
                                 max_model_calls=1, max_steps=8)
    assert len(rejected) == 2 and len(prompts) == stats.model_calls == 1
    assert outcome.result.run_result.status == "solved"
    assert stats.action_attempts <= 8
    fallback = stats.scheduling_decisions[2]
    assert fallback["model_enabled"] is True
    assert fallback["selected_strategy"] in ("local_model", "whole_model")
    assert_checked(expression, ("z",), outcome)


def test_terminal_candidate_still_requires_configured_lean_gate(tmp_path):
    # A missing optional compiler is deterministic and makes no external call.
    backend = LeanPolynomialBackend(tmp_path / "absent-lean")
    outcome, stats, _, store = solve("(z-4)*(z+6)", lean_backend=backend,
                                    max_steps=3, learn=True)
    run = outcome.result.run_result
    assert run.status != "solved" and run.state.facts == ()
    assert stats.lean_checks == 1 and stats.lean_verified == 0
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()
    assert any("lean_proof_unavailable" in event.reasons for event in run.trace)


def test_zero_local_budget_allows_one_verified_model_fallback():
    prompts = []

    def complete(raw):
        prompts.append(json.loads(raw))
        return json.dumps({"after": "z*z+2*z-24"})

    outcome, stats, counts, _ = solve("(z-4)*(z+6)", complete=complete,
                                     max_local_work=0, max_model_calls=1)
    assert outcome.result.run_result.status == "solved"
    assert len(prompts) == stats.model_calls == counts.proposal_calls == 1
    assert prompts[0]["strategy"] == "whole_model"
    assert stats.rule_accepts == stats.simplify_accepts == stats.distribute_accepts == 0
    assert_checked("(z-4)*(z+6)", ("z",), outcome)


@pytest.mark.parametrize("reply", ['{"after":"0"}', "null", "malformed"])
def test_model_fallback_failures_do_not_reset_model_or_action_budgets(reply):
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return reply

    outcome, stats, _, store = solve("(z-4)*(z+6)", complete=complete, max_local_work=0,
                                    max_model_calls=1, max_steps=5, learn=True)
    run = outcome.result.run_result
    assert run.status != "solved" and run.state.facts == ()
    assert len(calls) == stats.model_calls == 1
    assert stats.action_attempts == run.steps <= 5
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_exhausted_local_and_model_budgets_stop_without_fabricated_success():
    outcome, stats, counts, store = solve("(z-4)*(z+6)", max_local_work=0,
                                         max_model_calls=0, max_steps=3, learn=True)
    run = outcome.result.run_result
    assert run.status != "solved" and run.state.facts == ()
    assert stats.action_attempts == run.steps <= 3
    assert stats.model_calls == counts.proposal_calls == 0
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_local_work_overflow_is_bounded_and_can_cover_multiple_steps():
    expression = "(x+y)**8*(2*x-3*y+1)**4"
    cold, cold_stats, _, _ = solve(expression, variables=("x", "y"),
                                   max_local_work=4096, max_local_work_overflow=0)
    assert cold.result.run_result.status != "solved"
    assert cold_stats.local_work_overflow_grants == 0

    outcome, stats, _, _ = solve(expression, variables=("x", "y"),
                                  max_local_work=4096, max_local_work_overflow=4096)
    assert outcome.result.run_result.status == "solved"
    assert stats.model_calls == 0
    assert stats.local_work_overflow_grants >= 2
    grants = [event for event in stats.events if event.get("event") == "local_work_overflow_granted"]
    assert grants and sum(event["overflow_cost"] for event in grants) <= 4096
    assert grants[-1]["overflow_spent"] <= 4096
    assert_checked(expression, ("x", "y"), outcome)


def test_reusing_learner_refreshes_task_budget_without_resetting_audit_totals():
    stats = AdaptiveStats()
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return '{"after":"z*z+2*z-24"}'

    learner = build_adaptive_learning_agent(PolynomialProblem("(z-4)*(z+6)", ("z",)), library(),
                                             complete=complete, stats=stats,
                                             max_local_work=0, max_model_calls=1, max_steps=2)
    for name in ("public-reuse-first", "public-reuse-second"):
        outcome = learner.run(Task(name, "Expand", DOMAIN), learn=False)
        assert outcome.result.run_result.status == "solved"
        assert outcome.result.run_result.steps == 1
    assert stats.model_calls == len(calls) == 2
    assert {entry["task_id"] for entry in stats.scheduling_decisions} == {
        "public-reuse-first", "public-reuse-second"}


def test_explicit_legacy_scheduling_preserves_model_first_behavior():
    calls = []

    def complete(prompt):
        calls.append(json.loads(prompt))
        return '{"after":"z*z+2*z-24"}'

    outcome, stats, _, _ = solve("(z-4)*(z+6)", complete=complete, cost_aware_scheduling=False)
    assert outcome.result.run_result.status == "solved"
    assert stats.model_calls == len(calls) == 1
    assert calls[0]["strategy"] == "whole_model"


def test_rule_preview_cap_bounds_comparison_work():
    store = library()
    for index in range(5):
        admit(store, f"rule:bounded-{index}", "(a+b)**4", QUARTIC + "+0" * index)
    outcome, stats, _, _ = solve("(5*z+7)**4", store=store, max_rule_previews=2, max_steps=1)
    assert outcome.result.run_result.status == "solved"
    assert stats.rule_previews <= 2
    rules = [entry for entry in stats.scheduling_decisions[0]["candidates"]
             if entry["strategy"] == "verified_rule"]
    assert 1 <= len(rules) <= 2
    assert stats.scheduling_preview_nodes >= 0
    assert stats.scheduling_preview_elapsed_seconds >= 0


def test_rule_audit_describes_observation_and_does_not_become_knowledge(tmp_path):
    store = library()
    record = admit(store, "rule:audited-quartic", "(a+b)**4", QUARTIC)
    before_path, after_path = tmp_path / "before.json", tmp_path / "after.json"
    store.save(before_path)
    outcome, stats, _, _ = solve("(5*z+7)**4", store=store, task_id="public-audit", complete=no_model)
    store.save(after_path)
    assert before_path.read_bytes() == after_path.read_bytes()
    assert outcome.admissions == ()
    assert len(stats.rule_usage) == 1
    observation = stats.rule_usage[0]
    fact = outcome.result.run_result.state.facts[0]
    assert observation["task_id"] == "public-audit"
    assert observation["rule_id"] == record.candidate.id == fact.value[2]
    assert observation["rule_fingerprint"] == record.fingerprint == fact.value[3]
    assert observation["accepted"] and observation["progress"]
    assert observation["after"]["expanded"] is True
    assert observation["before"]["ast_nodes"] == sum(1 for _ in ast.walk(ast.parse(fact.value[0], mode="eval").body))
    assert observation["after"]["ast_nodes"] == sum(1 for _ in ast.walk(ast.parse(fact.value[1], mode="eval").body))
    assert observation["elapsed_seconds"] >= 0 and observation["preview_elapsed_seconds"] >= 0
    assert not {"saved_calls", "saved_time", "certificate", "proved_savings"} & observation.keys()
    json.dumps(asdict(stats), allow_nan=False)


def test_accumulated_observation_lists_are_bounded_and_do_not_authorize_a_rule():
    store = library()
    record = admit(store, "rule:bounded-observations", "(a+b)**4", QUARTIC)
    stats = AdaptiveStats()
    stats.scheduling_decisions = [{"prior_observation": True} for _ in range(512)]
    stats.rule_usage = [{"rule_id": "unverified-rule", "accepted": True} for _ in range(512)]
    outcome, stats, _, _ = solve("(5*z+7)**4", store=store, stats=stats, complete=no_model)
    assert outcome.result.run_result.status == "solved"
    assert outcome.result.run_result.state.facts[0].value[2] == record.candidate.id
    assert len(stats.scheduling_decisions) == len(stats.rule_usage) == 512
    assert stats.scheduling_decisions_omitted == stats.rule_usage_omitted == 1


def test_controller_eligibility_probe_is_read_only_and_respects_all_budgets():
    controller = StrategyController(("local", "model"), max_attempts=3,
                                    max_attempts_per_strategy=2, max_failures_per_strategy=1,
                                    branch_budgets={"main": 1, "other": 2})
    before = controller.snapshot()
    for _ in range(4):
        assert controller.is_eligible("state:first", "local")
        assert controller.is_eligible("state:new", "model", branch="other")
    assert controller.snapshot() == before
    assert controller.select("state:first", ("local",)) == "local"
    controller.record("state:first", "local", "candidate:first", accepted=False,
                      progress=False, reasons=("synthetic_failure",), revision=0)
    after_failure = controller.snapshot()
    assert not controller.is_eligible("state:first", "local", branch="other")
    assert not controller.is_eligible("state:new", "model")  # main branch budget
    assert controller.is_eligible("state:first", "model", branch="other")
    assert controller.snapshot() == after_failure
    for index in range(2):
        state = f"state:fresh-{index}"
        assert controller.select(state, ("model",), branch="other") == "model"
        controller.record(state, "model", f"candidate:{index}", accepted=True, progress=True,
                          revision=index + 1)
    after_exhaustion = controller.snapshot()
    assert not controller.is_eligible("state:never-seen", "local", branch="other")
    assert not controller.is_eligible("state:never-seen", "model")
    assert controller.snapshot() == after_exhaustion


@pytest.mark.parametrize("state,strategy,branch", [
    ("", "local", "main"), ("state", "not-registered", "main"),
    ("state", "local", "not-registered"), (None, "local", "main"),
])
def test_controller_eligibility_rejects_invalid_queries_without_mutation(state, strategy, branch):
    controller = StrategyController(("local",), branch_budgets={"main": 1})
    before = controller.snapshot()
    with pytest.raises(ValueError):
        controller.is_eligible(state, strategy, branch=branch)
    assert controller.snapshot() == before


@pytest.mark.parametrize("option", [
    {"cost_aware_scheduling": 1}, {"cost_aware_scheduling": "true"},
    {"cost_aware_scheduling": None}, {"max_local_work": -1},
    {"max_local_work": True}, {"max_local_work": 1.5},
    {"max_local_work_overflow": -1}, {"max_local_work_overflow": True},
    {"max_local_work_overflow": 1.5},
    {"max_rule_previews": 0}, {"max_rule_previews": 17},
    {"max_rule_previews": True}, {"max_rule_previews": 2.5},
])
def test_scheduling_options_are_strict_and_bounded(option):
    with pytest.raises(ValueError):
        build_adaptive_learning_agent(PolynomialProblem("(z-4)*(z+6)", ("z",)), library(), **option)
