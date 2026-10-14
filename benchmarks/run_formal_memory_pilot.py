"""Small core-Lean development pilot; no external leaderboard or LLM claim."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import time

from resimind.core import content_digest
from resimind.integrations.formal import FormalBackend, Goal, Recipe, TheoremMemory, nat, solve, term, var


def templates(a, b):
    return (
        term("imp", term("and", a, b), term("and", b, a)),
        term("imp", term("or", a, b), term("or", b, a)),
        term("eq", term("add", a, b), term("add", b, a)),
        term("eq", term("mul", a, term("add", b, nat(1))), term("add", term("mul", a, b), a)),
    )


def curriculum():
    p, q, n, m = (var(s) for s in ("p", "q", "n", "m"))
    logic = (("p", "Prop"), ("q", "Prop"))
    numbers = (("n", "Nat"), ("m", "Nat"))
    return (
        (Goal(logic, templates(p, q)[0]), Recipe("grind")),
        (Goal(logic, templates(p, q)[1]), Recipe("grind")),
        (Goal(numbers, templates(n, m)[2]), Recipe("omega")),
        (Goal(numbers, templates(n, m)[3]), Recipe("induction_simp", "n")),
    )


def cases():
    p, q, r, n, m = (var(s) for s in ("p", "q", "r", "n", "m"))
    prop_bindings = (("p", "Prop"), ("q", "Prop"), ("r", "Prop"))
    nat_bindings = (("n", "Nat"), ("m", "Nat"))
    props = [term(op, p, r) for op in ("and", "or", "imp", "iff")]
    props += [term("not", p), term("and", term("or", p, r), term("imp", r, q))]
    rows = []
    for i in range(6):
        for family, index in (("composed_conjunction", 0), ("composed_disjunction", 1)):
            rows.append((f"{family}:{i}", family, Goal(prop_bindings, templates(props[i], q)[index])))
        a, b = term("add", n, nat(i+2)), term("add", m, nat(i+3))
        for family, index in (("natural_addition", 2), ("natural_induction_identity", 3)):
            rows.append((f"{family}:{i}", family, Goal(nat_bindings, templates(a, b)[index])))
    return rows


def snapshot():
    root = Path(__file__).resolve().parents[1]
    files = sorted([*root.glob("src/resimind/**/*.py"), Path(__file__).resolve()])
    entries = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return {"files": entries, "digest": content_digest(entries)}


def study(output):
    root = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if output == root or root in output.parents:
        raise ValueError("raw evaluation records must stay outside the repository")
    output.mkdir(parents=True, exist_ok=False)
    tasks = cases()
    training = curriculum()
    manifest = {
        "protocol": "formal-memory-development-v1", "source": snapshot(),
        "tasks": [{"id": key, "family": family, "goal": asdict(goal)} for key, family, goal in tasks],
        "training": [{"goal": asdict(goal), "recipe": asdict(recipe)} for goal, recipe in training],
        "max_checks": 6, "timeout_seconds_per_compilation": 10,
        "recipes": ["grind", "omega", "simp", "induction_simp(first natural binder)"],
        "arms": ["empty", "fixed_first_lemma", "growing"], "shuffle_seed": 731,
        "limitations": ["handwritten development families", "24 repeated probes across four rounds",
                        "human-selected training statements and proof recipes", "no language model",
                        "fixed portfolio, not feedback-conditioned proof search",
                        "not long-horizon evidence or an external benchmark",
                        "all recalled lemmas are recompiled, including their proofs"],
    }
    task_digests = {content_digest(goal) for _, _, goal in tasks}
    assert len(task_digests) == len(tasks)
    assert not task_digests.intersection(content_digest(g) for g, _ in training)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    backend = FormalBackend(timeout_seconds=10)
    memory = TheoremMemory()
    fixed = None
    rows, learning = [], []
    rng = random.Random(731)
    for phase, (goal, recipe) in enumerate(training, 1):
        start = time.perf_counter()
        memory, proof = memory.admit(goal, recipe, backend)
        admission_seconds = time.perf_counter() - start
        memory.save(output / f"memory-{phase}.json")
        start = time.perf_counter()
        memory = TheoremMemory.load(output / f"memory-{phase}.json", backend)
        learning.append({"phase": phase, "admission_seconds": admission_seconds,
                         "reload_seconds": time.perf_counter()-start,
                         "admission_checks": 1, "reload_checks": len(memory.lemmas),
                         "proof": asdict(proof), "rules": len(memory.lemmas)})
        if fixed is None:
            fixed = memory
        frozen_digest = content_digest(memory)
        for key, family, task in tasks:
            arms = [("empty", TheoremMemory()), ("fixed_first_lemma", fixed), ("growing", memory)]
            rng.shuffle(arms)
            recipes = (Recipe("grind"), Recipe("omega"), Recipe("simp"))
            natural = next((name for name, sort in task.bindings if sort == "Nat"), None)
            if natural:
                recipes += (Recipe("induction_simp", natural),)
            for name, bank in arms:
                result = solve(task, backend, memory=bank, recipes=recipes, max_checks=6)
                row = {"phase": phase, "id": key, "family": family, "arm": name, **asdict(result)}
                rows.append(row)
                with (output / "rows.jsonl").open("a") as stream:
                    stream.write(json.dumps(row)+"\n")
        assert content_digest(memory) == frozen_digest
        print(f"phase {phase}: {len(memory.lemmas)} checked lemmas, {len(rows)} evaluations", flush=True)
    n, p = var("n"), var("p")
    false_goals = [Goal((("n", "Nat"),), term("eq", n, term("add", n, nat(k)))) for k in (1, 2, 3)]
    false_goals.append(Goal((("p", "Prop"),), p))
    controls = [asdict(solve(g, backend, memory=memory)) for g in false_goals]
    assert all(row["status"] != "verified" for row in controls)
    assert snapshot()["digest"] == manifest["source"]["digest"], "source changed during evaluation"
    summary = {"protocol": manifest["protocol"], "unique_tasks": len(tasks), "rounds": 4,
               "source_digest": manifest["source"]["digest"], "task_digest": content_digest(manifest["tasks"]),
               "learning": learning, "negative_controls": controls, "results": []}
    for phase in range(1, 5):
        for arm in manifest["arms"]:
            selected = [r for r in rows if r["phase"] == phase and r["arm"] == arm]
            summary["results"].append({"phase": phase, "arm": arm, "tasks": len(selected),
                "verified": sum(r["status"] == "verified" for r in selected),
                "checks": sum(len(r["attempts"]) for r in selected),
                "seconds": sum(r["seconds"] for r in selected),
                "recall_completions": sum(r["status"] == "verified" and r["attempts"][-1]["action"].startswith("recall:") for r in selected),
                "model_calls": sum(r["model_calls"] for r in selected)})
    (output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["results"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    study(parser.parse_args().output)
