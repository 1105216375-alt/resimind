from dataclasses import replace
from fractions import Fraction
import json

import pytest

from benchmarks.knowledge_growth import score_expansion
from benchmarks.run_search_learning_eval import STRESS
from resimind.domains.compact_algebra import (
    CompactBudget, ExpansionCertificate, SparseTerm, propose_expansion, run_compact, verify_expansion)
from resimind.domains.lifelong_math import LifelongMathAgent
from resimind.domains.polynomial_learning import PolynomialProblem
from resimind.domains.search_learning import SearchBudget, run_search_learning
from resimind.integrations.lean import LeanPolynomialBackend
from resimind.domains.algebra import AlgebraVerifier
from resimind.knowledge import KnowledgeLibrary, KnowledgeCandidate
from resimind.domains.rule_index import RuleIndex


@pytest.mark.parametrize('expression', STRESS)
def test_compact_stress_outputs_pass_independent_exact_grid(expression):
    result = run_compact(expression, ('x', 'y'))
    assert result.status == 'solved'
    assert score_expansion(expression, result.certificate.render(), ('x', 'y'))['status'] == 'valid'
    assert len(result.certificate.fingerprint) == 64


def test_repeated_subexpression_uses_shared_nodes_and_exact_fractions():
    source = '((x+y)**4+(x+y)**4)/3'
    result = run_compact(source, ('x', 'y'))
    assert result.status == 'solved' and result.stats.shared_uses > 0
    assert result.stats.unique_nodes < result.stats.subexpressions
    assert any(t.denominator == 3 for t in result.certificate.terms)
    assert score_expansion(source, result.certificate.render(), ('x', 'y'))['status'] == 'valid'


@pytest.mark.parametrize('source', ["__import__('os')", '(z+1)**0', 'x/0', 'x**(-1)', 'x**17', 'x/2.0', 'True+x'])
def test_unsupported_source_cannot_hide_behind_simplification(source):
    result = run_compact(source, ('x',))
    assert result.status == 'limited' and result.certificate is None


def test_wrong_binding_or_one_corrupted_coefficient_fails_closed():
    source = '(x+1)**4'
    certificate = propose_expansion(source, ('x',))
    assert verify_expansion('(x+2)**4', ('x',), certificate).status == 'rejected'
    terms = list(certificate.terms)
    terms[0] = replace(terms[0], numerator=terms[0].numerator+1)
    assert verify_expansion(source, ('x',), replace(certificate, terms=tuple(terms))).status == 'rejected'
    assert verify_expansion(source, ('y',), certificate).status == 'rejected'


@pytest.mark.parametrize('term', [SparseTerm((1,), True), SparseTerm((1,), 0), SparseTerm((-1,), 1),
    SparseTerm((1,), 2, 2), SparseTerm((1,), 1, -1), SparseTerm((33,), 1), SparseTerm((1,), 1<<513),
    SparseTerm((True,), 1), SparseTerm((1, 0), 1)])
def test_malformed_sparse_terms_never_become_verified(term):
    certificate = ExpansionCertificate('x', ('x',), (term,))
    assert verify_expansion('x', ('x',), certificate).status != 'verified'


def test_duplicate_or_unsorted_sparse_terms_and_budget_exhaustion():
    for terms in ((SparseTerm((1,), 1), SparseTerm((1,), 1)),
                  (SparseTerm((1,), 1), SparseTerm((0,), 1))):
        assert verify_expansion('x+1', ('x',), ExpansionCertificate('x+1', ('x',), terms)).status != 'verified'
    for budget in (CompactBudget(products=1), CompactBudget(terms=1), CompactBudget(nodes=1)):
        result = run_compact('(x+y)**8', ('x','y'), budget=budget)
        assert result.status == 'limited' and result.certificate is None


