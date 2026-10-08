"""Check a synthetic return request; explicitly opt in to live DeepSeek proposals."""

from __future__ import annotations

import argparse
import json
import os
import sys

from resimind import Task
from resimind.domains.customer_support import (
    DOMAIN, build_agent, demo_case, run_demo, verified_resolution,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("refund", "missing-delivery", "expired"), default="refund")
    parser.add_argument("--live", action="store_true", help="make real DeepSeek API requests; default is OFFLINE")
    parser.add_argument("--model", help="DeepSeek model ID; live default comes from DEEPSEEK_MODEL")
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--json", action="store_true", help="emit the mode, checked resolution, and complete audit")
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
            result = build_agent(demo_case(args.scenario), complete=complete).run(
                Task("customer-support-live", "Check this return request under the configured merchant policy; "
                     "recommend a next step without executing a refund.", DOMAIN)
            )
    finally:
        if complete is not None:
            complete.close()

    run = result.run_result
    solved = run.status == "solved" and run.residual.solved
    resolution = verified_resolution(result) if solved else None
    mode = "live-deepseek" if args.live else "offline-deterministic"
    output = {"mode": mode, "scenario": args.scenario, "resolution": resolution, "agent": result.to_dict()}
    if complete is not None:
        output.update(model=complete.model, api_calls=complete.calls, usage=complete.usage,
                      provider_error=complete.last_error)
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(f"ResiMind customer support | {mode} | synthetic order / fictional merchant policy")
        if not args.live:
            print("OFFLINE deterministic fixture | NOT A LIVE LLM RUN")
        for event in run.trace:
            action = event.candidate.action if event.candidate is not None else event.proposer
            print(f"  {event.step}. {event.decision.value.upper():6} {action}: "
                  f"{', '.join(event.reasons)} (remaining={event.residual_after.measure})")
        if resolution is not None:
            print("Verified recommendation (no refund executed):")
            print(json.dumps(resolution, ensure_ascii=False, indent=2))
        else:
            print(f"No completed recommendation. Pending: {list(run.residual.pending)}")
        print(f"Status: {run.status}; remaining={run.residual.measure}")
        if complete is not None:
            print(f"Model: {complete.model}; calls: {complete.calls}; usage: {complete.usage}")
            if complete.last_error:
                print(complete.last_error, file=sys.stderr)
    return 0 if solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
