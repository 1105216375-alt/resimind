"""Run the small conditional-lemma composition pilot.

The output is intentionally written outside the repository. This is a
development diagnostic for the planner and Lean gate, not an external theorem
proving leaderboard.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from resimind.integrations.formal import FormalBackend, Goal, Lemma, Recipe, TheoremMemory, solve, term, var


def _case():
    p, q = var("p"), var("q")
    bindings = (("p", "Prop"), ("q", "Prop"))
    lift = Lemma(Goal(bindings, term("imp", p, term("or", p, q))), Recipe("grind"))
    swap = Lemma(Goal(bindings,
                      term("imp", term("or", p, q), term("or", q, p))),
                 Recipe("grind"))
    target = Goal(bindings, term("imp", p, term("or", q, p)))
    return target, TheoremMemory((lift, swap))


def _run(label, target, memory, backend, *, recipes, depth):
    started = time.perf_counter()
    result = solve(target, backend, memory=memory, recipes=recipes,
                   composition_depth=depth, composition_budget=64)
    return {
        "label": label,
        "status": result.status,
        "attempts": len(result.attempts),
        "actions": [attempt.action for attempt in result.attempts],
        "seconds": time.perf_counter() - started,
        "proof_digest": result.attempts[-1].proof_digest if result.attempts else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True,
                        help="absolute path outside the repository for JSON output")
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        raise SystemExit("--output must be an absolute path outside the repository")
    target, memory = _case()
    backend = FormalBackend(timeout_seconds=10)
    if not backend.lean_path:
        raise SystemExit("Lean 4.29.0 is unavailable")
    rows = [
        _run("strong_grind_empty", target, TheoremMemory(), backend,
             recipes=(Recipe("grind"),), depth=0),
        _run("weak_simp_empty", target, TheoremMemory(), backend,
             recipes=(Recipe("simp"),), depth=0),
        _run("weak_simp_with_composition", target, memory, backend,
             recipes=(Recipe("simp"),), depth=3),
        _run("strong_grind_with_composition", target, memory, backend,
             recipes=(Recipe("grind"),), depth=3),
    ]
    payload = {
        "protocol": "formal-composition-pilot-v1",
        "lean_version": backend.required_version,
        "memory_lemmas": len(memory.lemmas),
        "composition_depth": 3,
        "composition_budget": 64,
        "rows": rows,
        "interpretation": {
            "scope": "hand-authored two-lemma Prop chain",
            "claim": "kernel-checked composition capability only",
            "not_claimed": "external theorem-proving ranking or frontier superiority",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
