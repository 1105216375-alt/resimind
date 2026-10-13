"""Executable training, frozen replay, rule reuse and compact math demo."""
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from .domains.lifelong_math import LifelongMathAgent
from .domains.polynomial_learning import PolynomialProblem


def run_demo():
    agent = LifelongMathAgent()
    training = []
    for expression, variables in (('(a+b)**3', ('a','b')), ('(u+v)**3', ('u','v'))):
        training.append(asdict(agent.solve(PolynomialProblem(expression, variables), learn=True, use_policy=False)))
    with TemporaryDirectory(prefix='resimind-lifelong-') as directory:
        agent.freeze().save(Path(directory))
        frozen = LifelongMathAgent.load(Path(directory))
    before = frozen.fingerprint()
    recall = frozen.solve(PolynomialProblem('(2*x+3*y)**3', ('x','y')), routes=('recall',))
    large = frozen.solve(PolynomialProblem('(x+y+1)**6*(2*x-y+1)**6', ('x','y')))
    return {'mode':'offline-lifelong-math', 'live_model':False, 'training':training,
            'verified_rules':len(frozen.library.lookup('algebra')),
            'recall':asdict(recall),'large_expression':asdict(large),
            'frozen_unchanged':frozen.fingerprint()==before,
            'scope':'Bounded polynomial tool orchestration, verified sparse output and empirical routing. '
                    'No model-weight training, arbitrary theorem proving or frontier claim.'}
