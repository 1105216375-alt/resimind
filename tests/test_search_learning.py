"""Adversarial checks for the integrated search/learning adapters."""
from dataclasses import replace
import json

import pytest

from benchmarks.knowledge_growth import score_expansion
from benchmarks.unified_bridge_oracle import score_bridge
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.bridge import demo_problem, LoadCombination
from resimind.domains.bridge_incremental import IncrementalBridgeAgent
from resimind.domains.polynomial_learning import PolynomialProblem
from resimind.domains.rule_abstraction import VerifiedExample
from resimind.domains.schema_learning import discover_schemas, apply_schema
from resimind.domains.search_learning import SearchBudget, residual_graph, run_search_learning
from resimind.integrations.lean import LeanPolynomialBackend
from resimind.knowledge import KnowledgeLibrary
from resimind.obligations import Obligation, ObligationGraph, ObligationUpdate
from resimind.policy import StrategyPolicy
from resimind.cli import main


def square_examples():
    return (VerifiedExample('(x+y)**2', 'x**2+2*x*y+y**2', ('x', 'y'), 'one'),
            VerifiedExample('(a+b)**2', 'a**2+2*a*b+b**2', ('a', 'b'), 'two'))


def test_dependency_replacement_cannot_leave_stale_verified_descendants():
    graph = ObligationGraph((Obligation('a', 'input'), Obligation('b', 'middle', ('a',)),
                             Obligation('c', 'result', ('b',))))
    with pytest.raises(ValueError, match='unverified'):
        graph.update(ObligationUpdate('c', 'verified'))
    for name in ('a', 'b', 'c'):
        graph = graph.update(ObligationUpdate(name, 'verified', 'old'))
    changed = graph.update(ObligationUpdate('a', 'verified', 'new'))
    assert graph.solved and not changed.solved
    assert [n.id for n in changed.plan_slice('c')] == ['b', 'c']
    assert changed.get('c').status == 'invalidated'
    with pytest.raises(ValueError, match='invalidate_evidence'):
        graph.update(ObligationUpdate('a', 'verified', evidence_revision='new'))
    with pytest.raises(ValueError, match='unverified'):
        ObligationGraph((Obligation('a', 'input'), Obligation('b', 'result', ('a',), status='verified')))


def test_residual_graph_orders_nested_work_before_parent():
    graph = residual_graph('((x+y)**2+1)*(x+y)')
    assert graph.get('goal').depends_on == ('ast:',)
    assert 'ast:' not in [n.id for n in graph.ready()]
    assert all(not n.depends_on for n in graph.ready())


@pytest.mark.parametrize('lhs,rhs', [
    ('(x+y)**2', 'x**2+2*x*y+y**2'),
    ('(x-y)*(x+y)', 'x**2-y**2'),
    ('(x-y)*y', 'x*y-y*y'),
])
def test_generic_schemas_transfer_and_revoke(lhs, rhs):
    one = VerifiedExample(lhs, rhs, ('x', 'y'), 'one')
    two = VerifiedExample(lhs.replace('x', 'a').replace('y', 'b'),
                          rhs.replace('x', 'a').replace('y', 'b'), ('a', 'b'), 'two')
    discovery = discover_schemas((one, two))
    assert len(discovery.candidates) == 1
    library = KnowledgeLibrary({'algebra': AlgebraVerifier()})
    record = library.admit(discovery.candidates[0])
    expression = lhs.replace('x', '(2*u)').replace('y', '(3*v)')
    after = apply_schema(library, record, expression, ('u', 'v'))
    assert after is not None
    library.revoke(record.candidate.id, 'regression')
    assert apply_schema(library, record, expression, ('u', 'v')) is None


def test_false_examples_and_unsupported_generalizations_are_not_admitted():
    one, two = square_examples()
    assert not discover_schemas((one, replace(two, source_id=one.source_id))).candidates
    assert discover_schemas((one, replace(two, after='a**2+b**2'))).rejected_examples == 1
    # Each individual equality holds, but erasing different constants on each
    # side cannot establish the supposed universal rule.
    examples = (VerifiedExample('x*0', '0', ('x',), 'a'),
                VerifiedExample('y*1', 'y', ('y',), 'b'))
    assert not discover_schemas(examples).candidates


@pytest.mark.parametrize('expression', ['(x+y)**5', '((x+y)**2+1)*(x-y)', '(x+y)*(x-y)'])
def test_search_output_checked_by_independent_complete_grid(expression):
    result = run_search_learning(PolynomialProblem(expression, ('x', 'y')))
    assert result.status == 'solved'
    assert score_expansion(expression, result.expression, ('x', 'y'))['status'] == 'valid'
    assert result.proof[0]['before'] == expression
    assert all(a['after'] == b['before'] for a, b in zip(result.proof, result.proof[1:]))
    assert result.model_calls == 0


