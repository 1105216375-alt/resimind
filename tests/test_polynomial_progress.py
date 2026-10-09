"""Progress recognition is bounded syntax analysis, separate from truth checks."""
import pytest

from resimind.domains import algebra, polynomial_learning
from resimind.domains.polynomial_progress import MAX_LOCAL_PROGRESS_PROBES, assess_polynomial_progress


@pytest.mark.parametrize("after", [
    "x*(x+3)+2*(x+3)",
    "(x+2)*x+(x+2)*3",
])
def test_both_distribution_directions_are_progress_even_when_size_and_goals_grow(after):
    result = assess_polynomial_progress("(x+2)*(x+3)", after, ("x",))
    assert result.classification == "structural"
    assert result.reason == "recognized_local_expansion"
    assert result.after["ast_nodes"] > result.before["ast_nodes"]
    assert result.after["remaining_actions"] > result.before["remaining_actions"]
    assert result.heuristic_only and 0 < result.local_probes <= MAX_LOCAL_PROGRESS_PROBES


def test_sign_notation_is_distinguished_from_reordering_or_regrouping():
    sign = assess_polynomial_progress("(2*x+(-3))**2", "(2*x-3)**2", ("x",))
    swap = assess_polynomial_progress("(x+2)*(x+3)", "(x+3)*(x+2)", ("x",))
    grouping = assess_polynomial_progress("((x+1)*(x+2))*(x+3)", "(x+1)*((x+2)*(x+3))", ("x",))
    assert sign.classification == "cosmetic" and sign.after["ast_nodes"] < sign.before["ast_nodes"]
    assert swap.classification == grouping.classification == "uncertain"
    assert sign.unexpanded_subexpressions["items"][0]["expression"]


def test_progress_assessment_does_not_generate_answers_using_an_identity_oracle(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("progress must not run a mathematical verifier")

    monkeypatch.setattr(algebra, "verify_identity", forbidden)
    monkeypatch.setattr(algebra, "validate_expression", forbidden)
    monkeypatch.setattr(polynomial_learning, "verify_identity", forbidden)
    result = assess_polynomial_progress("(x+2)**3", "(x+2)*(x+2)**2", ("x",))
    assert result.classification == "structural"
    assert result.reason == "recognized_local_expansion"
    assert result.heuristic_only
