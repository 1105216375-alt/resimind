"""Public development comparison; write raw results outside the repository.

Identical tasks and exact independent scoring across arms. The legacy budget
counts committed steps, search counts attempted actions, and CAS has no search
budget: these are explicitly NOT compute-matched. Train once; never fit on the
evaluation rows. Timings exclude independent scoring and include failed runs.
Optional SymPy is a strong task solver, not a proof-search Agent baseline.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from benchmarks.knowledge_growth import score_expansion

STRESS = ('(x+y)**12', '(x+y)**8*(2*x-3*y+1)**4', '((x+y)**4+(x-y)**4)**2',
          '(x+y+1)**10', '(x+y)**8*(x-y)**8', '((x+y)**3+(2*x-y)**3)**3',
          '(x+y+1)**6*(2*x-y+1)**6', '(x+y)**16')
CASES = tuple({'id': f'transfer-{i}-{j}', 'expression': expression, 'variables': ['x', 'y']}
              for i in (2, 3, 4, 5) for j, expression in enumerate((
                  f'(x+{i}*y)**2', f'({i}*x-y)**3', f'(x+{i})*(y-{i})',
                  f'(x+{i}*y)*(x-{i}*y)', f'((x+y)**2+{i})*(x-y)',
                  f'(x+{i}*y+1)**4')))
CASES += tuple({'id': f'stress-{i}', 'expression': e, 'variables': ['x', 'y']}
               for i, e in enumerate(STRESS))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def balanced_syntax(expression: str) -> str:
    """Balance associative chains so CAS printing does not trip depth limits.

    No coefficient calculation or equality decision occurs here. The resulting
    expression still has to pass the same independent exact scorer.
    """
    def visit(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            return visit(ast.BinOp(node.left, ast.Add(), ast.UnaryOp(ast.USub(), node.right)))
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
            leaves = []
            def collect(value):
                if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Sub) and isinstance(node.op, ast.Add):
                    collect(value.left); leaves.append(ast.UnaryOp(ast.USub(), visit(value.right)))
                    return
                if isinstance(value, ast.BinOp) and type(value.op) is type(node.op):
                    collect(value.left); collect(value.right)
                else:
                    leaves.append(visit(value))
            collect(node)
            def build(items):
                if len(items) == 1: return items[0]
                middle = len(items) // 2
                return ast.BinOp(build(items[:middle]), type(node.op)(), build(items[middle:]))
            return build(leaves)
        for name in ('left', 'right', 'operand'):
            child = getattr(node, name, None)
            if isinstance(child, ast.expr): setattr(node, name, visit(child))
        return node
    return ast.unparse(ast.fix_missing_locations(visit(ast.parse(expression, mode='eval').body)))


def freeze():
    root = Path(__file__).resolve().parents[1]
    sources = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
               for folder in ('src', 'benchmarks') for p in sorted((root / folder).rglob('*.py'))}
    return {'cases': CASES, 'task_digest': digest(CASES), 'source_hashes': sources,
            'source_digest': digest(sources), 'python': sys.version,
            'protocol': 'public within-family development comparison; no frontier ranking',
            'budgets': {'search': {'actions': 64, 'frontier': 64, 'depth': 32, 'model_calls': 0},
                        'legacy': {'max_steps': 64, 'max_local_work': 4096, 'max_local_work_overflow': 0}}}


def legacy_rows():
    from resimind.agent import Task
    from resimind.domains.adaptive_polynomial import build_adaptive_learning_agent, AdaptiveStats
    from resimind.domains.algebra import AlgebraVerifier
    from resimind.domains.polynomial_learning import PolynomialProblem, WorkCounts
    from resimind.knowledge import KnowledgeLibrary
    rows = []
    for case in CASES:
        counts, stats = WorkCounts(), AdaptiveStats()
        started = time.perf_counter()
        result = build_adaptive_learning_agent(PolynomialProblem(case['expression'], tuple(case['variables'])),
            KnowledgeLibrary({'algebra': AlgebraVerifier()}), counts=counts, stats=stats).run(
                Task(case['id'], 'Expand exactly', 'algebra'), learn=False)
        run = result.result.run_result
        elapsed = time.perf_counter() - started
        output = run.state.facts[-1].value[1] if run.status == 'solved' else None
        rows.append({'id': case['id'], 'status': run.status, 'expression': output,
                     'seconds': elapsed, 'steps': run.steps, 'work': asdict(counts), 'stats': asdict(stats),
                     'oracle': score_expansion(case['expression'], output, tuple(case['variables'])) if output else {'status': 'unsolved'}})
    import resimind.domains.adaptive_polynomial as module
    return {'module_path': module.__file__, 'rows': rows}


def totals(rows):
    return {'tasks': len(rows), 'valid': sum(r['oracle']['status'] == 'valid' for r in rows),
            'invalid': sum(r['oracle']['status'] == 'invalid' for r in rows),
            'unknown': sum(r['oracle']['status'] == 'unknown' for r in rows),
            'seconds': sum(r['seconds'] for r in rows),
            'actions': sum(r.get('actions', 0) for r in rows),
            'model_calls': sum(r.get('model_calls', 0) for r in rows),
            'stress_valid': sum(r['oracle']['status'] == 'valid' for r in rows if r['id'].startswith('stress'))}


def compare(output: Path, baseline_root: Path | None):
    from resimind.domains.polynomial_learning import PolynomialProblem
    from resimind.domains.search_learning import SearchBudget, run_search_learning
    from resimind.search_learning_demo import train
    output.mkdir(parents=True, exist_ok=False)
    manifest = freeze()
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    arms, summaries = {}, {}
    if baseline_root is not None:
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(baseline_root / 'src'), str(root))))
        subprocess.run([sys.executable, '-m', 'benchmarks.run_search_learning_eval', '--legacy-worker',
                        '--output', str(output / 'legacy.json')], cwd=output, env=env, check=True, timeout=300)
        data = json.loads((output / 'legacy.json').read_text())
        if not Path(data['module_path']).is_relative_to(baseline_root):
            raise RuntimeError('legacy import did not resolve to the frozen baseline')
        arms['legacy'] = data['rows']
    library, policy, training = train()
    library.save(output / 'rules.json')
    (output / 'policy.json').write_text(policy.to_json())
    (output / 'training.json').write_text(json.dumps(training, indent=2))
    assert not set(training['training_expressions']) & {c['expression'] for c in CASES}
    frozen_policy = policy.fingerprint()
    frozen_rules = digest([r.fingerprint for r in library.lookup('algebra')])
    for arm in ('search', 'with_rules', 'with_policy', 'full', 'no_residual', 'single_frontier', 'sympy'):
        rows = []
        for case in CASES:
            variables = tuple(case['variables'])
            started = time.perf_counter()
            if arm == 'sympy':
                import sympy
                # Only these source-controlled polynomial strings reach sympify.
                symbols = dict(zip(variables, sympy.symbols(' '.join(variables))))
                expression = balanced_syntax(str(sympy.expand(sympy.sympify(case['expression'], locals=symbols))))
                row = {'status': 'solved', 'expression': expression}
            else:
                result = run_search_learning(PolynomialProblem(case['expression'], variables),
                    library=library if arm in ('with_rules', 'full', 'no_residual', 'single_frontier') else None,
                    policy=policy if arm in ('with_policy', 'full', 'no_residual', 'single_frontier') else None,
                    use_residual=arm != 'no_residual',
                    budget=SearchBudget(model_calls=0, frontier=1 if arm == 'single_frontier' else 64))
                row = asdict(result)
            row.update(id=case['id'], seconds=time.perf_counter() - started)
            row['oracle'] = (score_expansion(case['expression'], row['expression'], variables)
                             if row['expression'] is not None else {'status': 'unsolved'})
            rows.append(row)
        arms[arm] = rows
        (output / (arm + '.json')).write_text(json.dumps(rows, indent=2))
        print(arm, json.dumps(totals(rows)), flush=True)
    assert policy.fingerprint() == frozen_policy
    assert digest([r.fingerprint for r in library.lookup('algebra')]) == frozen_rules
    assert freeze()['source_digest'] == manifest['source_digest'], 'source changed during evaluation'
    for arm, rows in arms.items():
        summaries[arm] = totals(rows)
    bridge = bridge_study()
    report = {'manifest_digest': digest(manifest), 'training': training, 'totals': summaries,
              'bridge': bridge, 'scope': manifest['protocol'],
              'budget_caveat': 'Attempted search actions, legacy steps, and CAS operations are not equivalent compute units.'}
    (output / 'summary.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def bridge_study():
    from resimind.domains.bridge import demo_problem
    from resimind.domains.bridge_incremental import IncrementalBridgeAgent
    from benchmarks.unified_bridge_oracle import score_bridge
    start = demo_problem()
    problems = [start, replace(start, live_loads=(26, 25)), replace(start, live_loads=(26, 25), moment_limit=10000),
                replace(start, live_loads=(26, 25), moment_limit=10000, flexural_rigidities=(37000000000, 48000000000))]
    agent = IncrementalBridgeAgent(); rows = []
    for problem in problems:
        result = agent.run(problem); full = IncrementalBridgeAgent().run(problem)
        summary = {key: result.payload[key] for key in ('envelope', 'comparisons')}
        rows.append({'incremental_checks': len(result.checked_cases), 'full_checks': len(full.checked_cases),
                     'identical': result.payload == full.payload, 'independent_oracle': score_bridge(problem, result.cases, summary)})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-root', type=Path)
    parser.add_argument('--legacy-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(root):
        parser.error('keep raw test results outside the public repository')
    if args.legacy_worker:
        args.output.write_text(json.dumps(legacy_rows(), indent=2))
    else:
        compare(args.output.resolve(), args.baseline_root.resolve() if args.baseline_root else None)


if __name__ == '__main__':
    main()
