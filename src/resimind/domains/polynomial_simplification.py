"""Bounded local polynomial candidates, independent of the identity verifier.

Compaction collects terms that are already expanded; it never distributes a
product across a sum. ``bounded_distribute`` separately performs one local
multiplication of already expanded operands, with a small pair budget. Neither
function proves anything: callers must verify the returned whole expression
against its evidence-bound predecessor before committing it.

There is no coefficient-oracle, CAS, ``eval``, or external dependency here.
The grammar and output limits match the exact verifier, but validation here is
syntactic and deliberately does not call that verifier or expand its ring.
"""
from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
import keyword
import re

from .algebra import (MAX_AST_DEPTH, MAX_AST_NODES, MAX_COEFFICIENT_BITS,
                      MAX_DEGREE, MAX_EXPONENT, MAX_INTEGER_BITS,
                      MAX_SOURCE_LENGTH, MAX_VARIABLES)

MAX_DISTRIBUTION_PAIRS = 256
MAX_OPERATIONS = 32_768
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_Powers = tuple[int, ...]
_Terms = dict[_Powers, Fraction]


class SimplificationLimit(ValueError):
    """Unsupported syntax or an exhausted bound; no candidate is authorized."""


@dataclass(frozen=True, slots=True)
class ExpressionComplexity:
    """Syntactic costs, not proof difficulty or exact expanded term counts.

    ``additive_terms`` is one plus the number of addition/subtraction nodes.
    ``expandable_products`` counts products or powers enclosing an addition.
    AST counts include operator/context nodes, just as the verifier does.
    """

    ast_nodes: int
    depth: int
    source_length: int
    expandable_products: int
    additive_terms: int


@dataclass
class _Budget:
    remaining: int = MAX_OPERATIONS

    def spend(self, amount: int = 1) -> None:
        self.remaining -= amount
        if self.remaining < 0:
            raise SimplificationLimit("local simplification exceeds operation budget")


def _integer(node: ast.AST) -> int:
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if (not isinstance(node, ast.Constant) or type(node.value) is not int
            or node.value.bit_length() > MAX_INTEGER_BITS):
        raise SimplificationLimit("a bounded integer literal is required")
    return sign * node.value


def _names(variables: Sequence[str]) -> tuple[str, ...]:
    if (not isinstance(variables, (tuple, list)) or len(variables) > MAX_VARIABLES
            or any(type(name) is not str or not _NAME.fullmatch(name)
                   or keyword.iskeyword(name) for name in variables)
            or len(set(variables)) != len(variables)):
        raise SimplificationLimit("variables must be distinct bounded ASCII identifiers")
    return tuple(variables)


def _parse(expression: str, variables: Sequence[str]) -> tuple[ast.expr, tuple[str, ...]]:
    names = _names(variables)
    if type(expression) is not str or not expression.strip() or len(expression) > MAX_SOURCE_LENGTH:
        raise SimplificationLimit("expression exceeds source bound or is empty")
    try:
        root = ast.parse(expression.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise SimplificationLimit("unsupported expression syntax") from exc
    stack, count = [(root, 0)], 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise SimplificationLimit("expression exceeds AST size or depth limit")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))

    def validate(node: ast.AST) -> int:
        if isinstance(node, ast.Constant):
            _integer(node)
            return 0
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise SimplificationLimit("undeclared variable")
            return 1
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return validate(node.operand)
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div):
                denominator = _integer(node.right)
                if not denominator:
                    raise SimplificationLimit("division by zero")
                return validate(node.left)
            if isinstance(node.op, ast.Pow):
                exponent = _integer(node.right)
                if not 0 <= exponent <= MAX_EXPONENT:
                    raise SimplificationLimit("exponent exceeds supported bound")
                degree = validate(node.left) * exponent
            elif isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                left, right = validate(node.left), validate(node.right)
                degree = left + right if isinstance(node.op, ast.Mult) else max(left, right)
            else:
                raise SimplificationLimit("unsupported polynomial operator")
            if degree > MAX_DEGREE:
                raise SimplificationLimit("syntactic polynomial degree exceeds bound")
            return degree
        raise SimplificationLimit("unsupported polynomial syntax")

    validate(root)
    return root, names


