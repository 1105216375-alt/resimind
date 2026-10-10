"""Reproducible offline development benchmark; no model or framework ranking.

Run from a source checkout with ``PYTHONPATH=src:. python -m
benchmarks.run_unified_verification_benchmark --output /tmp/resimind-benchmark``.
Fixtures are public synthetic development cases, not a held-out sample. The
manifest, per-case measurements and aggregate report contain no model traces.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, timedelta
from fractions import Fraction
import hashlib
import json
from math import ceil
from pathlib import Path
from statistics import mean
import tempfile

from benchmarks.knowledge_growth import score_expansion
from benchmarks.unified_bridge_oracle import score_bridge
from resimind import __version__
from resimind.agent import Task
from resimind.core import Decision
from resimind.domains import bridge, customer_support as support
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import DOMAIN, PolynomialProblem, WorkCounts
from resimind.knowledge import KnowledgeLibrary

PROTOCOL = "resimind-unified-offline-v1"
DISCOVERY = tuple(f"(u+v)**{n}" for n in range(2, 7))
STRESS = (
    "(x+y)**12", "(x+y)**8*(2*x-3*y+1)**4",
    "((x+y)**4+(x-y)**4)**2", "(x+y+1)**10",
    "(x+y)**8*(x-y)**8", "((x+y)**3+(2*x-y)**3)**3",
    "(x+y+1)**6*(2*x-y+1)**6", "(x+y)**16",
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def make_manifest(limit=50):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("cases per domain must be an integer from 1 through 50")
    math_cases, bridge_cases, support_cases = [], [], []
    for i in range(50):
        n, a, b = 2 + i % 5, 1 + i % 3, 1 + i % 4
        expressions = (
            f"({a}*x-{b}*y)**{n}",
            f"({a}*x+{b}*y)**{n}*(2*x-y)",
            f"({a}*x-{b}*y+1)**{2+i%3}",
            f"(x/{a+1}+y/{b+1})**{n}",
            f"(x+{a}*y)**{n}-(x-{b}*y)**{n}",
        )
        math_cases.append({"id": f"math-{i:03}", "expression": expressions[i // 10],
                           "variables": ["x", "y"], "family": i // 10})
        problem = replace(bridge.demo_problem(), subject=f"benchmark-bridge-{i:03}",
                          scope=f"unified-v1-{i:03}", spans=(18+i%11, 24+(3*i)%13),
                          flexural_rigidities=(24_000_000_000+i*500_000_000,
                                              36_000_000_000+i*700_000_000),
                          dead_loads=(20+i%17, 25+i%13), live_loads=(15+i%19, 20+i%11),
                          moment_limit=3500+100*i, midspan_deflection_limit=15+i%30)
        bridge_cases.append({"id": f"bridge-{i:03}", "problem": asdict(problem)})
        days = (0, 14, 15, 7, 7, 7, 1, 3, 10, 30)[i % 10]
        paid = 10000+137*i
        case = replace(support.demo_case(), case_id=f"return-{i:03}",
                       delivered_on=(date(2026, 10, 8)-timedelta(days=days)).isoformat(),
                       item_paid_cents=paid, shipping_paid_cents=0 if i%10 == 6 else 100+23*i,
                       prior_item_refund_cents=paid if i%10 == 5 else paid//3 if i%10 == 8 else 0,
                       item_returnable=i%10 != 3,
                       request_customer_id="different-customer" if i%10 == 4 else "demo-customer-001")
        support_cases.append({"id": f"support-{i:03}", "case": asdict(case)})
    return {"protocol": PROTOCOL, "cases_per_domain": limit,
            "mathematics": math_cases[:limit], "bridge": bridge_cases[:limit],
            "customer_support": support_cases[:limit],
            "discovery": list(DISCOVERY), "stress": list(STRESS),
            "settings": {"model": None, "lean": False, "max_steps_math": 64,
                         "max_local_work": 4096, "primary_overflow": 0,
                         "stress_overflow_arms": [0, 4096],
                         "mutations_per_completed_case": 3,
                         "missing_evidence_cases_per_domain": 10}}


def bridge_problem(data):
    data = dict(data)
    for key in ("spans", "flexural_rigidities", "dead_loads", "live_loads"):
        data[key] = tuple(data[key])
    data["combinations"] = tuple(bridge.LoadCombination(**c) for c in data["combinations"])
    return bridge.ContinuousBridgeProblem(**data)


def return_case(data):
    return support.ReturnCase(**{**data, "policy": support.ReturnPolicy(**data["policy"])})


def score_support(case, result):
    """Independent policy calculation; do not call the domain's policy helpers."""
    if case.delivered_on is None:
        return False
    if (case.request_order_id, case.request_customer_id) != (case.order_id, case.customer_id):
        reason = "request_identity_mismatch"
    elif not case.item_returnable:
        reason = "item_not_returnable"
    elif (date.fromisoformat(case.requested_on)-date.fromisoformat(case.delivered_on)).days > case.policy.window_days:
        reason = "outside_return_window"
    else:
        reason = "within_return_window"
    eligible = reason == "within_return_window"
    expected = {"case_id": case.case_id, "order_id": case.request_order_id,
                "customer_id": case.request_customer_id, "policy_id": case.policy.policy_id,
                "reason": reason, "currency": "CNY", "payment_executed": False,
                "outcome": "refund_recommended" if eligible else "human_review",
                "next_action": "merchant_refund_review" if eligible else "manual_policy_review",
                "refund_cents": case.item_paid_cents-case.prior_item_refund_cents if eligible else None,
                "excluded_shipping_cents": None if reason == "request_identity_mismatch" else case.shipping_paid_cents}
    return canonical(result) == canonical(expected)


