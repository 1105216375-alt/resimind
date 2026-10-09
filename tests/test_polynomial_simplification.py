"""Local candidates save syntax while an independent checker establishes truth."""
import ast

import pytest

from resimind.domains import algebra
from resimind.domains.polynomial_learning import is_expanded
from resimind.domains.polynomial_simplification import (
    MAX_DISTRIBUTION_PAIRS, bounded_distribute, compact_expression,
    expression_complexity,
)


def verified(before, after, variables=("x",)):
    assert after is not None
    assert algebra.verify_identity(before, after, variables).status == "verified"
    return after


@pytest.mark.parametrize("expression,variables,answer", [
    ("x+x+x-x", ("x",), "2*x"),
    ("x*x*x*x+2*x*x*x*x-x*x*x*x", ("x",), "2*x**4"),
    ("(2*x/3)*(3*x/4)+(x*x)/2", ("x",), "x**2"),
    ("-(x*x+2*x)+x*x+3*x", ("x",), "x"),
    ("x/(-2)+x/3+x/6", ("x",), "0"),
    ("2*x*y+3*y*x-x*y + y*y + 2*y*y", ("x", "y"), "4*x*y+3*y**2"),
    ("2*3*x*4*y/(-6)", ("x", "y"), "-4*x*y"),
    ("(x*x)/2-(x*x)/2+7-7", ("x",), "0"),
    ("(x+1)**0 + (x+1)**0", ("x",), "2"),
    ("(2*x*y)**2 + (2*x*y)**2", ("x", "y"), "8*x**2*y**2"),
    ("(1/2+1/3+1/6)*x", ("x",), "x"),
    ("(x+x+y+y)/(-2)", ("x", "y"), "-x-y"),
])
def test_compaction_exact_arithmetic_and_like_terms(expression, variables, answer):
    result = verified(expression, compact_expression(expression, variables), variables)
    verified(result, answer, variables)
    before, after = expression_complexity(expression, variables), expression_complexity(result, variables)
    assert (after.ast_nodes, after.source_length) < (before.ast_nodes, before.source_length)
    assert compact_expression(result, variables) is None


def test_compaction_preserves_unexpanded_products_and_their_siblings():
    expression = "(x*x*x+x*x*x)*(y+1)*(y+2)"
    result = verified(expression, compact_expression(expression, ("x", "y")), ("x", "y"))
    assert not is_expanded(result)
    assert algebra.verify_identity(result, "2*x**3*(y+1)*(y+2)", ("x", "y")).status == "verified"
    assert bounded_distribute("(x+1)*(x+2)", ("x",)) is not None
    assert compact_expression("(x+1)*(x+2)", ("x",)) is None


def test_compaction_combines_coefficients_around_opaque_factors_without_expanding():
    expression = "3*x*(x+1)*2*x"
    result = verified(expression, compact_expression(expression, ("x",)))
    verified(result, "6*x**2*(x+1)")
    assert not is_expanded(result)


@pytest.mark.parametrize("expression", [
    "x**16*x**16+x**16*x**16",
    "(x**8*x**8)*(x**8*x**8)",
])
def test_power_compaction_keeps_individual_exponents_at_most_sixteen(expression):
    result = verified(expression, compact_expression(expression, ("x",)))
    for node in ast.walk(ast.parse(result, mode="eval")):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            assert isinstance(node.right, ast.Constant)
            assert node.right.value <= algebra.MAX_EXPONENT
    assert is_expanded(result)


@pytest.mark.parametrize("expression", [
    "x/0", "x/(-0)", "x/(1+1)", "x/x", "x**-1", "x**17", "x**x",
    "sin(x)", "x.real", "x[0]", "__import__('os')", "True+x", "1.5+x",
    "[x]", "{x}", "x//2", "x%2", "x if x else 0", "y+x", "x+(",
    "0*(x/0)", "0*sin(x)", "sin(x)**0", "0*(y+1)",
    "x**16*x**16*x", str(2**128),
])
def test_invalid_syntax_cannot_disappear_behind_zero_or_a_power(expression):
    assert compact_expression(expression, ("x",)) is None
    assert bounded_distribute(expression, ("x",)) is None
    with pytest.raises(ValueError):
        expression_complexity(expression, ("x",))


@pytest.mark.parametrize("variables", [("x", "x"), ("for",), ("_x",), ("变量",), "x", (1,), [[], "x"]])
def test_variable_declarations_are_bounded_and_validated(variables):
    assert compact_expression("x+x", variables) is None
    assert bounded_distribute("(x+1)*(x+2)", variables) is None


def test_input_and_arithmetic_resource_limits_fail_closed():
    too_long = "x" + " " * algebra.MAX_SOURCE_LENGTH
    too_deep = "+" * (algebra.MAX_AST_DEPTH + 2) + "x"
    too_many_nodes = "+".join("x" for _ in range(algebra.MAX_AST_NODES))
    huge_fraction = "(" + str(2**127) + ")**16*x"
    for expression in (too_long, too_deep, too_many_nodes, huge_fraction):
        assert compact_expression(expression, ("x",)) is None
        assert bounded_distribute(expression, ("x",)) is None


