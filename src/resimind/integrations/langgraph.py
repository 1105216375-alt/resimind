"""Optional LangGraph gate that only reports committed, completed results.

Importing this module does not import LangGraph. Install ``resimind[langgraph]``
to build the graph. Domain adapters and verifiers remain trusted application
code; a graph branch is not a replacement for their mathematical contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypedDict

from ..agent import Agent, AgentResult, Task

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class VerificationInput(TypedDict):
    """Only the task is accepted as graph input; results are computed afresh."""

    task: Task


class VerifiedReport(TypedDict):
    """A local rendering of committed facts, without model-generated prose."""

    task_id: str
    facts: list[dict[str, object]]


class VerificationState(VerificationInput, total=False):
    """The result retains the residual and full audit, including rejected claims.

    Consumers should use ``report`` for completed output. ``result`` is an audit,
    not a final answer: its trace can contain rejected candidate claims.
    """

    result: AgentResult
    branch: Literal["verified_report", "needs_review"] | None
    report: VerifiedReport | None


def build_verification_graph(agent: Agent) -> CompiledStateGraph:
    """Compile ``run_verification -> verified_report | needs_review``.

    Invoke with ``{"task": Task(...)}``. The report branch requires both a
    solved status and an empty residual; all other outcomes retain the audit
    and unresolved obligations with ``report=None``. Neither branch publishes,
    sends messages, or invokes an unconstrained summarizing model.

    No checkpointer is installed. Each invocation calls ``agent.run`` from an
    empty verified state and replaces all output fields. Application-owned
    model callbacks/tools can still hold their own state, budgets, or sessions.
    """
    if not isinstance(agent, Agent):
        raise TypeError("agent must be a resimind.Agent")
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise ImportError(
            "LangGraph is optional. Install resimind[langgraph], or from this "
            'repository run: python -m pip install -e ".[langgraph]"'
        ) from exc

    def verify(state: VerificationInput) -> VerificationState:
        result = agent.run(state["task"])
        return {"result": result, "branch": None, "report": None}

    def route(state: VerificationState) -> Literal["verified_report", "needs_review"]:
        run = state["result"].run_result
        if run.status == "solved" and run.residual.solved:
            return "verified_report"
        return "needs_review"

    def verified_report(state: VerificationState) -> VerificationState:
        result = state["result"]
        return {
            "branch": "verified_report",
            "report": {
                "task_id": result.task.id,
                "facts": [fact.to_dict() for fact in result.run_result.state.facts],
            },
        }

    def needs_review(state: VerificationState) -> VerificationState:
        return {"branch": "needs_review", "report": None}

    graph = StateGraph(VerificationState, input_schema=VerificationInput)
    graph.add_node("run_verification", verify)
    graph.add_node("verified_report", verified_report)
    graph.add_node("needs_review", needs_review)
    graph.add_edge(START, "run_verification")
    graph.add_conditional_edges("run_verification", route, {
        "verified_report": "verified_report",
        "needs_review": "needs_review",
    })
    graph.add_edge("verified_report", END)
    graph.add_edge("needs_review", END)
    return graph.compile()
