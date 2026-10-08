"""Rebuild README figures from verified demo results.

Developer utility only: install matplotlib, then run with PYTHONPATH=src.
The resimind package itself has no matplotlib/numpy runtime dependency.
"""
from fractions import Fraction
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
import numpy as np

from resimind.domains.optimization import demo_problem as qp_problem, run_demo as qp_run, PRIMAL_FACT
from resimind.domains.bridge import demo_problem as bridge_problem, run_demo as bridge_run, verified_case_results, verified_summary

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / 'assets'
NAVY, CYAN, RED, MUTED = '#10233f', '#008b91', '#cf4a57', '#64758a'
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 10,
                     'axes.spines.top': False, 'axes.spines.right': False,
                     'axes.labelcolor': NAVY, 'text.color': NAVY,
                     'svg.fonttype': 'none', 'svg.hashsalt': 'resimind-v040'})


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f'{name}.svg', bbox_inches='tight', facecolor='white', metadata={'Date': None})
    fig.savefig('/tmp/' + f'resimind-{name}.png', bbox_inches='tight', facecolor='white', dpi=150)
    plt.close(fig)


def optimization():
    problem, result = qp_problem(), qp_run()
    assert result.run_result.status == 'solved'
    witness = json.loads(next(f.value for f in result.run_result.state.facts if f.id == PRIMAL_FACT))
    xstar = np.array([float(Fraction(x)) for x in witness['x']])
    rejected = next(e for e in result.run_result.trace if e.decision.value == 'reject')
    proposal = json.loads(rejected.candidate.claim)
    # The atomic witness may wrap its primal fields to keep feasibility + KKT together.
    if 'primal' in proposal:
        proposal = proposal['primal']
    wrong = np.array([float(Fraction(x)) for x in proposal['x']])
    q = np.array([[float(Fraction(v)) for v in row] for row in problem.q])
    c = np.array([float(Fraction(v)) for v in problem.c])
    assert problem.a == (('1', '1', '1'),) and problem.b == ('3',)
    assert problem.g == (('-1', '0', '0'), ('0', '-1', '0'), ('0', '0', '-1'), ('1', '0', '0'))
    assert problem.h == ('0', '0', '0', '1')
    xx, yy = np.meshgrid(np.linspace(-.2, 2.15, 220), np.linspace(-.2, 3.25, 260))
    pts = np.stack((xx, yy, 3-xx-yy), axis=-1)
    zz = .5*np.einsum('...i,ij,...j->...', pts,q,pts) + np.einsum('...i,i->...',pts,c)
    fig, (ax, note) = plt.subplots(1,2,figsize=(11.8,5.2),gridspec_kw={'width_ratios':[1.25,1]})
    fig.suptitle('A LOWER OBJECTIVE IS NOT ENOUGH',x=.06,y=1.03,ha='left',fontsize=19,fontweight='bold')
    ax.set_facecolor('#f4f7fa')
    ax.add_patch(Polygon([(0,0),(1,0),(1,2),(0,3)],facecolor='#b4efdc',alpha=.6,edgecolor=CYAN,lw=2,zorder=1))
    contours=ax.contour(xx,yy,zz,levels=[-10,-9.125,-8,-6,-3,0,5,10],colors='#8295ac',linewidths=.8,zorder=2)
    ax.clabel(contours,inline=True,fontsize=8,fmt='%g')
    ax.axvline(1,color=CYAN,lw=1.2,ls='--')
    ax.plot(*xstar[:2],marker='*',ms=18,color=CYAN,markeredgecolor='white',zorder=5)
    ax.annotate('Certified optimum',xy=xstar[:2],xytext=(.16,1.3),color=CYAN,fontweight='bold',arrowprops={'arrowstyle':'->','color':CYAN})
    ax.plot(*wrong[:2],marker='X',ms=11,color=RED,markeredgecolor='white',zorder=5)
    ax.annotate('Rejected: x1 > 1',xy=wrong[:2],xytext=(1.18,.66),color=RED,fontweight='bold',arrowprops={'arrowstyle':'->','color':RED})
    ax.text(.1,2.6,'FEASIBLE',color=CYAN,fontweight='bold',fontsize=9)
    ax.set(xlim=(-.2,2.15),ylim=(-.2,3.25),xlabel='x1',ylabel='x2',title='Objective contours on x3 = 3 − x1 − x2')
    note.axis('off')
    note.text(.02,.93,'Exact constrained optimization',fontsize=15,fontweight='bold',va='top')
    note.text(.02,.81,'Candidate with lower objective\n(26/15, 1/5, 16/15)',fontsize=12,color=RED,linespacing=1.5,va='top')
    note.text(.02,.64,'Violates the bound. Nothing is committed.',fontsize=10,color=MUTED,va='top')
    note.text(.02,.49,'Verified optimum',fontsize=11,color=CYAN,fontweight='bold',va='top')
    note.text(.02,.38,'x* = (1, 3/4, 5/4)\nf(x*) = −73/8',fontsize=18,fontweight='bold',linespacing=1.5,va='top')
    note.text(.02,.14,'Positive definiteness + feasibility + KKT\n+ exact global gap identity',fontsize=11,linespacing=1.5,va='top')
    fig.text(.06,-.02,'ResiMind  /  Rational certificates, verified independently of the proposal search.',color=MUTED,fontsize=10)
    fig.subplots_adjust(wspace=.27)
    save(fig,'optimization')


