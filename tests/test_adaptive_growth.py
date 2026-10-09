"""Growth control improves completion without becoming a source of truth."""
import ast

import pytest

from resimind import Decision, Task, Verdict
from resimind.domains import adaptive_polynomial as module
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.domains.algebra import AlgebraVerifier, MAX_AST_NODES, verify_identity
from resimind.domains.polynomial_learning import DOMAIN, PolynomialProblem, WorkCounts
from resimind.knowledge import KnowledgeLibrary


def library():
    return KnowledgeLibrary({DOMAIN: AlgebraVerifier()})


def linear_product(count, variable="x"):
    return "*".join(f"({variable}+{index})" for index in range(1, count + 1))


def check_committed_chain(problem, run):
    previous = problem.expression
    for fact in run.state.facts:
        assert fact.value[0] == previous
        assert verify_identity(previous, fact.value[1], problem.variables).status == "verified"
        assert sum(1 for _ in ast.walk(ast.parse(fact.value[1], mode="eval").body)) < MAX_AST_NODES
        previous = fact.value[1]
    assert verify_identity(problem.expression, previous, problem.variables).status == "verified"


@pytest.mark.parametrize("expression,variables", [
    (linear_product(6), ("x",)),
    (linear_product(8), ("x",)),
    ("(x+1)**8", ("x",)),
    ("(x*x+x+1)*(x*x+2*x+2)*(x*x+3*x+3)*(x*x+4*x+4)", ("x",)),
    ("(x+y+1)*(x+2*y+2)*(x+3*y+3)*(x+4*y+4)", ("x", "y")),
])
def test_default_growth_finishes_previously_expensive_products_with_checked_steps(expression, variables):
    problem = PolynomialProblem(expression, variables)
    stats, counts, store = AdaptiveStats(), WorkCounts(), library()
    outcome = build_adaptive_learning_agent(problem, store, stats=stats, counts=counts).run(
        Task("growth", "Expand this product", DOMAIN), learn=False)
    run = outcome.result.run_result
    assert run.status == "solved" and run.residual.solved
    assert run.steps < 16
    assert stats.action_attempts == run.steps
    assert stats.model_calls == counts.proposal_calls == 0
    assert stats.distribute_accepts > 0
    assert stats.peak_expression_nodes < MAX_AST_NODES
    check_committed_chain(problem, run)
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


def test_every_growth_commit_still_passes_the_state_bound_verifier(monkeypatch):
    original = module._StateBoundPolynomialVerifier.verify
    checked = []

    def spy(self, candidate, state, residual, evidence):
        verdict = original(self, candidate, state, residual, evidence)
        checked.append((candidate.id, state.revision, verdict.decision))
        return verdict

    monkeypatch.setattr(module._StateBoundPolynomialVerifier, "verify", spy)
    problem = PolynomialProblem(linear_product(8), ("x",))
    outcome = build_adaptive_learning_agent(problem, library()).run(Task("gates", "Expand", DOMAIN))
    run = outcome.result.run_result
    assert run.status == "solved"
    committed = [event for event in run.trace if event.decision is Decision.ACCEPT]
    assert [(event.candidate.id, event.before.revision, Decision.ACCEPT) for event in committed] == checked
    assert len(committed) == len(run.state.facts)
    assert len(outcome.admissions) == 1 and outcome.admissions[0].status == "verified"
    assert len(outcome.admissions[0].candidate.derivation) == len(run.state.facts)
    check_committed_chain(problem, run)


@pytest.mark.parametrize("helper", ["compact_expression", "bounded_distribute"])
def test_false_growth_candidate_cannot_commit_or_enter_knowledge(monkeypatch, helper):
    monkeypatch.setattr(module, helper, lambda *args, **kwargs: "0")
    store, stats = library(), AdaptiveStats()
    outcome = build_adaptive_learning_agent(
        PolynomialProblem("(x+1)*(x+2)", ("x",)), store, stats=stats, max_steps=1).run(
            Task("bad-candidate", "Expand", DOMAIN))
    run = outcome.result.run_result
    assert run.status != "solved"
    assert run.state.facts == () and outcome.admissions == () and store.lookup(DOMAIN) == ()
    assert run.trace[0].decision is Decision.REJECT
    assert "polynomial_identity_not_proved" in run.trace[0].reasons
    assert stats.diagnostic_counts["identity_math"] == 1


