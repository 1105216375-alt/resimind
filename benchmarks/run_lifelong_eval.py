"""Seeded development evaluation with frozen training and explicit ablations.

This is bounded polynomial tool orchestration, not a general theorem benchmark.
Tasks are generated after recording the source hash. Entire generated families
are reported; no failure is discarded or promoted by a production verifier.
"""
from __future__ import annotations
import argparse
import ast
from dataclasses import asdict
from fractions import Fraction
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

from benchmarks.run_search_learning_eval import freeze, digest
from benchmarks.knowledge_growth import _expanded

SEEDS = (91, 731, 20261013)
TRAINING = ('(a+b)**2','(u+v)**2','(a+b)**3','(u+v)**3',
            '(a-b)*(a+b)','(u-v)*(u+v)','(a+b)*(a-b+1)','(u+v)*(u-v+1)')


def generate(seed, per_family=16, excluded=()):
    rng = random.Random(seed); rows=[]; seen=set(excluded)
    for family in ('power_transfer','composed_holdout','rational_holdout','shared_cancel','large_output'):
        count=0
        while count < per_family:
            a,b,c,d = [rng.randint(2,11) for _ in range(4)]
            p,q = rng.randint(4,8),rng.randint(2,6)
            if family == 'power_transfer': expression=f'({a}*x+{b}*y)**{rng.choice((2,3))}'
            elif family == 'composed_holdout': expression=f'(({a}*x+{b}*y)**2+{c})*({d}*x-y)'
            elif family == 'rational_holdout': expression=f'(({a}*x+{b}*y)/{c}+(x-y)/{d})**{p}'
            elif family == 'shared_cancel': expression=f'({a}*x+{b}*y)**{p}-({a}*x+{b}*y)**{p}+({c}*x-{d}*y)**{q}'
            else: expression=f'({a}*x+{b}*y+{c})**{rng.randint(7,10)}'
            if expression in seen: continue
            seen.add(expression); count+=1
            rows.append({'id':f'{seed}:{family}:{count}','family':family,'source':expression,'variables':['x','y']})
    return rows


def train_agent():
    from resimind.domains.lifelong_math import LifelongMathAgent
    from resimind.domains.polynomial_learning import PolynomialProblem
    agent=LifelongMathAgent(); episodes=[]; started=time.perf_counter()
    for expression in TRAINING:
        variables=('a','b') if 'a' in expression else ('u','v')
        result=agent.solve(PolynomialProblem(expression,variables),learn=True,use_policy=False)
        episodes.append(asdict(result))
    # Explicit training-only exploration: observe recall and computation on the
    # curriculum, rather than assuming an unseen strategy is useful or cheap.
    for route in ('recall','compact'):
        for expression in TRAINING:
            variables=('a','b') if 'a' in expression else ('u','v')
            result=agent.solve(PolynomialProblem(expression,variables),routes=(route,),learn=True,use_policy=False)
            episodes.append(asdict(result))
    report={'expressions':TRAINING,'episodes':episodes,'seconds':time.perf_counter()-started,
            'rules':len(agent.library.lookup('algebra')),'schema_checks':sum(e['learning_checks'] for e in episodes)}
    return agent.freeze(),report


def baseline_worker(cases, output):
    from resimind.domains.polynomial_learning import PolynomialProblem
    from resimind.domains.search_learning import run_search_learning, SearchBudget
    from resimind.search_learning_demo import train
    import resimind.domains.search_learning as module
    library,policy,training=train(); rows=[]
    for case in cases:
        started=time.perf_counter()
        result=run_search_learning(PolynomialProblem(case['source'],tuple(case['variables'])),
            library=library,policy=policy,budget=SearchBudget(model_calls=0))
        rows.append({'id':case['id'],'status':result.status,'expression':result.expression,
                     'seconds':time.perf_counter()-started,'actions':result.actions})
    output.write_text(json.dumps({'module':module.__file__,'rows':rows,'training':training},indent=2))


def oracle(case):
    import sympy as sp
    x,y=sp.symbols('x y')
    poly=sp.Poly(sp.sympify(case['source'],locals={'x':x,'y':y}),x,y)
    return {tuple(p):Fraction(int(c.p),int(c.q)) for p,c in poly.terms() if c}