def test_proposer_does_not_use_verifier_as_answer_oracle(monkeypatch):
    import resimind.domains.compact_algebra as module
    monkeypatch.setattr(module._Ring, 'read', lambda *args: pytest.fail('proposer called checker'))
    certificate = module.propose_expansion('(x+1)**3', ('x',))
    assert len(certificate.terms) == 4


def test_zero_and_exponent_zero_have_canonical_sparse_forms():
    result = run_compact('(x-x)**0', ('x',))
    assert result.certificate.terms == (SparseTerm((0,), 1),)
    zero = run_compact('x-x', ('x',))
    assert zero.certificate.terms == () and zero.certificate.render() == '0'


def trained_agent():
    agent = LifelongMathAgent()
    for source, variables in (('(a+b)**3', ('a','b')), ('(u+v)**3', ('u','v'))):
        assert agent.solve(PolynomialProblem(source, variables), learn=True, use_policy=False).status == 'solved'
    return agent


def test_episode_learns_general_rule_then_uses_it_without_model():
    agent = trained_agent()
    assert len(agent.library.lookup('algebra')) == 1
    result = agent.solve(PolynomialProblem('(2*x+3*y)**3', ('x','y')), routes=('recall',))
    assert result.status == 'solved' and result.route == 'recall'
    assert result.search_actions == 1 and result.model_calls == 0
    assert score_expansion('(2*x+3*y)**3', result.expression, ('x','y'))['status'] == 'valid'


def test_freeze_is_detached_and_blocks_learning_and_revocation_stays_effective():
    agent = trained_agent(); frozen = agent.freeze(); before = frozen.fingerprint()
    problem = PolynomialProblem('(2*x+y)**3', ('x','y'))
    with pytest.raises(RuntimeError, match='frozen'): frozen.solve(problem, learn=True)
    ident = agent.library.lookup('algebra')[0].candidate.id
    agent.library.revoke(ident, 'withdrawn')
    assert frozen.solve(problem, routes=('recall',)).status == 'solved'
    assert frozen.fingerprint() == before
    assert agent.solve(problem, routes=('recall',)).status == 'limited'


def test_saved_memory_rechecks_rules_and_history_and_policy_round_trips(tmp_path):
    agent = trained_agent().freeze(); agent.save(tmp_path)
    restored = LifelongMathAgent.load(tmp_path)
    assert restored.fingerprint() == agent.fingerprint()
    data = json.loads((tmp_path/'learning.json').read_text())
    data['examples'][0]['after'] = '0'
    (tmp_path/'learning.json').write_text(json.dumps(data))
    with pytest.raises(ValueError, match='unverified'): LifelongMathAgent.load(tmp_path)


def test_formal_requirement_never_falls_back_to_exact_only_compact_result():
    agent = LifelongMathAgent()
    result = agent.solve(PolynomialProblem('(x+1)**2', ('x',)),
        lean_backend=LeanPolynomialBackend('/does/not/exist'), budget=SearchBudget(actions=2))
    assert result.status == 'limited'
    assert any(row['diagnosis']=='formal_proof_required' for row in result.attempts)


def test_route_changes_do_not_reset_search_or_model_budgets():
    agent = trained_agent()
    result = agent.solve(PolynomialProblem('(x+1)**8', ('x',)), routes=('recall','search'),
        budget=SearchBudget(actions=2, model_calls=1), complete=lambda _: '{bad')
    assert result.search_actions <= 2 and result.model_calls <= 1


def test_rule_retrieval_finds_applicable_rule_after_sixteen_irrelevant_records():
    library = KnowledgeLibrary({'algebra': AlgebraVerifier()})
    for i in range(20):
        library.admit(KnowledgeCandidate(id=f'irrelevant:{i}', domain='algebra', kind='polynomial_identity',
            statement={'lhs':'a-a','rhs':'0','variables':['a']}, derivation=({'lhs':'a-a','rhs':'0'},),
            source_task_id='test'))
    source = trained_agent().library.lookup('algebra')[0].candidate
    library.admit(source)
    result = run_search_learning(PolynomialProblem('(x+y)**3', ('x','y')), library=library,
                                strategies=('rule',), indexed_retrieval=True)
    assert result.status == 'solved' and result.actions == 1 and result.indexed_rules == 21
    assert result.rule_pattern_checks < 21


