"""Isolated offline search; input contains public/self-generated material only."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from experiments.chinatravel.upstream_worker import _apply_resource_limits, _save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "output", "upstream"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    _apply_resource_limits({"cpu_seconds": 120, "max_file_bytes": 32 * 1024 * 1024})
    sys.path.insert(0, str(args.upstream))
    import json
    from .solve import search_plan
    task = json.loads(args.input.read_text())
    if set(task) != {"uid", "attempt", "query", "plan", "self_translation", "optional_visit_paths"}:
        raise ValueError("unexpected_search_input")
    _save(args.output, search_plan(task["plan"], task["self_translation"], args.upstream,
                                  public_query=task["query"], optional_visit_paths=task["optional_visit_paths"]))


if __name__ == "__main__":
    main()
