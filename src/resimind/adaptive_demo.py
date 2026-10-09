"""Offline fault-injection fixture for strategy changes and verified reuse."""
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .agent import Task
from .domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from .domains.algebra import AlgebraVerifier
from .domains.polynomial_learning import PolynomialProblem
from .knowledge import KnowledgeLibrary


def run_demo() -> dict:
    """Exercise real checks with scripted replies, without a model or API key."""
    def fixture(prompt):
        payload = json.loads(prompt)
        selected = payload["selected_subexpression"]
        # A deliberate whole-answer error, one correct local edit, then another
        # error. The controller, not this fixture, selects symbolic recovery.
        local_answer = (payload["strategy"] == "local_model" and selected
                        and selected["expression"] == "(x + 1) * (x + 2)")
        return json.dumps({"after": "x*x + 3*x + 2" if local_answer else "0"})

    verifiers = {"algebra": AlgebraVerifier()}
    library, stats = KnowledgeLibrary(verifiers), AdaptiveStats()
    discovery = build_adaptive_learning_agent(
        PolynomialProblem("(x+1)*(x+2)*(x+3)", ("x",)), library,
        complete=fixture, stats=stats, cost_aware_scheduling=False,
    ).run(Task("adaptive-demo-discovery", "Expand the product", "algebra"))
    with TemporaryDirectory(prefix="resimind-adaptive-") as directory:
        path = Path(directory) / "rules.json"
        library.save(path)
        reloaded = KnowledgeLibrary.load(path, verifiers)
    transfer_stats = AdaptiveStats()
    transfer = build_adaptive_learning_agent(
        PolynomialProblem("(y+1)*(y+2)*(y+3)", ("y",)), reloaded,
        stats=transfer_stats,
    ).run(Task("adaptive-demo-transfer", "Reuse on a new variable", "algebra"), learn=False)

    def report(outcome, audit):
        run = outcome.result.run_result
        return {
            "status": run.status, "stop_reason": run.stop_reason,
            "output": run.state.facts[-1].value[1] if run.status == "solved" and run.residual.solved else None,
            "admitted_rules": [record.candidate.id for record in outcome.admissions if record.status == "verified"],
            "strategy_audit": asdict(audit), "result": outcome.result.to_dict(),
        }

    return {
        "mode": "offline-scripted-adaptive-recovery", "live_model": False,
        "discovery": report(discovery, stats), "transfer": report(transfer, transfer_stats),
        "scope": "Deliberate scripted mistakes use the legacy model-first policy to exercise real verification "
                 "and recovery; default routing is demonstrated by --domain scheduling. No live-model performance claim.",
    }
