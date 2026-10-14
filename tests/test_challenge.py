from copy import deepcopy

import pytest

from resimind.challenge import baseline, oracle, score, tasks, _polynomial

pytest.importorskip("sympy")


def test_independent_oracle_and_tool_baseline():
    rows = tasks()
    assert len(rows) == len({r["id"] for r in rows}) == 28
    counts = {s: sum(oracle(t)[0] == s for t in rows) for s in ("verified", "rejected", "unresolved")}
    assert counts == {"verified": 12, "rejected": 12, "unresolved": 4}
    result = score(baseline())
    assert result["status_correct"] == 28
    assert result["false_acceptances"] == 0
    assert result["error_localization_correct"] == result["repairs_correct"] == 12


def test_positive_controls_detect_false_acceptance_and_wrong_location():
    answers = baseline()
    all_yes = [{"id": r["id"], "status": "verified", "first_error": None, "repair": None} for r in answers]
    result = score(all_yes)
    assert result["false_acceptances"] == 16
    assert result["status_correct"] == 12
    for r in answers:
        if r["status"] == "rejected":
            r["first_error"] = 3 - r["first_error"]
    result = score(answers)
    assert result["error_localization_correct"] == result["repairs_correct"] == 0


def test_original_factored_source_is_not_an_expanded_repair():
    answers = baseline()
    by_id = {t["id"]: t for t in tasks()}
    for row in answers:
        if row["status"] == "rejected":
            row["repair"] = by_id[row["id"]]["steps"][0]
    assert score(answers)["repairs_correct"] == 0


def test_unsupported_preceding_step_stops_certification():
    assert oracle({"variables": ["x"], "steps": ["x", "1/x", "x+1"]}) == ("unresolved", None)


def test_missing_answers_count_against_full_denominator():
    result = score([])
    assert result["submitted"] == result["status_correct"] == 0
    assert result["total"] == 28
    assert result["repairs_total"] == 12


@pytest.mark.parametrize("expression", ["__import__('os').system('false')", "x**100000", "x/0", "[x]", "x.__class__", "1.0*x"])
def test_scorer_never_evaluates_untrusted_python(expression):
    with pytest.raises(ValueError):
        _polynomial(expression, ["x"])


@pytest.mark.parametrize("field,value", [("first_error", True), ("model_calls", True), ("elapsed_seconds", float("nan")), ("elapsed_seconds", 10**1000), ("repair", [])])
def test_invalid_answer_fields(field, value):
    answer = deepcopy(baseline()[1])
    answer[field] = value
    with pytest.raises(ValueError):
        score([answer])


def test_duplicate_answers_are_not_double_counted():
    row = baseline()[0]
    with pytest.raises(ValueError, match="duplicate"):
        score([row, row])