def test_model_rejection_feedback_and_global_call_limit():
    prompts = []
    def complete(prompt):
        prompts.append(json.loads(prompt))
        return json.dumps({'after': 'x*x+3*x+2' if len(prompts) == 2 else '0'})
    result = run_search_learning(PolynomialProblem('(x+1)*(x+2)', ('x',)),
                                complete=complete, strategies=('model',))
    assert result.status == 'solved' and result.rejected == 1 and result.model_calls == 2
    assert prompts[1]['feedback'][0]['verified'] is False
    result = run_search_learning(PolynomialProblem('(x+1)**2', ('x',)),
        complete=lambda _: '{malformed', strategies=('model',), budget=SearchBudget(model_calls=2))
    assert result.status == 'limited' and result.expression is None and result.model_calls == 2


def test_model_false_or_executable_output_never_reaches_a_proof():
    for after in ('x*x+1', "__import__('os').system('false')"):
        result = run_search_learning(PolynomialProblem('(x+1)**2', ('x',)),
            complete=lambda _: json.dumps({'after': after}), strategies=('model',),
            budget=SearchBudget(model_calls=1))
        assert result.status == 'limited' and not result.proof


@pytest.mark.parametrize('first,feedback', [
    ({'after': '(x+1)**2'}, 'no_new_state_try_different_rewrite'),
    ({'after': 'x*x+2*x+1', 'tactic': 'ring'}, 'unsupported_tactic_use_grind_or_rfl'),
])
def test_actionable_feedback_recovers_from_duplicate_or_unsupported_tactic(first, feedback):
    prompts = []
    def complete(prompt):
        prompts.append(json.loads(prompt))
        return json.dumps(first if len(prompts) == 1 else {'after': 'x*x+2*x+1', 'tactic': 'grind'})
    result = run_search_learning(PolynomialProblem('(x+1)**2', ('x',)), complete=complete,
                                strategies=('model',), budget=SearchBudget(model_calls=2))
    assert result.status == 'solved' and result.model_calls == 2
    assert feedback in prompts[1]['feedback'][0]['reason']


def test_callback_errors_never_leak_exception_text_to_feedback():
    def fail(_):
        raise ValueError('pretend_secret_token')
    result = run_search_learning(PolynomialProblem('(x+1)**2', ('x',)), complete=fail,
                                strategies=('model',), budget=SearchBudget(model_calls=1))
    assert 'pretend_secret_token' not in json.dumps(result.trace)
    assert 'model_callback' in result.trace[0]['reason']


def test_frozen_policy_uses_execution_experience_without_training_on_eval():
    training = run_search_learning(PolynomialProblem('(a+b)**3', ('a', 'b')))
    policy = StrategyPolicy.fit(training.observations, min_evidence=1)
    before = policy.fingerprint()
    run_search_learning(PolynomialProblem('(x+2*y)**3', ('x', 'y')), policy=policy)
    assert policy.fingerprint() == before
    with pytest.raises(AttributeError):
        policy.min_evidence = 5
    payload = json.loads(policy.to_json()); payload['extra'] = 'hidden setting'
    with pytest.raises(ValueError):
        StrategyPolicy.from_json(json.dumps(payload))
    with pytest.raises(ValueError):
        StrategyPolicy.from_json(policy.to_json().replace('"version":', '"version":"duplicate","version":'))


def test_rule_path_finishes_without_model_and_honors_admission():
    library = KnowledgeLibrary({'algebra': AlgebraVerifier()})
    for candidate in discover_schemas(square_examples()).candidates:
        assert library.admit(candidate).status == 'verified'
    result = run_search_learning(PolynomialProblem('(u+v)**2', ('u', 'v')), library=library,
                                strategies=('rule',))
    assert result.status == 'solved' and result.actions == 1
    assert result.proof[0]['rule_id'].startswith('abstract:')


def test_budget_failure_never_releases_partial_result():
    result = run_search_learning(PolynomialProblem('(x+y)**8', ('x', 'y')),
                                budget=SearchBudget(actions=1, frontier=1))
    assert result.status == 'limited' and result.expression is None
    assert result.actions == 1 and result.dropped_actions > 0


def test_missing_lean_never_silently_falls_back_even_on_expanded_input():
    for expression in ('x+1', '(x+1)**2'):
        result = run_search_learning(PolynomialProblem(expression, ('x',)),
            lean_backend=LeanPolynomialBackend('/nonexistent/resimind-lean'), budget=SearchBudget(actions=4))
        assert result.status == 'limited' and result.expression is None


