"""Compose an open-ended day plan and check its constraints; opt in to live DeepSeek."""

from __future__ import annotations

import argparse
import json
import os
import sys

from resimind import Task
from resimind.domains.planning import (
    DOMAIN, build_agent, demo_problem, run_demo, verified_plan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("day-out", "rain", "missing-travel"), default="day-out")
    parser.add_argument("--live", action="store_true", help="make real DeepSeek API requests; default is OFFLINE")
    parser.add_argument("--model", help="DeepSeek model ID; live default comes from DEEPSEEK_MODEL")
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--json", action="store_true", help="emit the mode, checked plan, and complete audit")
    args = parser.parse_args(argv)
    if args.model and not args.live:
        parser.error("--model requires --live; the default example is OFFLINE")

    complete = None
    if args.live:
        model = args.model or os.environ.get("DEEPSEEK_MODEL")
        if not model:
            parser.error("--live requires --model or DEEPSEEK_MODEL")
        from resimind.integrations.deepseek import DeepSeekCompletion, DeepSeekCompletionError

        try:
            complete = DeepSeekCompletion(model, timeout=args.timeout,
                                          max_output_tokens=args.max_output_tokens, max_calls=args.max_calls)
        except (ValueError, DeepSeekCompletionError) as exc:
            print(f"Configuration error: {exc}", file=sys.stderr)
            return 2

    try:
        if complete is None:
            result = run_demo(args.scenario)
        else:
            result = build_agent(demo_problem(args.scenario), complete=complete).run(
                Task("planning-live", "Plan a relaxed day with art and a meal; consider the supplied soft preferences "
                     "and satisfy every explicit hard constraint.", DOMAIN)
            )
    finally:
        if complete is not None:
            complete.close()

    run = result.run_result
    solved = run.status == "solved" and run.residual.solved
    plan = verified_plan(result) if solved else None
    mode = "live-deepseek" if args.live else "offline-deterministic"
    output = {"mode": mode, "scenario": args.scenario, "plan": plan, "agent": result.to_dict()}
    if complete is not None:
        output.update(model=complete.model, api_calls=complete.calls, usage=complete.usage,
                      provider_error=complete.last_error)
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(f"ResiMind open-ended planning | {mode} | fictional venues / declared transport data")
        if not args.live:
            print("OFFLINE deterministic fixture | NOT A LIVE LLM RUN")
        for event in run.trace:
            action = event.candidate.action if event.candidate is not None else event.proposer
            print(f"  {event.step}. {event.decision.value.upper():6} {action}: "
                  f"{', '.join(event.reasons)} (remaining={event.residual_after.measure})")
        if plan is not None:
            print("Verified feasible itinerary (no booking executed):")
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            print("Subjective rationale remains unverified; no best-plan claim.")
        else:
            print(f"No verified itinerary. Pending: {list(run.residual.pending)}")
        print(f"Status: {run.status}; remaining={run.residual.measure}")
        if complete is not None:
            print(f"Model: {complete.model}; calls: {complete.calls}; usage: {complete.usage}")
            if complete.last_error:
                print(complete.last_error, file=sys.stderr)
    return 0 if solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