def score(case,row,expected):
    """Independent SymPy coefficients; output from this benchmark is local code."""
    if row['status'] != 'solved': return 'unsolved'
    if row.get('terms') is not None:
        terms=row['terms']; actual={}
        if type(terms) not in (list,tuple): return 'invalid'
        for term in terms:
            if (type(term) is not dict or set(term)!={'powers','numerator','denominator'}
                    or type(term['powers']) not in (list,tuple)
                    or len(term['powers'])!=len(case['variables'])
                    or any(type(p) is not int or p<0 for p in term['powers'])
                    or type(term['numerator']) is not int or not term['numerator']
                    or type(term['denominator']) is not int or term['denominator']<=0): return 'invalid'
            power=tuple(term['powers'])
            if power in actual: return 'invalid'
            actual[power]=Fraction(term['numerator'],term['denominator'])
    elif row.get('expression') is not None:
        if not _expanded(ast.parse(row['expression'],mode='eval').body): return 'incomplete'
        actual=oracle({**case,'source':row['expression']})
    else: return 'unknown'
    return 'valid' if actual==expected else 'invalid'


def summarize(rows):
    return {'tasks':len(rows),'valid':sum(r['oracle']=='valid' for r in rows),
        'invalid':sum(r['oracle']=='invalid' for r in rows),'unknown':sum(r['oracle']=='unknown' for r in rows),
        'seconds':sum(r['seconds'] for r in rows),'median_ms':statistics.median(r['seconds'] for r in rows)*1000,
        'actions':sum(r.get('actions',0) for r in rows),'model_calls':sum(r.get('model_calls',0) for r in rows),
        'recall_solved':sum(r.get('route')=='recall' and r['oracle']=='valid' for r in rows),
        'families':{f:sum(r['oracle']=='valid' for r in rows if r['family']==f)
                    for f in sorted({r['family'] for r in rows})}}