def continuous_bridge():
    problem, result = bridge_problem(), bridge_run()
    assert result.run_result.status == 'solved'
    cases, summary = verified_case_results(result), verified_summary(result)
    assert problem.span_unit == 'm' and problem.rigidity_unit == 'Nm2'
    lengths = [float(Fraction(x)) for x in problem.spans]
    total=sum(lengths)
    fig,(beam,ax)=plt.subplots(2,1,figsize=(11.8,6.4),gridspec_kw={'height_ratios':[.85,2.2]})
    fig.suptitle('ONE BRIDGE. EIGHT LOAD CASES.',x=.06,y=.99,ha='left',fontsize=19,fontweight='bold')
    beam.set_xlim(0,total);beam.set_ylim(-1.9,2.8);beam.axis('off')
    beam.plot([0,total],[0,0],color=NAVY,lw=5)
    for x,label in ((0,'A'),(lengths[0],'B'),(total,'C')):
        beam.add_patch(Polygon([(x,0),(x-.65,-.55),(x+.65,-.55)],facecolor='white',edgecolor=NAVY,lw=1.8,clip_on=False))
        beam.text(x,-1.05,label,ha='center',fontweight='bold')
    for x in np.linspace(1,total-1,24):
        beam.annotate('',xy=(x,.12),xytext=(x,1.02),arrowprops={'arrowstyle':'->','color':CYAN,'lw':1})
    beam.text(total/2,1.42,'Dead load + live patterns: none / left / right / both',ha='center',fontsize=11)
    beam.text(lengths[0]/2,-1.6,f'{lengths[0]:g} m   |   EI = {float(problem.flexural_rigidities[0])/1e9:g} GN·m²',ha='center',color=MUTED)
    beam.text(lengths[0]+lengths[1]/2,-1.6,f'{lengths[1]:g} m   |   EI = {float(problem.flexural_rigidities[1])/1e9:g} GN·m²',ha='center',color=MUTED)
    local=[np.linspace(0,L,201) for L in lengths]
    coords=np.concatenate((local[0],lengths[0]+local[1]))
    curves=[]
    for case,data in cases.items():
        mb=float(data['pier_moment'])
        values=[]
        for i,xx in enumerate(local):
            m0,m1=(0,mb) if i==0 else (mb,0)
            L=lengths[i]
            values.append((m0*(1-xx/L)+m1*xx/L+float(data['q'][i])*xx*(L-xx)/2)/1000)
        curve=np.concatenate(values);curves.append(curve)
        ax.plot(coords,curve,color=CYAN if case.startswith('amplified') else '#a8b6c5',alpha=.6,lw=1,ls='-' if case.startswith('amplified') else '--')
    curves=np.array(curves)
    ax.fill_between(coords,curves.min(axis=0),curves.max(axis=0),color='#b4efdc',alpha=.55,label='Eight-case moment envelope')
    ax.plot(coords,curves.min(axis=0),color=NAVY,lw=1.9)
    ax.plot(coords,curves.max(axis=0),color=NAVY,lw=1.9)
    ax.axhline(0,color=MUTED,lw=.8)
    ax.axvline(lengths[0],color=MUTED,ls=':',lw=.8)
    for i,item in enumerate(summary['envelope']['positive_max']):
        x=float(Fraction(item['x']))+(lengths[0] if i else 0)
        m=float(Fraction(item['value']))/1000
        ax.plot(x,m,'o',color=CYAN,ms=6)
        ax.annotate(f'+{m:,.1f} kN·m\n{item["case"]}',(x,m),xytext=(0,12),textcoords='offset points',ha='center',fontsize=9,color=CYAN,fontweight='bold')
    item=summary['envelope']['pier_min'];mb=float(Fraction(item['value']))/1000
    ax.plot(lengths[0],mb,'o',color=RED,ms=6)
    ax.annotate(f'{mb:,.1f} kN·m  |  {item["case"]}',(lengths[0],mb),xytext=(13,12),textcoords='offset points',fontsize=10,color=RED,fontweight='bold')
    ax.set(xlim=(0,total),ylim=(mb*1.16,8500),xlabel='Distance from abutment A (m)',ylabel='Bending moment (kN·m)')
    ax.grid(axis='y',alpha=.16)
    ax.legend(loc='lower left',frameon=False)
    fig.text(.07,.008,'Synthetic continuous line beam  /  Sagging +, hogging −  /  Exact extrema; curves sampled only for display.',color=MUTED,fontsize=9)
    fig.subplots_adjust(hspace=.3,top=.89,bottom=.12)
    save(fig,'continuous-bridge')


if __name__ == '__main__':
    optimization()
    continuous_bridge()
    print('Saved verified showcase SVG figures to',OUT)
