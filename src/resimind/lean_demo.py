"""Scripted proposal selection with actual local Lean proof feedback."""
from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .agent import Task
from .domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from .domains.algebra import AlgebraVerifier
from .domains.polynomial_learning import PolynomialProblem
from .integrations.lean import LeanPolynomialBackend
from .knowledge import KnowledgeLibrary


def run_demo() -> dict:
    """No model API calls; Lean itself is executed, never simulated."""
    prompts = []

    def scripted_proposal(prompt):
        payload = json.loads(prompt)
        feedback = payload.get("proof_feedback")
        prompts.append({"strategy": payload["strategy"], "proof_feedback": feedback})
        return json.dumps({"after": feedback["proposed_after"] if feedback else "x*x+3*x+2",
                           "tactic": "grind" if feedback else "rfl"})

    backend = LeanPolynomialBackend()
    verifiers = {"algebra": AlgebraVerifier()}
    library, stats = KnowledgeLibrary(verifiers), AdaptiveStats()
    discovery = build_adaptive_learning_agent(
        PolynomialProblem("(x+1)*(x+2)", ("x",)), library, complete=scripted_proposal,
        lean_backend=backend, stats=stats, max_model_calls=2, max_lean_checks=4,
    ).run(Task("lean-feedback-discovery", "Expand and prove each step", "algebra"))
    with TemporaryDirectory(prefix="resimind-lean-demo-") as directory:
        path = Path(directory) / "rules.json"
        library.save(path)
        reloaded = KnowledgeLibrary.load(path, verifiers)
    transfer_stats = AdaptiveStats()
    transfer = None
    if discovery.result.run_result.status == "solved":
        transfer = build_adaptive_learning_agent(
            PolynomialProblem("(y+1)*(y+2)", ("y",)), reloaded, lean_backend=backend,
            stats=transfer_stats, max_model_calls=0, max_lean_checks=4,
        ).run(Task("lean-feedback-transfer", "Reuse and prove this application", "algebra"), learn=False)

    def report(outcome, audit):
        if outcome is None:
            return {"status": "not_run", "output": None, "admitted_rules": [], "strategy_audit": asdict(audit)}
        run = outcome.result.run_result
        return {"status": run.status, "stop_reason": run.stop_reason,
                "output": run.state.facts[-1].value[1] if run.status == "solved" else None,
                "admitted_rules": [r.candidate.id for r in outcome.admissions if r.status == "verified"],
                "strategy_audit": asdict(audit), "result": outcome.result.to_dict()}

    return {"mode": "scripted-proposals-real-local-lean", "live_model": False,
            "discovery": report(discovery, stats), "transfer": report(transfer, transfer_stats),
            "proposal_inputs": prompts,
            "scope": "Scripted proposals; actual Lean 4.29.0 proofs over Rat. No model API calls. "
                     "Rule admission/reload use the exact algebra library verifier; each task application also requires Lean."}
