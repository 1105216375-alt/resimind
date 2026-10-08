"""Gate a LangGraph workflow with independently verified optimization facts.

Default: deterministic OFFLINE demonstration, no API key or model call.
Use --live --model MODEL to request candidates from DeepSeek explicitly.
OpenAI is also available with --provider openai.
"""

from __future__ import annotations

import argparse
import json
import os

from resimind import Task
from resimind.domains.optimization import DOMAIN, PRIMAL_FACT, build_agent, demo_problem
from resimind.integrations.langgraph import build_verification_graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="make real provider API requests using its environment key")
    parser.add_argument("--provider", choices=("deepseek", "openai"), default="deepseek", help="provider for --live (default: deepseek)")
    parser.add_argument("--model", help="model for --live; defaults to DEEPSEEK_MODEL or OPENAI_MODEL")
    parser.add_argument("--max-calls", type=int, default=8, help="maximum live API requests (default: 8)")
    parser.add_argument("--max-output-tokens", type=int, default=4096, help="per-response output and reasoning token limit")
    parser.add_argument("--timeout", type=float, default=60.0, help="SDK network timeout in seconds")
    parser.add_argument("--json", action="store_true", help="emit mode, graph branch, report, and full audit as JSON")
    args = parser.parse_args(argv)
    if args.model and not args.live:
        parser.error("--model requires --live; the default demonstration is OFFLINE")
    if args.max_calls < 1:
        parser.error("--max-calls must be positive")

    complete = None
    if args.live:
        model_env = "DEEPSEEK_MODEL" if args.provider == "deepseek" else "OPENAI_MODEL"
        model = args.model or os.environ.get(model_env)
        if not model:
            parser.error(f"--live requires --model MODEL or {model_env}")
        try:
            if args.provider == "deepseek":
                from resimind.integrations.deepseek import DeepSeekCompletion

                complete = DeepSeekCompletion(model=model, max_calls=args.max_calls,
                                              max_output_tokens=args.max_output_tokens, timeout=args.timeout)
            else:
                from resimind.integrations.openai import OpenAICompletion

                complete = OpenAICompletion(model=model, max_calls=args.max_calls,
                                            max_output_tokens=args.max_output_tokens, timeout=args.timeout)
        except (ImportError, RuntimeError, ValueError) as exc:
            parser.error(str(exc))

    try:
        try:
            graph = build_verification_graph(build_agent(demo_problem(), complete=complete))
        except ImportError as exc:
            parser.error(str(exc))
        state = graph.invoke({"task": Task(
            "langgraph-optimization", "Prove the unique global minimum of the configured QP.", DOMAIN,
        )})
    finally:
        if complete is not None:
            complete.close()

    result = state["result"]
    run = result.run_result
    mode = f"LIVE ({args.provider})" if args.live else "OFFLINE (deterministic, no model requests)"
    if args.json:
        output = {
            "mode": mode, "branch": state["branch"], "report": state["report"],
            "audit": result.to_dict(),
        }
        if complete is not None:
            output["provider"] = args.provider
            output["model"] = complete.model
            output["model_calls"] = complete.calls
            output["usage"] = complete.usage
            output["last_error"] = complete.last_error
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"ResiMind + LangGraph | {mode}")
        if complete is not None:
            print(f"Model: {complete.model}")
        for event in run.trace:
            action = event.candidate.action if event.candidate is not None else "runtime"
            print(f"  {event.step}. {event.decision.value.upper():6} {action}: "
                  f"{', '.join(event.reasons)} (remaining={event.residual_after.measure})")
        print(f"Graph branch: {state['branch']}")
        if state["report"] is not None:
            primal = next(fact for fact in state["report"]["facts"] if fact["id"] == PRIMAL_FACT)
            value = json.loads(primal["value"])
            print(f"Verified minimizer: x = {value['x']}; objective = {value['objective']}")
            print("Report contains only committed facts; no model-written final answer.")
        else:
            print("No completed report. Outstanding obligations:")
            for obligation in run.residual.pending:
                print(f"  - {obligation}")
        print(f"Status: {run.status}; stop reason: {run.stop_reason}")
        if complete is not None:
            print(f"Model calls: {complete.calls}; usage: {complete.usage}")
            if complete.last_error:
                print(f"Model adapter: {complete.last_error}")
    return 0 if state["branch"] == "verified_report" else 2


if __name__ == "__main__":
    raise SystemExit(main())