def _cost(root: ast.AST, source_length: int) -> ExpressionComplexity:
    stack, count, depth, expandable, additions = [(root, 0)], 0, 0, 0, 0
    while stack:
        node, level = stack.pop()
        count, depth = count + 1, max(depth, level)
        if isinstance(node, ast.BinOp):
            additions += isinstance(node.op, (ast.Add, ast.Sub))
            if isinstance(node.op, (ast.Mult, ast.Pow)):
                operands = (node.left,) if isinstance(node.op, ast.Pow) else (node.left, node.right)
                expandable += any(isinstance(part, ast.BinOp)
                                  and isinstance(part.op, (ast.Add, ast.Sub))
                                  for operand in operands for part in ast.walk(operand))
        stack.extend((child, level + 1) for child in ast.iter_child_nodes(node))
    return ExpressionComplexity(count, depth, source_length, expandable, additions + 1)


def expression_complexity(expression: str, variables: Sequence[str]) -> ExpressionComplexity:
    """Return bounded syntax metrics; unsupported inputs raise ``ValueError``."""
    root, _ = _parse(expression, variables)
    return _cost(root, len(expression))


def _coefficient(value: Fraction) -> Fraction:
    if (abs(value.numerator).bit_length() > MAX_COEFFICIENT_BITS
            or value.denominator.bit_length() > MAX_COEFFICIENT_BITS):
        raise SimplificationLimit("coefficient exceeds arithmetic precision bound")
    return value


def _mul(left: tuple[Fraction, _Powers], right: tuple[Fraction, _Powers],
         budget: _Budget) -> tuple[Fraction, _Powers]:
    budget.spend()
    powers = tuple(a + b for a, b in zip(left[1], right[1]))
    if sum(powers) > MAX_DEGREE:
        raise SimplificationLimit("monomial exceeds total degree bound")
    return _coefficient(left[0] * right[0]), powers


def _monomial(node: ast.AST, names: tuple[str, ...], budget: _Budget
              ) -> tuple[Fraction, _Powers] | None:
    """Read one syntactic monomial, never a sum or a sum-containing product."""
    budget.spend()
    zero = (0,) * len(names)
    if isinstance(node, ast.Constant):
        return Fraction(_integer(node)), zero
    if isinstance(node, ast.Name):
        return Fraction(1), tuple(int(name == node.id) for name in names)
    if isinstance(node, ast.UnaryOp):
        item = _monomial(node.operand, names, budget)
        return None if item is None else ((-item[0] if isinstance(node.op, ast.USub) else item[0]), item[1])
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Div):
            item = _monomial(node.left, names, budget)
            return None if item is None else (_coefficient(item[0] / _integer(node.right)), item[1])
        if isinstance(node.op, ast.Mult):
            left, right = _monomial(node.left, names, budget), _monomial(node.right, names, budget)
            return None if left is None or right is None else _mul(left, right, budget)
        if isinstance(node.op, ast.Pow):
            exponent = _integer(node.right)
            if exponent == 0:
                return Fraction(1), zero
            item = _monomial(node.left, names, budget)
            if item is None:
                return None
            result = (Fraction(1), zero)
            for _ in range(exponent):
                result = _mul(result, item, budget)
            return result
    return None


def _add(terms: _Terms, powers: _Powers, value: Fraction, budget: _Budget) -> None:
    budget.spend()
    result = _coefficient(terms.get(powers, Fraction(0)) + value)
    if result:
        terms[powers] = result
    else:
        terms.pop(powers, None)
    if len(terms) > MAX_DISTRIBUTION_PAIRS:
        raise SimplificationLimit("local term count exceeds bound")


