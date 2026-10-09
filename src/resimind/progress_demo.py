"""Scripted examples of goal feedback and useful intermediate expansion."""
from dataclasses import asdict
import json

from .agent import Task
from .domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from .domains.algebra import AlgebraVerifier
from .domains.polynomial_learning import PolynomialProblem
from .knowledge import KnowledgeLibrary


def run_demo() -> dict:
    """Exercise actual verification and state transitions without an API call."""
    def episode(name, expression, variables, proposals):
        prompts = []

        def scripted(raw):
            prompts.append(json.loads(raw))
            index = len(prompts) - 1
            return json.dumps({"after": proposals[index]}) if index < len(proposals) else "null"

        stats = AdaptiveStats()
        outcome = build_adaptive_learning_agent(
            PolynomialProblem(expression, variables),
            KnowledgeLibrary({"algebra": AlgebraVerifier()}), complete=scripted,
            stats=stats, max_local_work=0, max_model_calls=len(proposals), max_steps=len(proposals) + 2,
        ).run(Task(name, "Expand the polynomial", "algebra"), learn=False)
        result = outcome.result.run_result
        return {"expression": expression, "status": result.status, "steps": result.steps,
                "output": result.state.facts[-1].value[1] if result.status == "solved" else None,
                "committed_steps": len(result.state.facts), "proposal_inputs": prompts,
                "strategy_audit": asdict(stats), "result": outcome.result.to_dict()}

    cosmetic = episode(
        "progress-cosmetic-recovery", "(5*u+(-3)*v)**2", ("u", "v"),
        ("(5*u-3*v)**2", "25*u**2-30*u*v+9*v**2"),
    )
    decomposition = episode(
        "progress-useful-decomposition", "(u+2)*(u+7)", ("u",),
        ("u*(u+7)+2*(u+7)", "u**2+7*u", "2*u+14"),
    )
    return {"mode": "scripted-goal-progress", "live_model": False,
            "cosmetic_recovery": cosmetic, "useful_decomposition": decomposition,
            "scope": "Public scripted proposals, actual exact verification and goal-policy decisions. "
                     "A zero local-work threshold deliberately activates callbacks; no model API or Lean "
                     "compiler is used in this demo. A cosmetic rewrite should leave state unchanged; "
                     "a useful distributive step can be accepted while making the AST larger. "
                     "Progress estimates are bounded guidance, not a proof of optimal reasoning."}
