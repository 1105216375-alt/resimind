"""CLI boundary: unfinished or failed live proposals never become a quote."""

import json

import pytest

from examples import customer_support as example
from resimind.integrations import deepseek


@pytest.mark.parametrize("scenario", ["refund", "missing-delivery", "expired"])
def test_offline_example_does_not_construct_a_model(monkeypatch, capsys, scenario):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline example constructed a live model")

    monkeypatch.setattr(deepseek, "DeepSeekCompletion", forbidden)
    code = example.main(["--scenario", scenario, "--json"])
    record = json.loads(capsys.readouterr().out)
    assert record["mode"] == "offline-deterministic"
    assert code == (1 if scenario == "missing-delivery" else 0)
    if scenario == "missing-delivery":
        assert record["resolution"] is None
    else:
        assert record["resolution"]["payment_executed"] is False
        assert record["resolution"]["refund_cents"] == (24900 if scenario == "refund" else None)


def test_failed_live_example_stays_unresolved_without_fixture_fallback(monkeypatch, capsys):
    class UnavailableCompletion:
        model = "explicit-test-model"
        calls = 0
        usage = {}
        last_error = "DeepSeek request failed."
        closed = False

        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, prompt):
            self.calls += 1
            raise deepseek.DeepSeekCompletionError(self.last_error)

        def close(self):
            self.closed = True

    complete = UnavailableCompletion()
    monkeypatch.setattr(deepseek, "DeepSeekCompletion", lambda *args, **kwargs: complete)
    code = example.main(["--live", "--model", complete.model, "--json"])
    record = json.loads(capsys.readouterr().out)
    assert code == 1
    assert complete.closed
    assert record["mode"] == "live-deepseek" and record["model"] == complete.model
    assert record["api_calls"] == 1
    assert record["resolution"] is None
    assert record["agent"]["run_result"]["state"]["facts"] == []
    assert record["agent"]["run_result"]["status"] == "error"


@pytest.mark.parametrize("scenario", ["refund", "missing-delivery", "expired"])
def test_customer_support_uses_the_existing_langgraph_gate(scenario):
    pytest.importorskip("langgraph.graph")
    from resimind import Task
    from resimind.domains.customer_support import DOMAIN, build_agent, demo_case, verified_resolution
    from resimind.integrations.langgraph import build_verification_graph

    graph = build_verification_graph(build_agent(demo_case(scenario)))
    outcome = graph.invoke({"task": Task("support-graph", "Check the return request.", DOMAIN)})
    if scenario == "missing-delivery":
        assert outcome["branch"] == "needs_review"
        assert outcome["report"] is None
        assert not outcome["result"].run_result.residual.solved
    else:
        assert outcome["branch"] == "verified_report"
        resolution = verified_resolution(outcome["result"])
        assert resolution["outcome"] == ("refund_recommended" if scenario == "refund" else "human_review")
        assert outcome["report"]["facts"] == [fact.to_dict() for fact in outcome["result"].run_result.state.facts]