def _expanded(node: ast.AST, names: tuple[str, ...], budget: _Budget) -> _Terms | None:
    """Collect an already expanded subtree; multiplying sums returns None."""
    budget.spend()
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        left, right = _expanded(node.left, names, budget), _expanded(node.right, names, budget)
        if left is None or right is None:
            return None
        sign = -1 if isinstance(node.op, ast.Sub) else 1
        for powers, coefficient in right.items():
            _add(left, powers, sign * coefficient, budget)
        return left
    if isinstance(node, ast.UnaryOp):
        terms = _expanded(node.operand, names, budget)
        if terms is None:
            return None
        return {power: -value for power, value in terms.items()} if isinstance(node.op, ast.USub) else terms
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        terms = _expanded(node.left, names, budget)
        if terms is None:
            return None
        return {power: _coefficient(value / _integer(node.right)) for power, value in terms.items()}
    term = _monomial(node, names, budget)
    return None if term is None else ({term[1]: term[0]} if term[0] else {})


def _integer_node(value: int) -> ast.expr:
    if abs(value).bit_length() > MAX_INTEGER_BITS:
        raise SimplificationLimit("compacted coefficient cannot fit a permitted output literal")
    return ast.UnaryOp(ast.USub(), ast.Constant(-value)) if value < 0 else ast.Constant(value)


def _balanced(nodes: list[ast.expr], operator: type[ast.operator], empty: int) -> ast.expr:
    if not nodes:
        return ast.Constant(empty)
    while len(nodes) > 1:
        nodes = [ast.BinOp(nodes[index], operator(), nodes[index + 1])
                 if index + 1 < len(nodes) else nodes[index] for index in range(0, len(nodes), 2)]
    return nodes[0]


def _term_node(coefficient: Fraction, powers: _Powers, names: tuple[str, ...]) -> ast.expr:
    if not coefficient:
        return ast.Constant(0)
    factors: list[ast.expr] = []
    for name, exponent in zip(names, powers):
        # The verifier caps each exponent at 16, even though total degree is 32.
        while exponent:
            chunk = min(exponent, MAX_EXPONENT)
            variable = ast.Name(name, ast.Load())
            factors.append(variable if chunk == 1 else ast.BinOp(variable, ast.Pow(), ast.Constant(chunk)))
            exponent -= chunk
    magnitude = abs(coefficient)
    if magnitude != 1 or not factors:
        literal = _integer_node(magnitude.numerator)
        if magnitude.denominator != 1:
            literal = ast.BinOp(literal, ast.Div(), _integer_node(magnitude.denominator))
        factors.insert(0, literal)
    result = _balanced(factors, ast.Mult, 1)
    return ast.UnaryOp(ast.USub(), result) if coefficient < 0 else result


def _terms_node(terms: _Terms, names: tuple[str, ...]) -> ast.expr:
    ordering = sorted(terms, key=lambda power: (-sum(power), tuple(-item for item in power)))
    return _balanced([_term_node(terms[power], power, names) for power in ordering], ast.Add, 0)


def _simplify(node: ast.expr, names: tuple[str, ...], budget: _Budget) -> ast.expr:
    budget.spend()
    if isinstance(node, ast.UnaryOp):
        node.operand = _simplify(node.operand, names, budget)
    elif isinstance(node, ast.BinOp):
        node.left = _simplify(node.left, names, budget)
        # Exponents/denominators remain signed integer literals, not general expressions.
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            node.right = _simplify(node.right, names, budget)
    terms = _expanded(node, names, budget)
    if terms is not None:
        return _terms_node(terms, names)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) and _integer(node.right) == 1:
        return node.left
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        factors = []

        def flatten(item: ast.expr) -> None:
            if isinstance(item, ast.BinOp) and isinstance(item.op, ast.Mult):
                flatten(item.left)
                flatten(item.right)
            else:
                factors.append(item)

        flatten(node)
        combined = (Fraction(1), (0,) * len(names))
        opaque = []
        for factor in factors:
            term = _monomial(factor, names, budget)
            if term is None:
                opaque.append(factor)
            else:
                combined = _mul(combined, term, budget)
        if not combined[0]:
            return ast.Constant(0)
        if combined != (Fraction(1), (0,) * len(names)):
            opaque.insert(0, _term_node(*combined, names))
        return _balanced(opaque, ast.Mult, 1)
    return node


