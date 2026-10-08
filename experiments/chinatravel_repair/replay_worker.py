"""Isolated offline repair of one recorded candidate. No gold or API client."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from experiments.chinatravel.upstream_worker import _apply_resource_limits, _save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    _apply_resource_limits({"cpu_seconds": 120, "max_file_bytes": 16 * 1024 * 1024})
    sys.path.insert(0, str(args.upstream))
    from .repair import repair_plan
    task = json.loads(args.input.read_text())
    if set(task) != {"uid", "attempt", "query", "plan", "self_translation"}:
        raise ValueError("unexpected_replay_input")
    result = repair_plan(task["plan"], task["self_translation"], args.upstream,
                         public_query=task["query"])
    _save(args.output, result)


if __name__ == "__main__":
    main()
