"""Independent scorer and frozen transfer-study regression tests."""
import json

import pytest

from benchmarks.knowledge_growth import DISCOVERY, TRANSFER, run_study, score_expansion


@pytest.mark.parametrize("lhs,rhs,variables,points", [
    ("(x+y)**2", "x*x+2*x*y+y*y", ("x", "y"), 9),
    ("x/3+x/6", "x/2", ("x",), 2),
    ("(x+2*x)**3", "27*x*x*x", ("x",), 4),
    ("(-x+2*y)**2", "x*x-4*x*y+4*y*y", ("x", "y"), 9),
    ("7/3", "14/6", (), 1),
])
def test_grid_oracle_certifies_with_exact_degree_complete_points(lhs, rhs, variables, points):
    result = score_expansion(lhs, rhs, variables)
    assert result["status"] == "valid"
    assert result["points_checked"] == result["required_points"] == points


def test_grid_oracle_does_not_confuse_a_few_passing_samples_with_identity():
    # This false identity happens to pass the three common sample points -1,0,1.
    result = score_expansion("x*(x-1)*(x+1)", "0", ("x",))
    assert result["status"] == "invalid"
    assert result["required_points"] == 4
    assert result["counterexample"]["x"] not in (-1, 0, 1)


def test_equivalent_but_unfinished_expression_does_not_score_as_success():
    assert score_expansion("(x+y)**2", "(x+y)**2", ("x", "y"))["status"] == "incomplete"
    assert score_expansion("x*(y+1)", "x*(y+1)", ("x", "y"))["status"] == "incomplete"


@pytest.mark.parametrize("expression", [
    "x/x", "x/0", "x**-1", "x**17", "float(x)", "x.real", "x[0]", "True", "1.0", "z", "x//2",
    "((2**16)**16)**16",
])
def test_unsupported_or_invalid_oracle_inputs_are_unknown(expression):
    assert score_expansion(expression, "0", ("x",))["status"] == "unknown"


def test_grid_cap_defers_without_scoring_partial_samples():
    result = score_expansion("(x+y+z+w)**8", "0", ("x", "y", "z", "w"), max_points=100)
    assert result["status"] == "unknown"
    assert result["points_checked"] == 0
    with pytest.raises(ValueError):
        score_expansion("x", "x", ("x",), max_points=True)


def test_full_study_freezes_library_scores_all_tasks_and_counts_actual_reuse():
    result = run_study()
    assert result["model_calls"] == 0
    assert result["library"]["reload_reverified"]
    assert result["library"]["frozen_during_transfer"]
    assert result["library"]["verified_rules"] == 2
    assert result["knowledge_verifier_calls"] == {
        "discovery_admission": 2, "reload_reverification": 2, "transfer_admission": 0,
    }
    assert {case.expression for case in DISCOVERY}.isdisjoint(case.expression for case in TRANSFER)
    assert len(result["transfer"]) == len(TRANSFER) == 10
    for arm in ("fixed", "growing"):
        rows = [row[arm] for row in result["transfer"]]
        totals = result["totals"][arm]
        assert totals["successes"] == len(rows)
        assert totals["accepted_steps"] == sum(row["accepted_steps"] for row in rows)
        assert totals["work"]["proposal_calls"] == sum(row["work"]["proposal_calls"] for row in rows)
        assert all(row["oracle"]["status"] == "valid" and not row["admissions"] and not row["learning_errors"]
                   for row in rows)
    assert result["totals"]["fixed"]["cross_task_rule_uses"] == 0
    assert result["totals"]["growing"]["tasks_with_cross_task_reuse"] == 8
    known = {rule["id"]: rule for rule in result["library"]["rules"]}
    for row in result["transfer"]:
        for use in row["growing"]["committed_rule_uses"]:
            assert use["rule_id"] in known
            assert use["fingerprint"] == known[use["rule_id"]]["fingerprint"]
            assert use["source_task_id"] != row["task_id"]
    for row in result["transfer"][-2:]:
        assert row["fixed"]["accepted_steps"] == row["growing"]["accepted_steps"]
        assert not row["growing"]["committed_rule_uses"]
    assert result["admission_safety_probes"]["wrong_admissions"] == 0
    assert result["admission_safety_probes"]["probes"] == 6
    # Stable snapshots make repeatability and accidental transfer mutation visible.
    assert result == run_study()
    json.dumps(result, allow_nan=False)


def test_incomplete_discovery_never_fabricates_rules_or_removes_transfer_failures():
    result = run_study(max_steps=1)
    assert result["discovery_totals"]["successes"] == 0
    assert result["library"]["verified_rules"] == 0
    assert len(result["transfer"]) == 10
    assert result["totals"]["fixed"]["successes"] < 10
    assert result["totals"]["fixed"] == result["totals"]["growing"]
