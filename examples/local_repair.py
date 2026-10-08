"""Offline fixture: repair one amount without changing a checked quantity."""
from dataclasses import asdict
import json

from resimind.repair import Edit, RepairProposal, apply_repair, plan_sha256


def main():
    # Synthetic application-owned task and tool evidence, not model assertions.
    quantity, unit_price, budget = 2, 20, 50
    plan = {"quantity": quantity, "unit_price": unit_price, "total": 20}

    def verify(candidate):
        return {
            "requested_quantity": candidate["quantity"] == quantity,
            "catalog_price": candidate["unit_price"] == unit_price,
            "total": candidate["total"] == candidate["quantity"] * unit_price,
            "budget": candidate["total"] <= budget,
        }

    proposal = RepairProposal(plan_sha256(plan), (
        Edit("/total", 20, 40, ("catalog:demo-item", "task:quantity")),
    ))
    result = apply_repair(plan, proposal,
                          evidence_ids={"catalog:demo-item", "task:quantity"}, verifier=verify)
    print(json.dumps({"mode": "offline-synthetic", "original": plan,
                      "repair": asdict(result)}, indent=2))
    return 0 if result.status == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