def test_real_lean_feedback_repairs_tactic_and_retains_replay_source():
    backend = LeanPolynomialBackend()
    if backend.lean_path is None:
        pytest.skip('optional Lean installation absent')
    prompts = []
    def complete(prompt):
        prompts.append(json.loads(prompt))
        return json.dumps({'after': 'x*x+3*x+2', 'tactic': 'rfl' if len(prompts) == 1 else 'grind'})
    result = run_search_learning(PolynomialProblem('(x+1)*(x+2)', ('x',)),
                                complete=complete, strategies=('model',), lean_backend=backend)
    assert result.status == 'solved' and result.lean_checks == 2
    assert 'lean_incomplete' in prompts[1]['feedback'][0]['reason']
    assert 'theorem resimind_checked' in result.proof[0]['lean_source']


@pytest.mark.parametrize('change,expected', [
    ({'live_loads': (26, 25)}, 4), ({'moment_limit': 10000}, 0),
    ({'flexural_rigidities': (37000000000, 48000000000)}, 8),
    ({'combinations': (LoadCombination('fresh'),)}, 4),
    ({'subject': 'different-model'}, 8),
])
def test_incremental_bridge_matches_independent_oracle(change, expected):
    problem = demo_problem()
    agent = IncrementalBridgeAgent()
    old = agent.run(problem)
    assert len(old.checked_cases) == 8
    new = replace(problem, **change)
    result = agent.run(new)
    assert result.status == 'verified' and len(result.checked_cases) == expected
    assert result.payload == IncrementalBridgeAgent().run(new).payload
    assert score_bridge(new, result.cases, {k: result.payload[k] for k in ('envelope', 'comparisons')})
    if expected == 0:
        assert result.envelope_checks == 0 and result.comparison_checks == 1


def test_bridge_invalid_input_and_caller_mutation_cannot_bless_stale_cache():
    problem = demo_problem(); agent = IncrementalBridgeAgent()
    result = agent.run(problem)
    result.payload['cases'].clear()
    bad = agent.run(replace(problem, live_loads=(None, 25)))
    assert bad.status == 'deferred' and bad.payload == {}
    again = agent.run(problem)
    assert again.payload == result.payload and len(again.reused_cases) == 8


@pytest.mark.parametrize('kwargs', [{'actions': 0}, {'frontier': 0}, {'depth': True}, {'model_calls': -1}, {'lean_checks': 513}])
def test_search_budget_rejects_unbounded_or_ambiguous_values(kwargs):
    with pytest.raises(ValueError):
        SearchBudget(**kwargs)


def test_cli_search_learning_runs_complete_offline_workflow(capsys):
    assert main(['demo', '--domain', 'search-learning', '--json']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['training']['observations'] > 0 and report['training']['verified_rules'] > 0
    assert report['math']['warm']['model_calls'] == 0
    assert report['math']['warm']['status'] == 'solved'
    assert report['bridge']['updated_checked_cases'] == 4


def test_search_keeps_an_alternative_after_failed_local_proposal(monkeypatch):
    from resimind.domains import search_learning as module
    monkeypatch.setattr(module, '_primitive', lambda *args: '0')
    result = module.run_search_learning(PolynomialProblem('(x+1)*(x+2)', ('x',)))
    assert result.status == 'solved'
    assert result.trace[0]['verified'] is False
    assert result.proof[0]['strategy'] == 'distribute'


def test_forged_formal_source_cannot_be_accepted(monkeypatch):
    from resimind.integrations.lean import LeanProofResult, polynomial_binding_digest, _source
    backend = LeanPolynomialBackend('/nonexistent/resimind-lean')
    def forged(before, after, variables, *, tactic):
        source, target, mapping = _source(before, after, variables, tactic, backend.max_heartbeats)
        return LeanProofResult('verified', tactic, target, 'actual target', (), (),
            polynomial_binding_digest(before, after, variables), '0' * 64,
            '1' * 64, (), '4.29.0', mapping)
    monkeypatch.setattr(backend, 'check', forged)
    result = run_search_learning(PolynomialProblem('(x+1)*(x+2)', ('x',)),
        complete=lambda _: json.dumps({'after': 'x*x+3*x+2'}), strategies=('model',),
        lean_backend=backend, budget=SearchBudget(model_calls=1))
    assert result.status == 'limited' and result.expression is None


def test_cas_output_balancing_preserves_identity():
    from benchmarks.run_search_learning_eval import balanced_syntax
    expression = '+'.join(f'{i}*x**2' for i in range(1, 80))
    balanced = balanced_syntax(expression)
    assert score_expansion('3160*x**2', balanced, ('x',))['status'] == 'valid'
    subtraction = 'x**2-' + '-'.join(f'{i}*x**2' for i in range(1, 80))
    assert score_expansion('-3159*x**2', balanced_syntax(subtraction), ('x',))['status'] == 'valid'