def measurement(identifier, run, oracle):
    solved = run.status == "solved" and run.residual.solved
    return {"id": identifier, "status": run.status, "stop_reason": run.stop_reason,
            "reported_solved": solved, "oracle": oracle,
            "correct_completion": solved and oracle == "valid", "model_calls": 0,
            "attempts": run.steps,
            "accepted_steps": sum(e.step > 0 and e.decision is Decision.ACCEPT for e in run.trace),
            "rejected_steps": sum(e.step > 0 and e.decision is Decision.REJECT for e in run.trace),
            "remaining_obligations": run.residual.measure}


def tamper_check(event, evidence, verifier, payloads):
    """A positive control prevents a reject-everything checker passing this test."""
    def verify(candidate):
        return verifier.verify(candidate, event.before, event.residual_before, evidence).decision
    control = verify(event.candidate) is Decision.ACCEPT
    decisions = [verify(replace(event.candidate, claim=canonical(p))).value for p in payloads]
    return {"positive_control_accepted": control, "mutations": len(payloads),
            "rejected": decisions.count("reject"), "false_accepts": decisions.count("accept"),
            "deferred_or_other": sum(d not in ("accept", "reject") for d in decisions)}


def math_run(case, library=None, *, overflow=0, mutations=False):
    library = KnowledgeLibrary({DOMAIN: AlgebraVerifier()}) if library is None else library
    problem = PolynomialProblem(case["expression"], tuple(case["variables"]))
    task = Task(case["id"], "Expand the polynomial", DOMAIN)
    stats = AdaptiveStats()
    counts = WorkCounts()
    learner = build_adaptive_learning_agent(problem, library, stats=stats, counts=counts,
                                             max_local_work_overflow=overflow)
    # Explicitly freeze retrieval during evaluation; discovery is a separate track.
    outcome = learner.run(task, learn=False)
    run = outcome.result.run_result
    final = run.state.facts[-1].value[1] if run.state.facts else problem.expression
    oracle = score_expansion(problem.expression, final, problem.variables)["status"]
    row = measurement(case["id"], run, oracle)
    row.update(model_calls=stats.model_calls, rule_accepts=stats.rule_accepts,
               overflow_grants=stats.local_work_overflow_grants, overflow_spent=stats.local_work_overflow_spent,
               work_counts=asdict(counts), scheduling_preview_nodes=stats.scheduling_preview_nodes,
               rule_preview_pattern_attempts=stats.rule_preview_pattern_attempts,
               adaptive_stop_reason=stats.stop_reason, overflow_denials=stats.local_work_overflow_denials,
               diagnostic_counts=dict(stats.diagnostic_counts),
               final_selection_reason=(stats.scheduling_decisions[-1]["selection_reason"]
                                       if stats.scheduling_decisions else None))
    if mutations and row["correct_completion"]:
        event = next(e for e in run.trace if e.decision is Decision.ACCEPT)
        payload = json.loads(event.candidate.claim)
        variants = [{**payload, "after": f"({payload['after']})+({delta})"} for delta in ("1", "-1", "x")]
        # A fresh controller avoids treating the replayed positive control as a cycle.
        fresh = learner.agent_factory(task, ())
        fresh.proposer_factory(task, (), run.evidence)[0].propose(event.before, event.residual_before)
        verifier = fresh.verifier_factory(task)
        row["tamper"] = tamper_check(event, run.evidence, verifier, variants)
    return row


