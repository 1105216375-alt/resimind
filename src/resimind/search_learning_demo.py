"""Small reproducible training stage and cross-domain offline demonstration."""
from dataclasses import asdict, replace
import time

from .domains.algebra import AlgebraVerifier
from .domains.bridge import demo_problem
from .domains.bridge_incremental import IncrementalBridgeAgent
from .domains.polynomial_learning import PolynomialProblem
from .domains.rule_abstraction import VerifiedExample
from .domains.schema_learning import discover_schemas
from .domains.search_learning import SearchBudget, run_search_learning
from .knowledge import KnowledgeLibrary
from .policy import StrategyPolicy

TRAINING_EXPRESSIONS = ('(a+b)**2', '(u+v)**2', '(a-b)*(a+b)', '(u-v)*(u+v)',
                        '(a+b)**3', '(u+v)**3', '(a+b)*(a-b+1)', '(u+v)*(u-v+1)')


def train():
    """Collect actual execution observations; freeze before any held-out run.

    This is a public, tiny curriculum for within-family transfer. It is not
    model training or evidence of unseen mathematical discovery.
    """
    started = time.perf_counter()
    observations, examples = [], []
    actions = 0
    budget = SearchBudget(actions=16, model_calls=0)
    for index, expression in enumerate(TRAINING_EXPRESSIONS):
        variables = ('a', 'b') if 'a' in expression else ('u', 'v')
        problem = PolynomialProblem(expression, variables)
        result = run_search_learning(problem, budget=budget)
        actions += result.actions
        observations.extend(result.observations)
        if result.status == 'solved':
            examples.append(VerifiedExample(expression, result.expression, variables, f'train-{index}'))
        # Measure strategies that default ordering would otherwise never try.
        for strategy in ('primitive', 'distribute', 'compact'):
            probe = run_search_learning(problem, strategies=(strategy,), budget=budget)
            actions += probe.actions
            observations.extend(probe.observations)
    discovery = discover_schemas(tuple(examples))
    library = KnowledgeLibrary({'algebra': AlgebraVerifier()})
    for candidate in discovery.candidates:
        if library.admit(candidate).status != 'verified':
            raise RuntimeError('independent library admission rejected a schema')
    policy = StrategyPolicy.fit(observations)
    return library, policy, {'training_expressions': list(TRAINING_EXPRESSIONS), 'actions': actions,
        'observations': len(observations), 'verified_rules': len(library.lookup('algebra')),
        'input_checks': discovery.input_checks, 'schema_checks': discovery.schema_checks,
        'rejected_generalizations': discovery.rejected_generalizations,
        'policy_fingerprint': policy.fingerprint(), 'seconds': time.perf_counter() - started}


def run_demo():
    library, policy, training = train()
    problem = PolynomialProblem('(x+2*y)**3', ('x', 'y'))
    cold = run_search_learning(problem)
    warm = run_search_learning(problem, library=library, policy=policy)
    agent = IncrementalBridgeAgent(); original = demo_problem()
    baseline = agent.run(original)
    updated = agent.run(replace(original, live_loads=(26, 25)))
    return {'mode': 'offline-search-learning', 'live_model': False, 'training': training,
        'math': {'cold': asdict(cold), 'warm': asdict(warm)},
        'bridge': {'initial_checked_cases': len(baseline.checked_cases),
                   'updated_checked_cases': len(updated.checked_cases),
                   'reused_cases': len(updated.reused_cases), 'status': updated.status},
        'scope': 'Bounded rational polynomials and a synthetic two-span beam. '
                 'Frozen empirical strategy selection; no model-weight training or general theorem proving.'}
