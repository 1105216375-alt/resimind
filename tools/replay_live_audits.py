"""Recheck archived synthetic candidates OFFLINE; make no model requests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from resimind import Task
from resimind.domains import customer_support, optimization, planning


def main() -> None:
    folder = Path(__file__).resolve().parents[1] / "docs" / "evidence"
    records = sorted(folder.rglob("deepseek-*.json"))
    if not records:
        raise SystemExit("No archived DeepSeek audits found.")
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        expected = record["agent"]["run_result"]
        candidates = iter(event["candidate"] for event in expected["trace"])

        def complete(prompt: str) -> str:
            return json.dumps(next(candidates))

        task = record["agent"]["task"]
        if task["domain"] == optimization.DOMAIN:
            agent = optimization.build_agent(optimization.demo_problem(), complete=complete)
        elif task["domain"] == customer_support.DOMAIN:
            agent = customer_support.build_agent(customer_support.demo_case(record["scenario"]), complete=complete)
        elif task["domain"] == planning.DOMAIN:
            if record["scenario"] == "coffee-required":
                problem = replace(planning.demo_problem(), required_categories=("art", "meal", "coffee"))
            else:
                problem = planning.demo_problem(record["scenario"])
            agent = planning.build_agent(problem, complete=complete)
        else:
            raise SystemExit(f"Unsupported archived domain: {task['domain']}")
        result = agent.run(
            Task(task["id"], task["instruction"], task["domain"])
        )
        if result.to_dict()["run_result"] != expected:
            raise SystemExit(f"Replay mismatch: {path.name}")
        print(f"OFFLINE replay matched: {path.name} ({result.run_result.status})")


if __name__ == "__main__":
    main()