def bridge_run(case):
    problem = bridge_problem(case["problem"])
    result = bridge.build_agent(problem).run(Task(case["id"], "Check the line beam certificate", bridge.DOMAIN))
    run = result.run_result
    valid = score_bridge(problem, bridge.verified_case_results(result), bridge.verified_summary(result))
    row = measurement(case["id"], run, "valid" if valid else "invalid")
    if row["correct_completion"]:
        event = next(e for e in run.trace if e.decision is Decision.ACCEPT and e.candidate.action == bridge.ACTIONS[1])
        payload = json.loads(event.candidate.claim)
        variants = []
        for key in ("pier_moment", "reactions", "v1"):
            altered = json.loads(canonical(payload))
            if key == "pier_moment":
                altered[key] = str(Fraction(altered[key])+1)
            else:
                altered[key][0] = str(Fraction(altered[key][0])+1)
            variants.append(altered)
        row["tamper"] = tamper_check(event, run.evidence, bridge.BridgeVerifier(problem), variants)
    return row


def support_run(case):
    problem = return_case(case["case"])
    result = support.build_agent(problem).run(Task(case["id"], "Assess the return", support.DOMAIN))
    run = result.run_result
    # Read the committed claim directly so scoring does not reuse verified_resolution.
    resolution = next((json.loads(f.value) for f in run.state.facts if f.id == support.RESOLUTION_FACT), {})
    row = measurement(case["id"], run, "valid" if score_support(problem, resolution) else "invalid")
    if row["correct_completion"]:
        event = next(e for e in run.trace if e.decision is Decision.ACCEPT and e.candidate.action == support.ACTIONS[2])
        variants = [{**resolution, "payment_executed": True},
                    {**resolution, "refund_cents": (resolution["refund_cents"] or 0)+1},
                    {**resolution, "customer_id": "unauthorized-customer"}]
        row["tamper"] = tamper_check(event, run.evidence, support.CustomerSupportVerifier(problem), variants)
    return row


def aggregate(rows):
    attempts = sorted(row["attempts"] for row in rows)
    tampers = [r["tamper"] for r in rows if "tamper" in r]
    return {"cases": len(rows), "correct_completions": sum(r["correct_completion"] for r in rows),
            "correct_completion_rate": mean(r["correct_completion"] for r in rows),
            "false_solved": sum(r["reported_solved"] and r["oracle"] in ("invalid", "incomplete") for r in rows),
            "unscored_solved": sum(r["reported_solved"] and r["oracle"] == "unknown" for r in rows),
            "oracle_statuses": dict(sorted(Counter(r["oracle"] for r in rows).items())),
            "model_calls": sum(r["model_calls"] for r in rows),
            "mean_attempts": mean(attempts), "p95_attempts": attempts[ceil(.95*len(attempts))-1],
            "mean_accepted_steps": mean(r["accepted_steps"] for r in rows),
            "work_totals": {key: sum(r.get("work_counts", {}).get(key, 0) for r in rows)
                            for key in sorted({key for r in rows for key in r.get("work_counts", {})})},
            "rejected_steps": sum(r["rejected_steps"] for r in rows),
            "positive_controls": len(tampers),
            "positive_controls_accepted": sum(t["positive_control_accepted"] for t in tampers),
            "mutations": sum(t["mutations"] for t in tampers),
            "mutations_rejected": sum(t["rejected"] for t in tampers),
            "mutation_false_accepts": sum(t["false_accepts"] for t in tampers),
            "mutations_deferred_or_other": sum(t["deferred_or_other"] for t in tampers)}


def paired_summary(before, after):
    if [r["id"] for r in before] != [r["id"] for r in after]:
        raise ValueError("paired rows must refer to the same ordered cases")
    pairs = list(zip(before, after))
    deltas = [a["accepted_steps"]-b["accepted_steps"] for a, b in pairs
              if a["correct_completion"] and b["correct_completion"]]
    return {"newly_completed": sum(not a["correct_completion"] and b["correct_completion"] for a, b in pairs),
            "lost_completions": sum(a["correct_completion"] and not b["correct_completion"] for a, b in pairs),
            "both_completed": len(deltas), "fewer_steps": sum(d > 0 for d in deltas),
            "same_steps": deltas.count(0), "more_steps": sum(d < 0 for d in deltas),
            "mean_steps_saved_on_both_completed": mean(deltas) if deltas else None}