def study(output,baseline_root):
    from resimind.domains.lifelong_math import LifelongMathAgent
    from resimind.domains.polynomial_learning import PolynomialProblem
    from resimind.domains.search_learning import SearchBudget
    from resimind.domains.compact_algebra import run_compact
    import sympy as sp
    output.mkdir(parents=True,exist_ok=False)
    manifest=freeze()
    # This protocol has its own generated task set. Do not carry the older
    # 32-task manifest fields from the shared source-hashing helper.
    manifest.pop('cases',None); manifest.pop('task_digest',None)
    manifest.update(protocol='seeded within-domain development comparison; no frontier ranking',
        seeds=SEEDS,training=TRAINING,per_family=16,
        budgets={'search_actions':64,'model_calls':0,'sparse_products':100000,'sparse_terms':4096},
        budget_caveat='Equal configured route budgets; symbolic and search operations are not equivalent work units.')
    (output/'source-freeze.json').write_text(json.dumps(manifest,indent=2))
    cases=[]
    for seed in SEEDS:
        cases.extend(generate(seed,excluded={c['source'] for c in cases}))
    assert not set(TRAINING)&{c['source'] for c in cases}
    (output/'tasks.json').write_text(json.dumps(cases,indent=2))
    frozen,training=train_agent(); frozen.save(output/'memory')
    (output/'training.json').write_text(json.dumps(training,indent=2))
    before=frozen.fingerprint()
    arms={a:[] for a in ('compact_only','cold_router','warm_fixed','warm_policy','sympy')}
    if baseline_root:
        root=Path(__file__).resolve().parents[1]
        env=dict(os.environ,PYTHONPATH=os.pathsep.join((str(baseline_root/'src'),str(root))))
        subprocess.run([sys.executable,'-m','benchmarks.run_lifelong_eval','--worker',str(output/'tasks.json'),
                        '--output',str(output/'baseline.json')],cwd=output,env=env,check=True,timeout=300)
        prior=json.loads((output/'baseline.json').read_text())
        if not Path(prior['module']).is_relative_to(baseline_root): raise RuntimeError('wrong baseline import')
        lookup={c['id']:c for c in cases}
        arms['prior_search']=prior['rows']
        for row in arms['prior_search']: row['family']=lookup[row['id']]['family']
        print('prior search finished; independent scoring follows timed runs',flush=True)
    x,y=sp.symbols('x y')
    # Warm the optional CAS import before timing any arm.
    sp.expand((x+y)**2)
    order_rng=random.Random(1234)
    for number,case in enumerate(cases):
        order=['compact_only','cold_router','warm_fixed','warm_policy','sympy']; order_rng.shuffle(order)
        case_rows=[]
        for arm in order:
            started=time.perf_counter()
            if arm=='sympy':
                poly=sp.Poly(sp.expand(sp.sympify(case['source'],locals={'x':x,'y':y})),x,y)
                row={'status':'solved','terms':[{'powers':p,'numerator':int(c.p),'denominator':int(c.q)} for p,c in poly.terms() if c]}
            elif arm=='compact_only':
                result=run_compact(case['source'],tuple(case['variables']))
                row={'status':result.status,'terms':[asdict(t) for t in result.certificate.terms] if result.certificate else None,
                     'work':asdict(result.stats),'route':'compact'}
            else:
                agent=LifelongMathAgent(frozen=True) if arm=='cold_router' else frozen
                result=agent.solve(PolynomialProblem(case['source'],tuple(case['variables'])),
                    budget=SearchBudget(model_calls=0),use_policy=arm=='warm_policy')
                row={'status':result.status,'route':result.route,'expression':result.expression,
                     'actions':result.search_actions,'model_calls':result.model_calls,
                     'attempts':result.attempts,'terms':None}
                if result.route=='compact':
                    row['terms']=[asdict(t) for t in result.compact.certificate.terms]
                    row['work']=asdict(result.compact.stats)
            row.update(seconds=time.perf_counter()-started,id=case['id'],family=case['family'])
            case_rows.append(row)
            arms[arm].append(row)
        expected=oracle(case)
        for row in case_rows: row['oracle']=score(case,row,expected)
        if (number+1)%40==0: print('evaluated',number+1,'/',len(cases),flush=True)
    if frozen.fingerprint()!=before: raise RuntimeError('evaluation mutated memory')
    if freeze()['source_digest']!=manifest['source_digest']: raise RuntimeError('sources changed during evaluation')
    if 'prior_search' in arms:
        for row in arms['prior_search']:
            case=lookup[row['id']]; row['oracle']=score(case,row,oracle(case))
    for arm,rows in arms.items(): (output/(arm+'.json')).write_text(json.dumps(rows,indent=2))
    summary={arm:summarize(rows) for arm,rows in arms.items()}
    paired={}
    for a,b in (('cold_router','prior_search'),('warm_fixed','cold_router'),('warm_policy','warm_fixed')):
        if b not in arms: continue
        left={r['id']:r['oracle']=='valid' for r in arms[a]}; right={r['id']:r['oracle']=='valid' for r in arms[b]}
        paired[a+'_vs_'+b]={'wins':sum(left[k] and not right[k] for k in left),
                            'losses':sum(right[k] and not left[k] for k in left)}
    report={'source_digest':manifest['source_digest'],'task_digest':digest(cases),
            'totals':summary,'paired_completion':paired,'training_seconds':training['seconds'],
            'training_checks':training['schema_checks'],'rules':training['rules'],
            'scope':'240 generated tasks across 3 seeds and 5 polynomial families. Development evaluation, no live LLM. '
                    'Training and evaluation differ in exact tasks and some compositions, not mathematical domain. '
                    'CAS scoring shares the CAS baseline implementation. Separate grid tests cover the eight original stress tasks.'}
    (output/'summary.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline-root',type=Path)
    parser.add_argument('--worker',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args(); root=Path(__file__).resolve().parents[1]
    if args.output.resolve().is_relative_to(root): parser.error('raw records must be outside the public repository')
    if args.worker: baseline_worker(json.loads(args.worker.read_text()),args.output)
    else: study(args.output.resolve(),args.baseline_root.resolve() if args.baseline_root else None)


if __name__=='__main__': main()
