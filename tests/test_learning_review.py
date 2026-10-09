"""Independent regressions for learned-rule applicability and rational rewrites."""

import pytest

from resimind.agent import Task
from resimind.core import Decision
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import PolynomialProblem, build_learning_agent
from resimind.knowledge import KnowledgeCandidate, KnowledgeLibrary


def _library():
    return KnowledgeLibrary({"algebra": AlgebraVerifier()})


def _run(expression, variables, library):
    return build_learning_agent(
        PolynomialProblem(expression, variables), library, max_steps=32,
    ).run(Task("transfer-review", "Expand and verify", "algebra"), learn=False)


def test_unbound_rhs_parameter_cannot_block_primitive_fallback():
    library = _library()
    lhs = "(u+v)**2"
    rhs = "u*u+2*u*v+v*v+0*w"
    candidate = KnowledgeCandidate(
        id="extra-symbol", domain="algebra", kind="polynomial_identity",
        statement={"lhs": lhs, "rhs": rhs, "variables": ["u", "v", "w"]},
        derivation=({"lhs": lhs, "rhs": rhs},), source_task_id="discovery-review",
    )
    assert library.admit(candidate).status == "verified"
    fixed = _run("(x+y)**2", ("x", "y"), _library())
    grown = _run("(x+y)**2", ("x", "y"), library)
    assert fixed.result.run_result.status == "solved"
    assert grown.result.run_result.status == "solved"
    assert not grown.learning_errors


def test_rejected_oversized_rule_instantiation_falls_back_to_primitives():
    def balanced_zero_terms(count):
        if count == 1:
            return "0*u"
        left = count // 2
        return f"({balanced_zero_terms(left)}+{balanced_zero_terms(count - left)})"

    library = _library()
    lhs = "(u+v)**2"
    rhs = "u*u+2*u*v+v*v+" + balanced_zero_terms(90)
    candidate = KnowledgeCandidate(
        id="bloated-but-proved", domain="algebra", kind="polynomial_identity",
        statement={"lhs": lhs, "rhs": rhs, "variables": ["u", "v"]},
        derivation=({"lhs": lhs, "rhs": rhs},), source_task_id="discovery-review",
    )
    # The generic certificate is valid and within limits, but substitution of
    # x*x*x*x for u makes its repeated zero terms exceed the target AST budget.
    assert library.admit(candidate).status == "verified"
    outcome = _run("(x*x*x*x+y)**2", ("x", "y"), library)
    run = outcome.result.run_result
    assert run.status == "solved"
    assert run.trace[0].decision is Decision.REJECT
    assert sum(event.decision is Decision.REJECT for event in run.trace) == 1
    assert all(fact.value[2] == "" for fact in run.state.facts)
    assert not outcome.learning_errors


@pytest.mark.parametrize("expression", [
    "+(x+y)*z", "((x+y)/2)*z", "(x+y)*((z+1)/2)", "((x+y)/2)**2",
])
def test_supported_rational_and_unary_grammar_can_finish_expansion(expression):
    result = _run(expression, ("x", "y", "z"), _library())
    assert result.result.run_result.status == "solved"
    assert not result.learning_errors