def _render(node: ast.expr, names: tuple[str, ...]) -> tuple[str, ast.expr]:
    # Bound materialized output before unparsing or reparsing it.
    if sum(1 for _ in ast.walk(node)) > MAX_AST_NODES:
        raise SimplificationLimit("candidate exceeds AST size limit")
    text = ast.unparse(ast.fix_missing_locations(node))
    checked, _ = _parse(text, names)
    return text, checked


def compact_expression(expression: str, variables: Sequence[str]) -> str | None:
    """Propose a strictly smaller expression using only local simplifications.

    Size is lexicographic ``(AST nodes, source length)``. Unsupported inputs,
    precision/resource exhaustion, and non-improvements return ``None``.
    No multiplication of a sum occurs here. Output remains only a candidate.
    """
    try:
        root, names = _parse(expression, variables)
        before = _cost(root, len(expression))
        text, result = _render(_simplify(root, names, _Budget()), names)
        after = _cost(result, len(text))
        return text if (after.ast_nodes, after.source_length) < (before.ast_nodes, before.source_length) else None
    except (SimplificationLimit, RecursionError):
        return None


def bounded_distribute(expression: str, variables: Sequence[str], *,
                       max_pairs: int = MAX_DISTRIBUTION_PAIRS) -> str | None:
    """Propose one deepest local distribution, collecting products as they form.

    Both multiplicands must already be expanded. A power of an expanded sum
    performs at most one square/product and leaves any remaining power intact.
    ``max_pairs`` is restricted to 1..256. No recursive full-expression
    expansion is performed, and no candidate bypasses independent verification.
    """
    if type(max_pairs) is not int or not 1 <= max_pairs <= MAX_DISTRIBUTION_PAIRS:
        raise ValueError("max_pairs must be an integer between 1 and 256")
    try:
        root, names = _parse(expression, variables)
        budget = _Budget()

        def multiply(left: _Terms, right: _Terms) -> ast.expr | None:
            if len(left) * len(right) > max_pairs:
                return None
            result: _Terms = {}
            for lp, lc in left.items():
                for rp, rc in right.items():
                    coefficient, powers = _mul((lc, lp), (rc, rp), budget)
                    _add(result, powers, coefficient, budget)
            return _terms_node(result, names)

        def visit(node: ast.expr) -> ast.expr | None:
            budget.spend()
            # Deepest eligible work prevents repeatedly duplicating large siblings.
            for field, child in ast.iter_fields(node):
                if isinstance(child, ast.expr):
                    replacement = visit(child)
                    if replacement is not None:
                        setattr(node, field, replacement)
                        return node
            if not isinstance(node, ast.BinOp):
                return None
            if isinstance(node.op, ast.Mult):
                if _monomial(node, names, budget) is not None:
                    return None
                left, right = _expanded(node.left, names, budget), _expanded(node.right, names, budget)
                if left is not None and right is not None:
                    return multiply(left, right)
            if isinstance(node.op, ast.Pow):
                exponent = _integer(node.right)
                base = _expanded(node.left, names, budget)
                if base is None or _monomial(node.left, names, budget) is not None:
                    return None
                if exponent < 2:
                    return ast.Constant(1) if exponent == 0 else node.left
                square = multiply(base, base)
                if square is not None:
                    if exponent == 2:
                        return square
                    remainder = node.left if exponent == 3 else ast.BinOp(node.left, ast.Pow(), ast.Constant(exponent - 2))
                    return ast.BinOp(square, ast.Mult(), remainder)
            return None

        replacement = visit(root)
        return None if replacement is None else _render(replacement, names)[0]
    except (SimplificationLimit, RecursionError):
        return None
