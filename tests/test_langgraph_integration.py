"""Exercise the real optional graph and ensure unfinished runs cannot report."""

import builtins
from dataclasses import replace
import importlib
import json

import pytest

from resimind import Decision, Residual, Task
from resimind.domains import optimization as qp
from resimind.integrations.langgraph import (
    VerificationInput, VerificationState, build_verification_graph,
)


@pytest.fixture
def graph_api():
    return pytest.importorskip("langgraph.graph")


@pytest.fixture(scope="module")
def accepted_candidates():
    result = qp.run_demo().run_result
    return [event.candidate.to_json() for event in result.trace if event.decision is Decision.ACCEPT]


def task(identifier="test", instruction="Prove the configured QP minimum."):
    return Task(identifier, instruction, qp.DOMAIN)


def test_solved_graph_reports_only_committed_facts(graph_api):
    graph = build_verification_graph(qp.build_agent(qp.demo_problem()))
    state = graph.invoke({"task": task()})
    run = state["result"].run_result
    assert state["branch"] == "verified_report"
    assert run.status == "solved" and run.residual.solved
    assert state["report"] == {"task_id": "test", "facts": [fact.to_dict() for fact in run.state.facts]}
    assert [event.decision.value for event in run.trace] == ["accept", "reject", "accept", "accept"]
    assert "26/15" not in json.dumps(state["report"])
    state["report"]["facts"][0]["value"] = "tampered renderer data"
    assert run.state.facts[0].value != "tampered renderer data"


def test_empty_proposer_preserves_residual_and_never_runs_report_node(graph_api):
    graph = build_verification_graph(qp.build_agent(qp.demo_problem(), complete=lambda prompt: "null"))
    updates = list(graph.stream({"task": task()}, stream_mode="updates"))
    assert [next(iter(update)) for update in updates] == ["run_verification", "needs_review"]
    run = updates[0]["run_verification"]["result"].run_result
    assert run.status == "stalled"
    assert run.state.facts == ()
    assert set(run.residual.pending) == set(qp.TARGETS)
    assert updates[-1]["needs_review"]["report"] is None


def test_rejected_certificate_cannot_reach_report(graph_api, accepted_candidates):
    bad = json.loads(accepted_candidates[0])
    claim = json.loads(bad["claim"])
    claim["d"][0] = "-4"
    bad["claim"] = json.dumps(claim)
    graph = build_verification_graph(qp.build_agent(qp.demo_problem(), complete=lambda prompt: json.dumps(bad)))
    state = graph.invoke({"task": task()})
    run = state["result"].run_result
    assert state["branch"] == "needs_review" and state["report"] is None
    assert run.state.facts == () and run.residual.measure == 3
    assert any("ldl_diagonal_not_positive" in event.reasons for event in run.trace)
    assert not any(event.decision is Decision.ACCEPT for event in run.trace)


def test_partial_progress_keeps_committed_fact_but_blocks_completed_report(graph_api, accepted_candidates):
    def complete(prompt):
        return "null" if json.loads(prompt)["state"]["facts"] else accepted_candidates[0]

    graph = build_verification_graph(qp.build_agent(qp.demo_problem(), complete=complete))
    state = graph.invoke({"task": task()})
    run = state["result"].run_result
    assert state["branch"] == "needs_review" and state["report"] is None
    assert [fact.id for fact in run.state.facts] == [qp.CONVEXITY_FACT]
    assert run.residual.measure == 2


def test_model_format_error_routes_to_review(graph_api):
    graph = build_verification_graph(qp.build_agent(qp.demo_problem(), complete=lambda prompt: "not JSON"))
    state = graph.invoke({"task": task()})
    assert state["branch"] == "needs_review" and state["report"] is None
    assert state["result"].run_result.status == "error"
    assert state["result"].run_result.residual.measure == 3


def test_each_invocation_replaces_previous_output_and_ignores_supplied_results(graph_api, accepted_candidates):
    def complete(prompt):
        payload = json.loads(prompt)
        if payload["task_instruction"].startswith("NO_PROPOSAL"):
            return "null"
        return accepted_candidates[{0: 0, 1: 1, 3: 2}[len(payload["state"]["facts"])]]

    graph = build_verification_graph(qp.build_agent(qp.demo_problem(), complete=complete))
    first = graph.invoke({"task": task("first")})
    second = graph.invoke({
        "task": task("second", "NO_PROPOSAL"),
        "result": first["result"], "branch": "verified_report", "report": first["report"],
    })
    third = graph.invoke({"task": task("third")})
    assert first["report"]["task_id"] == "first"
    assert second["result"].task.id == "second"
    assert second["branch"] == "needs_review" and second["report"] is None
    assert second["result"].run_result.state.facts == ()
    assert second["result"].run_result.residual.measure == 3
    assert third["report"]["task_id"] == "third"
    assert third["result"].run_result.trace[0].before.revision == 0


