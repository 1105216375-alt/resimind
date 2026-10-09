"""Dependency-free installed-package entry point for reproducible Agent demos."""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
from typing import Sequence

from .agent import AgentResult
from .domains import bridge, customer_support, optimization, planning


def _trace(result: AgentResult) -> None:
    for event in result.run_result.trace:
        action = event.candidate.action if event.candidate is not None else "setup"
        change = f"{event.before.revision}->{event.after.revision}"
        unchanged = " (no committed change)" if event.before == event.after else ""
        print(f"  {event.step}. {event.decision.value.upper():6} {action}")
        print(f"     {', '.join(event.reasons)} | remaining={event.residual_after.measure} | "
              f"state_revision={change}{unchanged}")


def _optimization_answer(result: AgentResult) -> None:
    # Only committed certificates supply result values; never read a candidate.
    facts = {fact.id: json.loads(fact.value) for fact in result.run_result.state.facts}
    primal = facts[optimization.PRIMAL_FACT]
    dual = facts[optimization.KKT_FACT]
    proof = facts[optimization.GLOBAL_FACT]
    print(f"Verified unique global minimizer: x = ({', '.join(primal['x'])})")
    print(f"Verified objective: {primal['objective']}")
    print(f"Verified multipliers: lambda = ({', '.join(dual['lambda'])}); mu = ({', '.join(dual['mu'])})")
    print(f"Global certificate: {len(proof['weights'])} positive weighted squares + constraint terms")


def _bridge_answer(result: AgentResult) -> None:
    cases = bridge.verified_case_results(result)
    summary = bridge.verified_summary(result)
    envelope = summary["envelope"]
    pier = envelope["pier_min"]
    print(f"Verified load cases: {len(cases)}")
    print(f"Governing pier moment: {float(Fraction(pier['value']) / 1000):.2f} kNm ({pier['case']})")
    for index, peak in enumerate(envelope["positive_max"], 1):
        print(f"Span {index} positive maximum: {float(Fraction(peak['value']) / 1000):.2f} kNm "
              f"at x={float(Fraction(peak['x'])):.3f} m ({peak['case']})")
    for index, peak in enumerate(envelope["midspan_abs_max"], 1):
        print(f"Span {index} absolute MIDSPAN deflection: {float(Fraction(peak['value']) * 1000):.3f} mm "
              f"({peak['case']})")
    for metric, passed in summary["comparisons"].items():
        print(f"Supplied-limit comparison: {metric} = {passed}")


def _customer_support_answer(result: AgentResult) -> None:
    resolution = customer_support.verified_resolution(result)
    print(f"Verified recommendation: {resolution['outcome']}")
    amount = resolution["refund_cents"]
    if amount is not None:
        print(f"Recommended item refund: CNY {amount // 100}.{amount % 100:02d}")
    print(f"Reason: {resolution['reason']} | next action: {resolution['next_action']}")
    print("No refund executed; no payment-arrival promise.")


def _planning_answer(result: AgentResult) -> None:
    print("Verified feasible itinerary under the supplied synthetic data:")
    print(json.dumps(planning.verified_plan(result), ensure_ascii=False, indent=2))
    print("Subjective rationale is not a verified fact. No booking executed.")


