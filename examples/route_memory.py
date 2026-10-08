"""Reviewed memory selects an action; current evidence still determines answers.

Run with ``python -m examples.route_memory``. All evidence is synthetic.
"""

from __future__ import annotations

import json

from resimind import Engine, RunResult
from resimind.memory import Route, RouteMemory

from .inventory import (
    ACTION, INPUTS, InventoryDomain, InventoryProposer, InventoryVerifier,
    make_evidence,
)


def run_demo() -> dict:
    memory = RouteMemory(registered_actions=(ACTION,))
    memory.add(Route(
        "inventory-balance-v1", "inventory", (("workflow", "stock-balance"),),
        (ACTION,), INPUTS,
    ))
    query = {
        "domain": "inventory", "context": {"workflow": "stock-balance"},
        "available_metrics": INPUTS,
    }
    pending = memory.suggest(**query)
    memory.review("inventory-balance-v1", reviewer="synthetic-reviewer",
                  reason="The registered action recomputes the stock equation.")
    approved = memory.suggest(**query)

    def run_current_data(opening: int, received: int, dispatched: int) -> RunResult:
        evidence = make_evidence(opening, received, dispatched)
        # Memory supplies only a registered action name. The proposer calculates
        # a fresh candidate, and the verifier checks it against these inputs.
        proposer = InventoryProposer(evidence, wrong_first=False,
                                     action=approved[0].actions[0])
        return Engine(InventoryDomain(), InventoryVerifier(), (proposer,),
                      evidence=evidence).run()

    previous = run_current_data(10, 6, 4)
    current = run_current_data(20, 3, 9)
    memory.revoke("inventory-balance-v1", reviewer="synthetic-reviewer",
                  reason="Demonstrate immediate removal from future suggestions.")
    return {
        "pending_routes": [route.route_id for route in pending],
        "approved_routes": [route.route_id for route in approved],
        "revoked_routes": [route.route_id for route in memory.suggest(**query)],
        "previous_run": previous.to_dict(),
        "current_run": current.to_dict(),
        "memory_audit": memory.audit_log(),
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2, ensure_ascii=False))
