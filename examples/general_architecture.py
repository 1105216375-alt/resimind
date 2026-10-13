"""Run the provider-neutral architecture extensions without external services."""

from __future__ import annotations

import json

from resimind import (
    Obligation,
    ObligationGraph,
    ObligationUpdate,
    StrategyObservation,
    StrategyPolicy,
    VerifiedExample,
    apply_rule,
    extract_rules,
)


def run_demo() -> dict:
    graph = ObligationGraph((
        Obligation("input", "collect input", evidence=("e1",)),
        Obligation("derive", "derive result", depends_on=("input",)),
        Obligation("report", "prepare report", depends_on=("derive",)),
    ))
    ready_before = [item.id for item in graph.ready()]
    graph = graph.update(ObligationUpdate("input", "verified"))
    ready_after = [item.id for item in graph.ready()]

    policy = StrategyPolicy.fit((
        StrategyObservation("algebra", "symbolic", True, 1.0),
        StrategyObservation("algebra", "symbolic", True, 1.0),
        StrategyObservation("algebra", "model", False, 0.5),
        StrategyObservation("algebra", "model", True, 4.0),
    ))
    rules = extract_rules((
        VerifiedExample("(x+y)*z", "x*z+y*z", ("x", "y", "z"), "case-a"),
        VerifiedExample("(p+q)*r", "p*r+q*r", ("p", "q", "r"), "case-b"),
    ))
    applied = apply_rule(rules[0], "(u+v)*w", ("u", "v", "w"), ("u", "v", "w"))
    return {
        "ready_before": ready_before,
        "ready_after": ready_after,
        "strategy_order": list(policy.rank("algebra", ("model", "symbolic"))),
        "rule_id": rules[0].id,
        "applied_rule": applied,
        "policy_fingerprint": policy.fingerprint(),
    }


def main() -> int:
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