def main(argv: Sequence[str] | None = None) -> int:
    """Return an exit code for console scripts and ``python -m resimind``."""
    parser = argparse.ArgumentParser(prog="resimind", description="Evidence-bound Agent reasoning with independent verification.")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="run an OFFLINE deterministic fixture, without a model or API key")
    demo.add_argument("--domain", choices=("optimization", "bridge", "customer-support", "planning", "knowledge-growth", "adaptive", "growth-control", "lean", "scheduling"), default="optimization",
                      help="demonstration domain (default: optimization)")
    demo.add_argument("--scenario", choices=("refund", "missing-delivery", "expired", "day-out", "rain", "missing-travel"),
                      help="customer-support: refund/missing-delivery/expired; planning: day-out/rain/missing-travel")
    demo.add_argument("--json", action="store_true", help="write only the complete JSON evidence/decision audit")
    args = parser.parse_args(argv)
    scenarios = {"customer-support": ("refund", "missing-delivery", "expired"),
                 "planning": ("day-out", "rain", "missing-travel")}
    if args.scenario is not None and args.scenario not in scenarios.get(args.domain, ()):
        parser.error("--scenario must match the selected customer-support or planning domain")
    if args.domain == "scheduling":
        from .scheduling_demo import run_demo
        report = run_demo()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("ResiMind | OFFLINE local scheduling | NOT A LIVE LLM RUN")
            print("Use checked rules and bounded symbolic tools; invoke a model only when local work is unsuitable.")
            for example in report["examples"]:
                print(example["name"] + ": " + example["expression"])
                for name in ("cold", "warm"):
                    arm = example[name]
                    audit = arm["strategy_audit"]
                    print(f"  {name}: {arm['status']}; actions={arm['steps']}; "
                          f"model callbacks={audit['model_calls']}; checked rule uses={audit['rule_accepts']}")
            fallback = report["fallback"]
            print(f"Explicit zero-local-work fallback: {fallback['status']}; "
                  f"scripted callbacks={fallback['strategy_audit']['model_calls']}")
            print(report["scope"])
        normal_ok = all(arm["status"] == "solved" and arm["strategy_audit"]["model_calls"] == 0
                        for example in report["examples"] for arm in (example["cold"], example["warm"]))
        return 0 if normal_ok and report["fallback"]["status"] == "solved" else 1
    if args.domain == "growth-control":
        from .growth_demo import run_demo
        report = run_demo()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("ResiMind | OFFLINE expression growth control | NO MODEL CALLS")
            print("Expand:", report["expression"])
            for name, arm in report["arms"].items():
                print(f"  {name}: {arm['status']}; actions={arm['steps']}; "
                      f"peak AST nodes={arm['strategy_audit']['peak_expression_nodes']}")
                if arm["output"] is not None:
                    print("  Verified expression:", arm["output"])
            print(report["scope"])
        return 0 if report["arms"]["bounded_growth"]["status"] == "solved" else 1
    if args.domain == "lean":
        from .lean_demo import run_demo
        report = run_demo()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("ResiMind | SCRIPTED proposals + REAL LOCAL LEAN | NOT A LIVE LLM RUN")
            for event in report["discovery"]["strategy_audit"]["events"]:
                if "strategy" not in event:
                    continue
                print(f"  {event['step']}. {event['strategy']}: {event['decision']} | {', '.join(event['reasons'])}")
                proof = event.get("lean_proof")
                if proof:
                    print(f"     Lean {proof['lean_version']}, tactic={proof['tactic']}, status={proof['status']}")
                    for goal in proof["goals"]:
                        print("     Remaining goal:", goal)
            for name in ("discovery", "transfer"):
                arm = report[name]
                print(f"{name}: {arm['status']}; Lean checks={arm['strategy_audit']['lean_checks']}; "
                      f"verified={arm['strategy_audit']['lean_verified']}")
            if report["discovery"]["status"] != "solved":
                print("Proof remains incomplete. Install Lean 4.29.0 or inspect the proof diagnostics.")
            print(report["scope"])
        return 0 if report["discovery"]["status"] == report["transfer"]["status"] == "solved" else 1
    if args.domain == "adaptive":
        from .adaptive_demo import run_demo
        report = run_demo()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("ResiMind | OFFLINE scripted adaptive recovery | NOT A LIVE LLM RUN")
            print("Deliberate wrong answer -> local task -> symbolic recovery -> verified rule reuse")
            for event in report["discovery"]["strategy_audit"]["events"]:
                if "strategy" in event:
                    print(f"  {event['step']}. {event['strategy']}: {event['decision']} "
                          f"| {', '.join(event['reasons'])}")
            for label in ("discovery", "transfer"):
                item = report[label]
                print(f"{label}: {item['status']}; checked rule uses={item['strategy_audit']['rule_accepts']}")
                if item["output"] is not None:
                    print(f"Verified expression: {item['output']}")
            print(report["scope"])
        return 0 if (report["discovery"]["status"] == report["transfer"]["status"] == "solved"
                     and report["discovery"]["admitted_rules"]
                     and report["transfer"]["strategy_audit"]["rule_accepts"] > 0) else 1
    if args.domain == "knowledge-growth":
        from .domains.polynomial_learning import run_demo
        report = run_demo()
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("ResiMind | OFFLINE deterministic knowledge growth | NOT A LIVE LLM RUN")
            print("Derive a cubic identity -> verify -> persist -> reload/reverify -> transfer")
            discovery = report["discovery"]
            print(f"Discovery: {discovery['proposal_calls']} proposals; "
                  f"{len(discovery['admitted_rules'])} verified rule(s)")
            print(f"New task: expand {report['transfer_expression']}")
            for name, arm in report["arms"].items():
                print(f"  {name}: {arm['status']}; {arm['proposal_calls']} proposals; "
                      f"{len(arm['rule_uses'])} committed cross-task rule use(s)")
            print(report["scope"])
        return 0 if (report["discovery"]["status"] == "solved"
                     and report["discovery"]["admitted_rules"]
                     and all(arm["status"] == "solved" for arm in report["arms"].values())) else 1
    if args.domain == "optimization":
        result = optimization.run_demo()
    elif args.domain == "bridge":
        result = bridge.run_demo()
    elif args.domain == "customer-support":
        result = customer_support.run_demo(args.scenario or "refund")
    else:
        result = planning.run_demo(args.scenario or "day-out")
    run = result.run_result
    solved = run.status == "solved" and run.residual.solved
    if args.json:
        print(result.to_json())
        return 0 if solved else 1
    print("ResiMind | OFFLINE deterministic fixture | NOT A LIVE LLM RUN")
    if args.domain == "optimization":
        print("Exact constrained optimization: 3 coupled variables, equality and inequality constraints")
    elif args.domain == "bridge":
        print("Continuous bridge: synthetic 24 m + 30 m line beam; eight load cases")
        print("Supplied factors/limits; deflections reported at MIDSPAN. No bridge safety certification.")
    elif args.domain == "customer-support":
        print(f"Customer support: synthetic order and fictional merchant rules | scenario={args.scenario or 'refund'}")
    else:
        print(f"Open-ended day planning: fictional venues and declared travel data | scenario={args.scenario or 'day-out'}")
    _trace(result)
    if solved:
        if args.domain == "optimization":
            _optimization_answer(result)
        elif args.domain == "bridge":
            _bridge_answer(result)
        elif args.domain == "customer-support":
            _customer_support_answer(result)
        else:
            _planning_answer(result)
    elif run.residual.pending:
        print(f"Pending evidence or checks: {', '.join(run.residual.pending)}")
    print(f"Status: {run.status} | remaining={run.residual.measure} | state_revision={run.state.revision}")
    return 0 if solved else 1
