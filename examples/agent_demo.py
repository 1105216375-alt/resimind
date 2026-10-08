"""Complete offline Agent: task -> tool -> model proposal -> verify -> result.

The model callback is a deterministic fake, not a remote LLM or a benchmark.
Replace it with a provider callback to use an actual model.
"""
from __future__ import annotations

import json

from residual_agent.agent import Agent, AgentResult, Task
from residual_agent.adapters import ModelProposer
from residual_agent.memory import Route, RouteMemory

from .inventory import (
    ACTION, INPUTS, TARGET, InventoryDomain, InventoryVerifier, make_evidence,
)


class SyntheticInventoryTool:
    name = "synthetic_inventory_reader"

    def collect(self, task: Task):
        if task.domain != "inventory":
            raise ValueError("This demo tool supports only inventory tasks")
        return make_evidence()


def build_agent() -> Agent:
    memory = RouteMemory(registered_actions=(ACTION,))
    memory.add(Route(
        "inventory-route-v1", "inventory", (("workflow", "stock-balance"),),
        (ACTION,), INPUTS,
    ))
    memory.review("inventory-route-v1", reviewer="demo-maintainer",
                  reason="Reviewed the registered inventory arithmetic rule")

    def proposers(task, routes, evidence):
        attempt = 0

        def fake_model(prompt: str) -> str:
            # A real provider would consume this JSON prompt. This fake lets the
            # example demonstrate a bad first answer without model credentials.
            nonlocal attempt
            payload = json.loads(prompt)
            feedback = payload.get("last_feedback")
            corrected = bool(feedback and "arithmetic_claim_mismatch" in feedback["reasons"])
            attempt += 1
            return json.dumps({
                "id": f"model-candidate-{attempt}",
                "action": ACTION,
                "target": TARGET,
                "claim": "closing_count=12" if corrected else "closing_count=999",
                "refs": [item.id for item in evidence],
            })

        # The reviewed route is guidance only. Both attempts still go through
        # InventoryVerifier with the evidence freshly collected for this task.
        actions = routes[0].actions if routes else (ACTION,)
        return (ModelProposer(
            fake_model, allowed_actions=actions, evidence=evidence,
            instruction=task.instruction,
        ),)

    return Agent(
        domain_factory=lambda task: InventoryDomain(),
        verifier_factory=lambda task: InventoryVerifier(),
        proposer_factory=proposers,
        tools=(SyntheticInventoryTool(),), memory=memory,
        max_steps=4, max_no_progress=3,
    )


def run_demo() -> AgentResult:
    return build_agent().run(Task(
        "synthetic-task-1", "Reconcile this synthetic inventory shift.",
        "inventory", (("workflow", "stock-balance"),),
    ))


if __name__ == "__main__":
    print(run_demo().to_json())