def test_unknown_checker_does_not_become_a_successful_simplification(monkeypatch):
    def unknown(self, candidate, state, residual, evidence):
        return Verdict.for_candidate(candidate, state, Decision.DEFER, evidence=evidence,
                                     reasons=("verification_unknown",))

    monkeypatch.setattr(module._StateBoundPolynomialVerifier, "verify", unknown)
    store = library()
    outcome = build_adaptive_learning_agent(
        PolynomialProblem(linear_product(6), ("x",)), store, max_steps=6).run(
            Task("unknown", "Expand", DOMAIN))
    assert outcome.result.run_result.status != "solved"
    assert outcome.result.run_result.state.facts == ()
    assert outcome.admissions == () and store.lookup(DOMAIN) == ()


@pytest.mark.parametrize("factor_count,expected_status", [(4, "solved"), (6, "stalled")])
def test_disabling_growth_retains_primitive_path_and_existing_budget(factor_count, expected_status):
    stats, store = AdaptiveStats(), library()
    problem = PolynomialProblem(linear_product(factor_count), ("x",))
    outcome = build_adaptive_learning_agent(problem, store, stats=stats, control_expression_growth=False,
                                             cost_aware_scheduling=False).run(
        Task("legacy", "Expand", DOMAIN), learn=False)
    run = outcome.result.run_result
    assert run.status == expected_status
    assert stats.simplify_attempts == stats.distribute_attempts == 0
    assert stats.simplification_probes == stats.distribution_probes == 0
    assert stats.primitive_attempts == stats.action_attempts == run.steps
    assert run.steps == (25 if factor_count == 4 else 64)
    assert stats.action_budget_exhausted is (factor_count == 6)
    check_committed_chain(problem, run)


def test_reusing_a_learner_gets_fresh_task_budgets_without_learning():
    problem = PolynomialProblem(linear_product(8), ("x",))
    stats, store = AdaptiveStats(), library()
    learner = build_adaptive_learning_agent(problem, store, stats=stats, max_steps=4)
    first = learner.run(Task("limited-a", "Expand", DOMAIN), learn=False)
    second = learner.run(Task("limited-b", "Expand", DOMAIN), learn=False)
    for outcome in (first, second):
        run = outcome.result.run_result
        assert run.status != "solved" and run.steps == 4
        assert outcome.admissions == ()
        check_committed_chain(problem, run)
    assert first.result.run_result.state.facts == second.result.run_result.state.facts
    assert stats.action_attempts == 8 and store.lookup(DOMAIN) == ()
    finished = [event for event in stats.events if event.get("event") == "finished"]
    assert [event["actions_in_task"] for event in finished] == [4, 4]


def test_compact_learned_identity_reloads_and_executes_on_a_new_variable(tmp_path):
    store = library()
    first = build_adaptive_learning_agent(PolynomialProblem(linear_product(8), ("x",)), store).run(
        Task("learn-compact", "Expand", DOMAIN))
    assert first.result.run_result.status == "solved" and first.admissions[0].status == "verified"
    path = tmp_path / "rules.json"
    store.save(path)
    reloaded = KnowledgeLibrary.load(path, {DOMAIN: AlgebraVerifier()})
    stats = AdaptiveStats()
    problem = PolynomialProblem(linear_product(8, "y"), ("y",))
    second = build_adaptive_learning_agent(problem, reloaded, stats=stats).run(
        Task("reuse-compact", "Expand", DOMAIN), learn=False)
    assert second.result.run_result.status == "solved"
    assert stats.rule_accepts == stats.action_attempts == 1
    assert second.admissions == () and len(reloaded.lookup(DOMAIN)) == 1
    check_committed_chain(problem, second.result.run_result)


@pytest.mark.parametrize("enabled", [False, True])
def test_growth_metrics_preserve_legal_zero_products_with_high_syntactic_degree(enabled):
    # The existing grammar bounds actual polynomial degree. The zero factor
    # keeps this valid even though a syntax-only upper bound would be 33.
    problem = PolynomialProblem("0*x**16*x**16*x", ("x",))
    outcome = build_adaptive_learning_agent(problem, library(), control_expression_growth=enabled).run(
        Task("zero-degree", "Verify the zero polynomial", DOMAIN), learn=False)
    assert outcome.result.run_result.status == "solved"
    check_committed_chain(problem, outcome.result.run_result)


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_growth_switch_requires_an_explicit_boolean(value):
    with pytest.raises(ValueError):
        build_adaptive_learning_agent(PolynomialProblem("(x+1)**2", ("x",)), library(),
                                      control_expression_growth=value)