def missing_evidence_track():
    rows = []
    for i in range(10):
        problem = replace(bridge.demo_problem(), subject=f"missing-{i}", spans=(None, 24+i))
        result = bridge.build_agent(problem).run(Task(f"missing-bridge-{i}", "Check bridge", bridge.DOMAIN))
        run = result.run_result
        rows.append({"domain": "bridge", "id": i, "safe_abstention": run.status != "solved"
                     and bool(run.residual.unknowns) and not run.state.facts})
        case = replace(support.demo_case(), case_id=f"missing-{i}", delivered_on=None, item_paid_cents=10000+i)
        result = support.build_agent(case).run(Task(f"missing-support-{i}", "Assess return", support.DOMAIN))
        run = result.run_result
        rows.append({"domain": "customer_support", "id": i, "safe_abstention": run.status != "solved"
                     and support.DELIVERY_UNKNOWN in run.residual.unknowns and not run.state.facts})
    return rows


def run_benchmark(limit=50):
    manifest = make_manifest(limit)
    rows = {"mathematics": [math_run(c, mutations=True) for c in manifest["mathematics"]],
            "bridge": [bridge_run(c) for c in manifest["bridge"]],
            "customer_support": [support_run(c) for c in manifest["customer_support"]]}
    library = KnowledgeLibrary({DOMAIN: AlgebraVerifier()})
    discovery = []
    for i, expression in enumerate(DISCOVERY):
        stats = AdaptiveStats()
        counts = WorkCounts()
        outcome = build_adaptive_learning_agent(PolynomialProblem(expression, ("u", "v")), library,
                                                stats=stats, counts=counts).run(Task(f"discovery-{i}", "Expand", DOMAIN))
        discovery.append({"id": f"discovery-{i}", "accepted_steps": len(outcome.result.run_result.state.facts),
                          "model_calls": stats.model_calls, "work_counts": asdict(counts),
                          "verified_admissions": sum(r.status == "verified" for r in outcome.admissions)})
    with tempfile.TemporaryDirectory(prefix="resimind-unified-library-") as directory:
        path = Path(directory) / "rules.json"
        library.save(path)
        library = KnowledgeLibrary.load(path, {DOMAIN: AlgebraVerifier()})
        library.save(path)
        frozen = path.read_bytes()
        warm = [math_run(c, library) for c in manifest["mathematics"]]
        library.save(path)
        library_unchanged = frozen == path.read_bytes()
        if not library_unchanged:
            raise RuntimeError("transfer evaluation modified the frozen library")
    stress = {str(overflow): [math_run({"id": f"stress-{i}", "expression": e, "variables": ["x", "y"]},
                                      overflow=overflow) for i, e in enumerate(STRESS)] for overflow in (0, 4096)}
    missing = missing_evidence_track()
    report = {"protocol": PROTOCOL, "resimind_version": __version__,
              "manifest_sha256": hashlib.sha256(canonical(manifest).encode()).hexdigest(),
              "scope": "offline synthetic development fixtures; no neural model, Lean or external framework evaluated",
              "primary": {key: aggregate(value) for key, value in rows.items()},
              "math_reuse": {"discovery": discovery, "library_unchanged": library_unchanged,
                             "library_sha256": hashlib.sha256(frozen).hexdigest(),
                             "cold": aggregate(rows["mathematics"]),
                             "warm": aggregate(warm), "paired": paired_summary(rows["mathematics"], warm),
                             "warm_rule_applications": sum(r["rule_accepts"] for r in warm)},
              "stress_overflow_ablation": {key: aggregate(value) for key, value in stress.items()},
              "stress_paired": paired_summary(stress["0"], stress["4096"]),
              "missing_evidence": {"cases": len(missing), "safe_abstentions": sum(r["safe_abstention"] for r in missing)}}
    details = {"primary": rows, "warm_math": warm, "stress": stress, "missing_evidence": missing}
    return manifest, report, details


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-per-domain", type=int, default=50)
    parser.add_argument("--output", type=Path, help="write manifest.json, summary.json, measurements.json here")
    args = parser.parse_args(argv)
    manifest, report, details = run_benchmark(args.cases_per_domain)
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        for name, data in (("manifest", manifest), ("summary", report), ("measurements", details)):
            (args.output / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
