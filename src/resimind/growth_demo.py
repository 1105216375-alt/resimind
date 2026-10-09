"""Offline comparison of expression growth on one explicit development task."""
from dataclasses import asdict

from .agent import Task
from .domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from .domains.algebra import AlgebraVerifier
from .domains.polynomial_learning import PolynomialProblem, WorkCounts
from .knowledge import KnowledgeLibrary


def run_demo() -> dict:
    expression = "(x*x+x+1)*(x*x+2*x+2)*(x*x+3*x+3)*(x*x+4*x+4)"
    arms = {}
    for name, enabled in (("primitive", False), ("bounded_growth", True)):
        stats, counts = AdaptiveStats(), WorkCounts()
        outcome = build_adaptive_learning_agent(
            PolynomialProblem(expression, ("x",)), KnowledgeLibrary({"algebra": AlgebraVerifier()}),
            stats=stats, counts=counts, max_model_calls=0, max_steps=64,
            control_expression_growth=enabled,
        ).run(Task(name, "Expand within fixed limits", "algebra"), learn=False)
        run = outcome.result.run_result
        arms[name] = {"status": run.status, "steps": run.steps,
                      "output": run.state.facts[-1].value[1] if run.status == "solved" else None,
                      "counts": asdict(counts), "strategy_audit": asdict(stats),
                      "result": outcome.result.to_dict()}
    return {"mode": "offline-symbolic-growth-control", "live_model": False,
            "expression": expression, "arms": arms,
            "scope": "One published development task; both arms have empty libraries and 64-action limits. "
                     "The improved arm adds bounded local distribution and compaction; this does not isolate "
                     "controller scheduling or measure model performance."}
