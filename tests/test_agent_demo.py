import json

from examples.agent_demo import build_agent, run_demo
from resimind.agent import Task


def test_complete_agent_rejects_false_model_claim_and_finishes():
    result = run_demo()
    assert result.run_result.status == "solved"
    assert [event.decision.value for event in result.run_result.trace] == ["reject", "accept"]
    closing = next(f for f in result.run_result.state.facts if f.metric == "closing_count")
    assert closing.value == 12
    assert len(result.routes) == 1
    assert len(result.tool_events) == 1
    assert json.loads(result.to_json())["task"]["id"] == "synthetic-task-1"


def test_agent_reuse_has_fresh_candidates_state_and_trace():
    agent = build_agent()
    task = Task("another-task", "Reconcile inventory", "inventory")
    first = agent.run(task)
    second = agent.run(task)
    assert first.run_result.to_dict() == second.run_result.to_dict()
    assert len(second.run_result.trace) == 2


def test_tool_failure_does_not_produce_facts():
    result = build_agent().run(Task("unsupported-task", "Unsupported", "other"))
    assert result.run_result.status == "error"
    assert result.run_result.state.facts == ()
    assert not result.run_result.residual.solved