def test_index_cache_never_bypasses_current_library_revocation():
    agent = trained_agent(); index = RuleIndex(agent.library)
    first = index.lookup('(x+y)**3'); second = index.lookup('(x+y)**3')
    assert first == second and index.stats.cache_hits == 1
    agent.library.revoke(first[0].candidate.id, 'invalidated')
    from resimind.domains.schema_learning import apply_schema
    assert apply_schema(agent.library, first[0], '(x+y)**3', ('x','y')) is None


def test_typed_diagnosis_enters_model_repair_prompt():
    prompts=[]
    def complete(prompt):
        prompts.append(json.loads(prompt))
        return json.dumps({'after':'0' if len(prompts)==1 else 'x*x+2*x+1'})
    result=run_search_learning(PolynomialProblem('(x+1)**2',('x',)),complete=complete,strategies=('model',))
    assert result.status=='solved'
    assert prompts[1]['feedback'][0]['diagnosis']['kind']=='identity_mismatch'


def test_lifelong_cli_runs_saved_frozen_reuse_and_large_expression(capsys):
    from resimind.cli import main
    assert main(['demo','--domain','lifelong','--json'])==0
    report=json.loads(capsys.readouterr().out)
    assert report['verified_rules']==1 and report['frozen_unchanged']
    assert report['recall']['route']=='recall' and report['recall']['model_calls']==0
    assert report['large_expression']['compact']['verification']['status']=='verified'


def test_mixed_snapshot_generation_and_duplicate_metadata_are_rejected(tmp_path):
    agent=trained_agent().freeze(); agent.save(tmp_path)
    saved=(tmp_path/'rules.json').read_text()
    (tmp_path/'rules.json').write_text(saved+' ')
    with pytest.raises(ValueError,match='mixed or modified'): LifelongMathAgent.load(tmp_path)
    (tmp_path/'rules.json').write_text(saved)
    text=(tmp_path/'learning.json').read_text()
    (tmp_path/'learning.json').write_text(text.replace('"version": 1','"version": 1, "version": 1'))
    with pytest.raises(ValueError,match='duplicate'): LifelongMathAgent.load(tmp_path)


def test_retired_knowledge_still_counts_against_record_bound():
    agent=trained_agent()
    record=agent.library.lookup('algebra')[0]
    agent.library.revoke(record.candidate.id,'retire')
    assert agent.library.record_count==1 and len(agent.library.lookup('algebra'))==0


def test_benchmark_scorer_rejects_factored_and_duplicated_answers():
    from benchmarks.run_lifelong_eval import score
    case={'source':'(x+1)**2','variables':['x']}
    expected={(2,):Fraction(1),(1,):Fraction(2),(0,):Fraction(1)}
    assert score(case,{'status':'solved','expression':'(x+1)**2'},expected)=='incomplete'
    terms=[{'powers':[p[0]],'numerator':int(c),'denominator':1} for p,c in expected.items()]
    assert score(case,{'status':'solved','terms':terms+[terms[0]]},expected)=='invalid'
    terms[0]['numerator']+=1
    assert score(case,{'status':'solved','terms':terms},expected)=='invalid'


def test_generated_evaluation_tasks_are_unique_and_training_is_separate():
    from benchmarks.run_lifelong_eval import SEEDS, TRAINING, generate
    cases=[]
    for seed in SEEDS: cases.extend(generate(seed,excluded={c['source'] for c in cases}))
    assert len(cases)==len({c['source'] for c in cases})==240
    assert not set(TRAINING)&{c['source'] for c in cases}
