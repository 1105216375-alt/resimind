"""Offline demonstration of bounded local selection and explicit model fallback."""
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .agent import Task
from .domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from .domains.algebra import AlgebraVerifier
from .domains.polynomial_learning import PolynomialProblem, WorkCounts
from .knowledge import KnowledgeLibrary


def run_demo() -> dict:
    verifiers = {"algebra": AlgebraVerifier()}
    library = KnowledgeLibrary(verifiers)
    for name, expression, variables in (
        ("quartic", "(u+v)**4", ("u", "v")),
        ("trinomial-square", "(u+v+w)**2", ("u", "v", "w")),
    ):
        learned = build_adaptive_learning_agent(PolynomialProblem(expression, variables), library).run(
            Task("schedule-learn-" + name, "Expand and store a checked rule", "algebra"))
        if learned.result.run_result.status != "solved":
            raise RuntimeError("The offline discovery example did not finish")

    def unused_callback(_):
        raise RuntimeError("This task should finish without a model callback")

    def episode(name, expression, variables, store, *, complete=unused_callback, max_local_work=4096):
        stats, counts = AdaptiveStats(), WorkCounts()
        outcome = build_adaptive_learning_agent(
            PolynomialProblem(expression, variables), store, complete=complete,
            stats=stats, counts=counts, max_local_work=max_local_work,
        ).run(Task(name, "Expand using bounded local work before requesting a model", "algebra"), learn=False)
        run = outcome.result.run_result
        return {"status": run.status, "steps": run.steps,
                "output": run.state.facts[-1].value[1] if run.status == "solved" else None,
                "strategy_audit": asdict(stats), "work": asdict(counts),
                "result": outcome.result.to_dict()}

    examples = []
    with TemporaryDirectory(prefix="resimind-scheduling-") as directory:
        path = Path(directory) / "rules.json"
        library.save(path)
        for name, expression, variables in (
            ("quartic-shortcut", "(2*x+5*y)**4", ("x", "y")),
            ("simplify-before-expanding", "((x+2*y)+(3*x-y)+2)**2", ("x", "y")),
        ):
            examples.append({"name": name, "expression": expression,
                             "cold": episode(name+"-cold", expression, variables, KnowledgeLibrary(verifiers)),
                             "warm": episode(name+"-warm", expression, variables, KnowledgeLibrary.load(path, verifiers))})
    fallback = episode(
        "explicit-fallback", "(x+3)*(x+4)", ("x",), KnowledgeLibrary(verifiers),
        complete=lambda _: json.dumps({"after": "x*x+7*x+12"}), max_local_work=0,
    )
    return {"mode": "offline-local-scheduling", "live_model": False,
            "verified_rules": len(library.lookup("algebra")), "examples": examples, "fallback": fallback,
            "scope": "Public development examples with a configured callback but no API calls. "
                     "Normal cases should finish without invoking it. The separate zero-local-work "
                     "case deliberately invokes one scripted callback to demonstrate fallback. "
                     "Candidate costs are bounded heuristics, not proofs or measured savings. "
                     "Rule effects and selection reasons are available in strategy_audit."}
