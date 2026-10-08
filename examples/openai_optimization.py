"""Ask a real OpenAI model for optimization certificates; verify them locally."""

from __future__ import annotations

import argparse
import json
import os
import sys

from resimind.agent import Task
from resimind.domains.optimization import DOMAIN, PRIMAL_FACT, build_agent, demo_problem
from resimind.integrations.openai import OpenAICompletion, OpenAICompletionError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL"), help="Responses model ID (or OPENAI_MODEL)")
    parser.add_argument("--max-calls", type=int, default=8, help="maximum API attempts, including failed requests")
    parser.add_argument("--max-output-tokens", type=int, default=4096, help="per-response output and reasoning token limit")
    parser.add_argument("--timeout", type=float, default=60.0, help="SDK network timeout in seconds")
    parser.add_argument("--json", action="store_true", help="emit the actual Agent audit and API usage as JSON")
    args = parser.parse_args(argv)
    if not args.model:
        parser.error("provide --model or set OPENAI_MODEL to a Responses model available to your account")
    try:
        completion = OpenAICompletion(args.model, timeout=args.timeout,
                                      max_output_tokens=args.max_output_tokens, max_calls=args.max_calls)
    except (ValueError, OpenAICompletionError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    with completion:
        result = build_agent(demo_problem(), complete=completion).run(Task(
            "openai-optimization", "Prove the unique global minimum of the configured QP.", DOMAIN))
        run = result.run_result
        solved = run.status == "solved" and run.residual.solved
        if args.json:
            print(json.dumps({"mode": "live-openai", "model": completion.model,
                              "api_calls": completion.calls, "usage": completion.usage,
                              "provider_error": completion.last_error, "agent": result.to_dict()},
                             ensure_ascii=False, indent=2, allow_nan=False))
        else:
            print("ResiMind | live OpenAI proposals, independent exact verification")
            print(f"Model: {completion.model}")
            for event in run.trace:
                action = event.candidate.action if event.candidate else event.proposer
                print(f"  {event.step}. {event.decision.value.upper():9} {action}: "
                      f"{', '.join(event.reasons)} (remaining={event.residual_after.measure})")
            if solved:
                for fact in run.state.facts:
                    if fact.id == PRIMAL_FACT:
                        primal = json.loads(fact.value)
                        print(f"Verified minimizer: {primal['x']}; objective: {primal['objective']}")
            else:
                print(f"Verified partial fact IDs: {[fact.id for fact in run.state.facts]}")
                print(f"Pending obligations: {list(run.residual.pending)}")
            print(f"Status: {run.status}; stop reason: {run.stop_reason}; remaining: {run.residual.measure}")
            print(f"API calls: {completion.calls}/{completion.max_calls}; reported token usage: {completion.usage}")
            if completion.last_error:
                print(completion.last_error, file=sys.stderr)
        return 0 if solved else 1


if __name__ == "__main__":
    raise SystemExit(main())