def test_valid_large_internal_coefficient_is_not_emitted_as_an_illegal_literal():
    expression = f"({2**100}*{2**100})*x"
    assert algebra.verify_identity(expression, expression, ("x",)).status == "verified"
    # 201-bit product is permitted internally, but an output literal is capped at 128 bits.
    assert compact_expression(expression, ("x",)) is None
    assert bounded_distribute(f"({2**100}*x+1)*({2**100}*x+1)", ("x",)) is None


def test_helpers_do_not_use_the_verifier_as_a_generation_oracle(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("candidate generation called the verifier")
    monkeypatch.setattr(algebra, "verify_identity", forbidden)
    monkeypatch.setattr(algebra, "validate_expression", forbidden)
    monkeypatch.setattr(algebra._Ring, "read", forbidden)
    assert compact_expression("x*x+x*x", ("x",)) is not None
    assert bounded_distribute("(x+1)*(x+2)*(x+3)", ("x",)) is not None


@pytest.mark.parametrize("factors", [4, 6, 8])
def test_larger_factor_products_finish_in_bounded_local_steps(factors):
    expression = "*".join(f"(x+{index})" for index in range(1, factors + 1))
    current, actions, largest = expression, 0, 0
    while not is_expanded(current):
        candidate = bounded_distribute(current, ("x",))
        verified(current, candidate)
        current = candidate
        actions += 1
        metrics = expression_complexity(current, ("x",))
        largest = max(largest, metrics.ast_nodes)
        assert metrics.ast_nodes <= algebra.MAX_AST_NODES
        assert metrics.depth <= algebra.MAX_AST_DEPTH
        assert metrics.source_length <= algebra.MAX_SOURCE_LENGTH
        assert actions < 16
    assert actions == factors - 1
    assert largest < 160
    verified(expression, current)


def test_one_distribution_does_not_expand_all_factors():
    expression = "(x+1)*(x+2)*(x+3)*(x+4)"
    result = verified(expression, bounded_distribute(expression, ("x",)))
    assert not is_expanded(result)
    verified(result, "(x**2+3*x+2)*(x+3)*(x+4)")
    assert expression_complexity(result, ("x",)).expandable_products > 0


@pytest.mark.parametrize("expression,variables", [
    ("(x+1)**8", ("x",)),
    ("(x+y+1)*(x-y+2)*(2*x+y-3)", ("x", "y")),
    ("(x/2+1/3)*(x/3-1/2)*(x+2)", ("x",)),
    ("(-x-1)*(-(x+2))*(x+3)", ("x",)),
])
def test_distributions_handle_powers_signs_fractions_and_multiple_variables(expression, variables):
    current = expression
    for _ in range(16):
        if is_expanded(current):
            break
        current = verified(current, bounded_distribute(current, variables), variables)
    assert is_expanded(current)
    verified(expression, current, variables)


def test_pair_budget_counts_distinct_local_terms_and_is_enforced():
    expression = "(x+y+z+1)*(x+y+z+2)"
    assert bounded_distribute(expression, ("x", "y", "z"), max_pairs=15) is None
    verified(expression, bounded_distribute(expression, ("x", "y", "z"), max_pairs=16), ("x", "y", "z"))
    assert MAX_DISTRIBUTION_PAIRS == 256
    for limit in (0, -1, 257, True, 1.5, None):
        with pytest.raises(ValueError):
            bounded_distribute(expression, ("x", "y", "z"), max_pairs=limit)


def test_within_pair_budget_still_cannot_exceed_whole_expression_ast_limit():
    left_names = tuple(f"x{index}" for index in range(8))
    right_names = tuple(f"y{index}" for index in range(8))
    names = left_names + right_names
    left = "+".join(f"{name}+{name}**2" for name in left_names)
    right = "+".join(f"{name}+{name}**2" for name in right_names)
    expression = f"({left})*({right})"
    assert expression_complexity(expression, names).ast_nodes < algebra.MAX_AST_NODES
    assert algebra.verify_identity(expression, expression, names).status == "verified"
    # 16 x 16 pairs fit the operation budget; the 256 distinct products do not fit the AST.
    assert bounded_distribute(expression, names) is None


def test_distribution_cancels_to_zero_without_retaining_zero_terms():
    expression = "(x-x)*(x+1)"
    result = verified(expression, bounded_distribute(expression, ("x",)))
    assert result == "0"


def test_already_compact_expression_has_no_rewrite():
    for expression in ("x", "0", "2 * x ** 3", "x ** 16 * x ** 16"):
        assert compact_expression(expression, ("x",)) is None
        assert bounded_distribute(expression, ("x",)) is None