@pytest.mark.parametrize("status,residual", [
    ("solved", Residual(goals=("still-open",))),
    ("error", Residual()),
])
def test_both_solved_status_and_empty_residual_are_required(graph_api, monkeypatch, status, residual):
    agent = qp.build_agent(qp.demo_problem())
    result = agent.run(task())
    inconsistent = replace(result, run_result=replace(result.run_result, status=status, residual=residual))
    monkeypatch.setattr(agent, "run", lambda requested_task: inconsistent)
    state = build_verification_graph(agent).invoke({"task": task()})
    assert state["branch"] == "needs_review" and state["report"] is None


@pytest.mark.parametrize("solves", [True, False])
def test_compiled_subgraph_blocks_parent_consumer_on_unresolved_run(graph_api, solves):
    class ParentState(VerificationState, total=False):
        consumed: bool

    graph = build_verification_graph(qp.build_agent(
        qp.demo_problem(), complete=None if solves else lambda prompt: "null",
    ))
    parent = graph_api.StateGraph(ParentState, input_schema=VerificationInput)
    parent.add_node("verify", graph)
    parent.add_node("consume_verified_report", lambda state: {"consumed": True})
    parent.add_edge(graph_api.START, "verify")
    parent.add_conditional_edges("verify", lambda state: state["branch"], {
        "verified_report": "consume_verified_report", "needs_review": graph_api.END,
    })
    parent.add_edge("consume_verified_report", graph_api.END)
    state = parent.compile().invoke({"task": task()})
    assert state.get("consumed", False) is solves
    assert (state["report"] is not None) is solves


def test_missing_optional_dependency_has_install_hint(monkeypatch):
    original = builtins.__import__

    def without_langgraph(name, *args, **kwargs):
        if name.startswith("langgraph"):
            raise ModuleNotFoundError("missing optional dependency")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_langgraph)
    with pytest.raises(ImportError, match=r"resimind\[langgraph\]"):
        build_verification_graph(qp.build_agent(qp.demo_problem()))


def test_builder_rejects_arbitrary_runner():
    with pytest.raises(TypeError, match="resimind.Agent"):
        build_verification_graph(object())


@pytest.mark.parametrize("provider,class_name", [
    ("deepseek", "DeepSeekCompletion"), ("openai", "OpenAICompletion"),
])
def test_live_cli_passes_request_limits_and_identifies_model_in_actual_graph_audit(
    graph_api, accepted_candidates, monkeypatch, capsys, provider, class_name,
):
    from examples.langgraph_optimization import main

    instances = []

    class ScriptedCompletion:
        """Synthetic proposals through the real graph, with no SDK or network."""

        def __init__(self, **configuration):
            self.configuration = configuration
            self.model = configuration["model"]
            self.calls = 0
            self.usage = {}
            self.last_error = None
            self.closed = False
            instances.append(self)

        def __call__(self, prompt):
            self.calls += 1
            payload = json.loads(prompt)
            return accepted_candidates[{0: 0, 1: 1, 3: 2}[len(payload["state"]["facts"])]]

        def close(self):
            self.closed = True

    module = importlib.import_module(f"resimind.integrations.{provider}")
    monkeypatch.setattr(module, class_name, ScriptedCompletion)
    code = main([
        "--live", "--provider", provider, "--model", "synthetic-review-model",
        "--max-calls", "5", "--max-output-tokens", "8192", "--timeout", "90", "--json",
    ])
    data = json.loads(capsys.readouterr().out)
    assert code == 0
    assert instances[0].configuration == {
        "model": "synthetic-review-model", "max_calls": 5, "max_output_tokens": 8192, "timeout": 90.0,
    }
    assert instances[0].closed
    assert data["provider"] == provider and data["model"] == "synthetic-review-model"
    assert data["model_calls"] == 3
    assert data["branch"] == "verified_report"
    assert data["audit"]["run_result"]["status"] == "solved"
    assert data["report"]["facts"] == data["audit"]["run_result"]["state"]["facts"]
