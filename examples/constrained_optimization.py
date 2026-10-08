"""Run a rational QP Agent with feasibility, KKT and a global proof certificate."""
from __future__ import annotations

import argparse
import json

from resimind.domains.optimization import GLOBAL_FACT, KKT_FACT, PRIMAL_FACT, run_demo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the complete evidence/decision audit")
    args = parser.parse_args()
    result = run_demo()
    if args.json:
        print(result.to_json())
        return
    run = result.run_result
    print("ResiMind | exact constrained optimization")
    print("min 2*x1^2 + x1*x2 + x2^2 + x3^2 - 8*x1 - 3*x2 - 3*x3")
    print("subject to x1+x2+x3=3, x>=0, x1<=1")
    for event in run.trace:
        print(f"  {event.step}. {event.decision.value.upper():6} {event.candidate.action if event.candidate else 'setup'}: "
              f"{', '.join(event.reasons)} (remaining={event.residual_after.measure})")
        if event.decision.value == "reject" and event.candidate:
            print(f"     rejected x = {json.loads(event.candidate.claim).get('primal', {}).get('x')}")
    facts = {fact.id: json.loads(fact.value) for fact in run.state.facts}
    if run.status == "solved":
        primal, kkt, proof = facts[PRIMAL_FACT], facts[KKT_FACT], facts[GLOBAL_FACT]
        print(f"Unique global minimizer: x = {primal['x']}")
        print(f"Exact objective: {primal['objective']} | lambda = {kkt['lambda']} | mu = {kkt['mu']}")
        print(f"Global certificate: {len(proof['weights'])} positive weighted squares + constraint terms")
    print(f"Status: {run.status}; outstanding obligations: {run.residual.measure}")


if __name__ == "__main__":
    main()
