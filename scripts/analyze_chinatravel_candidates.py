#!/usr/bin/env python3
"""Post-hoc candidate audit, only after all 36 frozen formal runs have finished.

Reads existing artifacts and writes only ROOT/candidate-analysis.json. No model
client is created. Each proposal round is scored as a complete 12-UID batch by
the frozen official scorer; absent candidates are explicitly marked as padding.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


REPO = Path(__file__).resolve().parents[1]
ARMS = ("official_react", "official_nesy", "resimind_react")
TERMINAL = {"completed", "technical_failure", "budget_exhausted"}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan_hash(plan: dict) -> str:
    content = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def complete_cohort(root: Path) -> tuple[dict, dict, dict]:
    """Fail before importing/scoring gold if any scheduled terminal record is absent."""
    manifest = read(root / "manifest.json")
    require(sha(root / "manifest.json") == (root / "manifest.sha256").read_text().strip(),
            "Frozen manifest checksum mismatch")
    ids = manifest.get("selected_uids", [])
    require(manifest.get("phase") == "formal", "Candidate audit requires a formal cohort")
    require(len(ids) == len(set(ids)) == 12, "Expected exactly 12 distinct formal UIDs")
    require(tuple(manifest.get("arms", ())) == ARMS, "Unexpected frozen arms")
    expected = {(uid, arm) for uid in ids for arm in ARMS}
    scheduled = [(item.get("uid"), item.get("arm")) for item in manifest.get("schedule", [])]
    require(len(scheduled) == 36 and set(scheduled) == expected, "Expected 36 unique scheduled runs")
    paths = {(uid, arm): root / "runs" / uid / arm / "result.json" for uid, arm in expected}
    missing = sorted(str(path.relative_to(root)) for path in paths.values() if not path.is_file())
    require(not missing, "Refusing partial-cohort analysis; missing terminal records: " + ", ".join(missing))
    results, hashes = {}, {}
    for (uid, arm), path in sorted(paths.items()):
        result = read(path)
        require(result.get("uid") == uid and result.get("arm") == arm,
                f"Terminal UID/arm mismatch: {path}")
        require(result.get("status") in TERMINAL, f"Nonterminal result: {path}")
        results[(uid, arm)] = result
        hashes[str(path.relative_to(root))] = sha(path)
    return manifest, results, hashes


def gate_record(path: Path) -> dict:
    if not path.is_file():
        return {"record_present": False, "decision": "missing", "reasons": []}
    gate = read(path)
    accepted, deferred = gate.get("accepted"), gate.get("deferred")
    require(type(accepted) is bool and type(deferred) is bool,
            f"Invalid gate boolean fields: {path}")
    require(not (accepted and deferred), f"Inconsistent gate decision: {path}")
    reasons = gate.get("reasons", [])
    require(isinstance(reasons, list), f"Invalid gate reasons: {path}")
    return {
        "record_present": True,
        "decision": "accepted" if accepted else "deferred" if deferred else "rejected",
        "accepted": accepted, "deferred": deferred, "reasons": reasons,
    }


def load_candidate(worker: Path, attempt: int, root: Path, hashes: dict) -> tuple[dict, dict]:
    path = worker / f"candidate_{attempt:02d}.json"
    gate_path = worker / f"gate_{attempt:02d}.json"
    candidate = read(path) if path.is_file() else None
    if candidate is not None:
        require(type(candidate) is dict and "plan" in candidate, f"Invalid candidate record: {path}")
        require(candidate.get("attempt") == attempt, f"Candidate attempt mismatch: {path}")
        require(candidate["plan"] is None or type(candidate["plan"]) is dict,
                f"Unexpected candidate plan type: {path}")
    plan = (candidate or {}).get("plan")
    # The frozen proposer treats None and {} as no candidate. Malformed nonempty
    # plan dictionaries are actual candidates and must remain in the audit.
    present = type(plan) is dict and bool(plan)
    gate = gate_record(gate_path)
    require(present or not gate["record_present"], f"Gate exists without a candidate: {gate_path}")
    for recorded in (path, gate_path):
        if recorded.is_file():
            hashes[str(recorded.relative_to(root))] = sha(recorded)
    metadata = {
        "attempt": attempt, "record_present": path.is_file(), "present": present,
        "candidate_file": str(path.relative_to(root)) if path.is_file() else None,
        "plan_sha256": plan_hash(plan) if present else None,
        "gate": gate,
    }
    return plan if present else {}, metadata


def classify_case(candidates: list[dict], delivered_plan: dict, delivered_score: dict,
                  terminal_result: dict) -> dict:
    first = candidates[0]
    delivered = bool(delivered_plan)
    delivered_gold_pass = delivered and bool(delivered_score["final"])
    matched = [row["attempt"] for row in candidates if row["present"] and delivered
               and row["plan_sha256"] == plan_hash(delivered_plan)]
    later_accepted_match = any(
        row["attempt"] > 1 and row["attempt"] in matched and row["gold_pass"] is True
        and row["gate"]["decision"] == "accepted" for row in candidates
    )
    repaired = (first["present"] and first["gold_pass"] is False
                and delivered_gold_pass and later_accepted_match)
    withheld = first["present"] and first["gold_pass"] is True and not delivered
    return {
        "terminal_status": terminal_result["status"],
        "terminal_details": terminal_result.get("details", {}),
        "actual_candidate_count": sum(row["present"] for row in candidates),
        "initial_present": first["present"], "initial_gold_pass": first["gold_pass"],
        "initial_gate_decision": first["gate"]["decision"],
        "delivered_present": delivered,
        "delivered_gold_pass": bool(delivered_gold_pass),
        "delivered_plan_sha256": plan_hash(delivered_plan) if delivered else None,
        "delivered_official_score": delivered_score,
        "delivered_matching_candidate_attempts": matched,
        "repaired_success": bool(repaired),
        "valid_initial_withheld": bool(withheld),
        "valid_initial_withheld_initial_gate": first["gate"]["decision"] if withheld else None,
        "candidates": candidates,
    }


def analyze(root: Path, upstream: Path, prepared_data: Path | None = None) -> dict:
    manifest, results, input_hashes = complete_cohort(root)
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "src"))
    from experiments.chinatravel.run import verify
    from experiments.chinatravel.official_scoring import score_batch

    dataset = read(root / "dataset_manifest.json")
    data = (prepared_data.resolve() if prepared_data else
            Path(dataset["sandbox"]["path"]).resolve().parent)
    verify(root, data, upstream)
    ids = manifest["selected_uids"]
    gold = {uid: read(root / "queries/gold" / f"{uid}.json") for uid in ids}
    candidates_by_uid = {uid: [] for uid in ids}
    rounds = {}
    for attempt in (1, 2, 3):
        plans, metadata = {}, {}
        for uid in ids:
            worker = root / "runs" / uid / "resimind_react" / "worker"
            allowed = {f"candidate_{number:02d}.json" for number in (1, 2, 3)}
            require(all(path.name in allowed for path in worker.glob("candidate_*.json")),
                    f"Unexpected candidate round for {uid}")
            plans[uid], metadata[uid] = load_candidate(worker, attempt, root, input_hashes)
        evaluated = score_batch(gold, plans, upstream_root=upstream)
        for uid in ids:
            row = metadata[uid]
            score = evaluated["per_query"][uid]
            row["gold_pass"] = bool(score["final"]) if row["present"] else None
            row["official_score"] = score
            row["score_is_missing_padding"] = not row["present"]
            candidates_by_uid[uid].append(row)
        rounds[str(attempt)] = {
            "batch_query_count": 12,
            "present_count": sum(row["present"] for row in metadata.values()),
            "official_valid_present_count": sum(row["gold_pass"] is True for row in metadata.values()),
            "evaluation": evaluated,
        }
    delivered = {uid: results[(uid, "resimind_react")].get("plan") or {} for uid in ids}
    require(all(type(plan) is dict for plan in delivered.values()), "Unexpected delivered plan type")
    final_evaluation = score_batch(gold, delivered, upstream_root=upstream)
    cases = {
        uid: classify_case(candidates_by_uid[uid], delivered[uid], final_evaluation["per_query"][uid],
                           results[(uid, "resimind_react")])
        for uid in ids
    }
    actual = [row for rows in candidates_by_uid.values() for row in rows if row["present"]]
    summary = {
        "formal_uids": 12, "completed_terminal_records": 36,
        "actual_generated_candidates": len(actual),
        "official_valid_candidates": sum(row["gold_pass"] is True for row in actual),
        "local_accepted_candidates": sum(row["gate"]["decision"] == "accepted" for row in actual),
        "local_accepted_but_gold_failed": sum(row["gate"]["decision"] == "accepted"
                                               and row["gold_pass"] is False for row in actual),
        "gold_valid_explicitly_rejected": sum(row["gold_pass"] is True
                                               and row["gate"]["decision"] == "rejected" for row in actual),
        "gold_valid_explicitly_deferred": sum(row["gold_pass"] is True
                                               and row["gate"]["decision"] == "deferred" for row in actual),
        "gold_valid_explicitly_rejected_or_deferred": sum(
            row["gold_pass"] is True and row["gate"]["decision"] in {"rejected", "deferred"}
            for row in actual),
        "candidate_without_gate_record": sum(not row["gate"]["record_present"] for row in actual),
        "gold_valid_without_gate_record": sum(row["gold_pass"] is True
                                                and not row["gate"]["record_present"] for row in actual),
        "initial_gold_pass": sum(case["initial_gold_pass"] is True for case in cases.values()),
        "delivered_gold_pass": sum(case["delivered_gold_pass"] for case in cases.values()),
        "repaired_success": sum(case["repaired_success"] for case in cases.values()),
        "valid_initial_withheld": sum(case["valid_initial_withheld"] for case in cases.values()),
        "valid_initial_withheld_by_initial_gate": dict(Counter(
            case["valid_initial_withheld_initial_gate"] for case in cases.values()
            if case["valid_initial_withheld"])),
    }
    # Result and candidate artifacts must remain unchanged during this audit.
    verify(root, data, upstream)
    for relative, expected_hash in input_hashes.items():
        require(sha(root / relative) == expected_hash, f"Artifact changed during analysis: {relative}")
    return {
        "schema_version": 1, "analysis_type": "posthoc_resimind_candidate_audit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": sha(root / "manifest.json"),
        "analysis_script_sha256": sha(Path(__file__)),
        "frozen_official_scoring_sha256": manifest["source_sha256"]["experiments/chinatravel/official_scoring.py"],
        "upstream_commit": manifest["upstream_commit"], "input_artifact_sha256": input_hashes,
        "definitions": {
            "present": "A saved nonempty plan dict actually emitted by the frozen proposer; None/{} is missing padding.",
            "gold_pass": "Independent official schema/common-sense/gold-logic conjunction; null when no candidate exists.",
            "repaired_success": "Initial real candidate fails gold; a later gold-valid candidate is explicitly gate-accepted, equals the delivered plan, and final delivery passes gold.",
            "valid_initial_withheld": "Initial real candidate passes gold but the terminal run delivers no plan; subtyped by initial gate state.",
            "missing_gate": "No recorded local gate result; not evidence of explicit rejection or deferral.",
        },
        "limitations": [
            "This audit is post hoc and never feeds gold scores into generation.",
            "Rejected, deferred, missing, and locally accepted outputs are not official successes unless gold evaluation passes.",
            "A gold-valid candidate rejected/deferred locally may expose a stricter or mistranslated local contract; it is not proof of complete natural-language correctness.",
            "This 12-task single-run cohort has no shared initial candidate across arms and cannot isolate a causal residual-mechanism effect.",
            "Per-round batch metrics include missing {} padding for all 12 UIDs; candidate validity counts exclude that padding.",
        ],
        "summary": summary, "cases": cases, "proposal_rounds": rounds,
        "delivered_evaluation": final_evaluation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--upstream", required=True, type=Path)
    parser.add_argument("--data", type=Path, help="Prepared data directory when relocated from the original host")
    args = parser.parse_args()
    root = args.root.resolve()
    report = analyze(root, args.upstream.resolve(), args.data)
    output = root / "candidate-analysis.json"
    temporary = root / "candidate-analysis.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"output": str(output), "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
