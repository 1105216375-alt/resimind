"""Recheck archived synthetic candidates OFFLINE; make no model requests."""

from __future__ import annotations

import json
from pathlib import Path

from resimind import Task
from resimind.domains.optimization import build_agent, demo_problem


def main() -> None:
    folder = Path(__file__).resolve().parents[1] / "docs" / "evidence"
    records = sorted(folder.glob("deepseek-*.json"))
    if not records:
        raise SystemExit("No archived DeepSeek audits found.")
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        expected = record["agent"]["run_result"]
        candidates = iter(event["candidate"] for event in expected["trace"])

        def complete(prompt: str) -> str:
            return json.dumps(next(candidates))

        task = record["agent"]["task"]
        result = build_agent(demo_problem(), complete=complete).run(
            Task(task["id"], task["instruction"], task["domain"])
        )
        if result.to_dict()["run_result"] != expected:
            raise SystemExit(f"Replay mismatch: {path.name}")
        print(f"OFFLINE replay matched: {path.name} ({result.run_result.status})")


if __name__ == "__main__":
    main()
