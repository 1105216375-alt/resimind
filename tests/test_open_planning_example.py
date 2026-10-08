"""Open-ended plans remain blocked on unknown data or failed live requests."""

import json

import pytest

from examples import open_planning as example
from resimind.integrations import deepseek


@pytest.mark.parametrize("scenario", ["day-out", "rain", "missing-travel"])
def test_offline_planning_keeps_model_calls_disabled(monkeypatch, capsys, scenario):
    def forbidden(*args, **kwargs):
        raise AssertionError("offline demonstration attempted model access")

    monkeypatch.setattr(deepseek, "DeepSeekCompletion", forbidden)
    code = example.main(["--scenario", scenario, "--json"])
    record = json.loads(capsys.readouterr().out)
    assert record["mode"] == "offline-deterministic"
    assert code == (1 if scenario == "missing-travel" else 0)
    if scenario == "missing-travel":
        assert record["plan"] is None
    else:
        assert record["plan"]["total_cost_cents"] == 6100
        assert "rationale" not in record["plan"]


def test_failed_live_planning_never_substitutes_the_offline_itinerary(monkeypatch, capsys):
    class UnavailableCompletion:
        model = "explicit-test-model"
        calls = 0
        usage = {}
        last_error = "DeepSeek request failed."
        closed = False

        def __call__(self, prompt):
            self.calls += 1
            raise deepseek.DeepSeekCompletionError(self.last_error)

        def close(self):
            self.closed = True

    complete = UnavailableCompletion()
    monkeypatch.setattr(deepseek, "DeepSeekCompletion", lambda *args, **kwargs: complete)
    code = example.main(["--live", "--model", complete.model, "--json"])
    record = json.loads(capsys.readouterr().out)
    assert code == 1 and complete.closed
    assert record["mode"] == "live-deepseek" and record["api_calls"] == 1
    assert record["plan"] is None
    assert record["agent"]["run_result"]["state"]["facts"] == []
    assert record["agent"]["run_result"]["status"] == "error"
