"""Apply grounded local edits without using gold, scoring, or a model client."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from resimind.repair import apply_repair, plan_sha256
from .binding import OfficialTools, propose_repairs
from .checker import LocalContract


def repair_plan(plan: dict, translation: dict, upstream: Path, *, max_rounds=3, tools=None,
                public_query=None) -> dict:
    """Return a checked draft, separately from the locally deliverable plan.

    The supplied translation must come from the solving model/public request,
    never from benchmark gold. A draft with unresolved checks is not delivery.
    Every patch is independently checked against all of the local obligations.
    """
    if type(max_rounds) is not int or not 1 <= max_rounds <= 8:
        raise ValueError("max_rounds must be between one and eight")
    tools = tools or OfficialTools()
    verifier = LocalContract(translation, upstream, tools=tools, public_query=public_query)
    draft = deepcopy(plan)
    initial = verifier(draft)
    trace, evidence, unresolved = [], {}, []
    stop = "repair_budget"
    if initial.get("translation_valid") is not True:
        stop = "translation_needs_regeneration"
    elif any(initial.get("task/" + key) is not True
             for key in ("start_city", "target_city", "people_number", "days")):
        stop = "task_envelope_mismatch"
    else:
        for _ in range(max_rounds):
            current = verifier(draft)
            if current and all(value is True for value in current.values()):
                stop = "local_contract_passed"
                break
            proposed = propose_repairs(draft, translation, tools=tools)
            evidence.update(proposed.evidence)
            unresolved.extend(proposed.unresolved)
            progressed = False
            for proposal in proposed.proposals:
                result = apply_repair(draft, proposal, evidence_ids=proposed.evidence,
                                      verifier=verifier, max_edits=128)
                trace.append({"proposal": asdict(proposal), "result": asdict(result)})
                if result.status in {"repaired", "accepted"}:
                    draft = result.plan
                    progressed = True
                    break  # Remaining proposals refer to the old draft hash.
            if not progressed:
                stop = "no_verified_local_progress"
                break
    final = verifier(draft)
    accepted = bool(final) and all(value is True for value in final.values())
    if accepted:
        stop = "local_contract_passed"
    return {
        "scope": "self_translated_local_contract_only",
        "initial_sha256": plan_sha256(plan), "draft_sha256": plan_sha256(draft),
        "status": "accepted" if accepted else "unfinished", "stop_reason": stop,
        "plan": draft if accepted else None, "draft": draft,
        "initial_checks": initial, "final_checks": final,
        "resolved": sorted(key for key in initial if initial[key] is not True and final.get(key) is True),
        "regressed": sorted(key for key in initial if initial[key] is True and final.get(key) is not True),
        "transactions": trace, "evidence": evidence, "unresolved": unresolved,
        "diagnostics": verifier.last_diagnostics,
    }
