"""The generic architecture example is deterministic and offline."""

from examples.general_architecture import run_demo


def test_general_architecture_demo_runs_without_model_credentials():
    result = run_demo()
    assert result["ready_before"] == ["input"]
    assert result["ready_after"] == ["derive"]
    assert result["strategy_order"] == ["symbolic", "model"]
    assert result["applied_rule"] == "(u)*(w)+(v)*(w)"
    assert len(result["rule_id"]) > len("schema:")
