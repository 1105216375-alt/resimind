"""Small exact algebra certificates for reusable knowledge.

This verifier proves polynomial identities over the rationals (also valid over
the reals), not arbitrary mathematical statements. Expressions are parsed as a
bounded Python expression AST and evaluated in a sparse ``Fraction`` polynomial
ring. No Python evaluation, numerical sampling, SymPy, or model is involved.

The grammar is integer literals, declared ASCII variables, parentheses, unary
``+``/``-``, ``+``, ``-``, ``*``, nonnegative integer powers, and division by a
nonzero integer literal. Unsupported input and exhausted resource limits return
``unknown`` and cannot authorize admission to a knowledge library.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
import keyword
import re

from ..knowledge import KnowledgeCandidate, Verification


VERIFIER_ID = "exact-rational-polynomial-v1"
MAX_SOURCE_LENGTH = 8192
MAX_VARIABLES = 16
MAX_AST_NODES = 1024
MAX_AST_DEPTH = 64
MAX_EXPONENT = 16
MAX_DEGREE = 32
MAX_TERMS = 4096
MAX_INTEGER_BITS = 128
MAX_COEFFICIENT_BITS = 512
MAX_PRODUCTS = 100_000
MAX_DERIVATION_STEPS = 64

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_Monomial = tuple[int, ...]
_Polynomial = dict[_Monomial, Fraction]


class PolynomialError(ValueError):
    """Expression is outside the supported, bounded polynomial language."""


def _variables(variables: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(variables, (tuple, list)) or len(variables) > MAX_VARIABLES:
        raise PolynomialError(f"variables must be a list or tuple of at most {MAX_VARIABLES} names")
    if any(type(name) is not str or not _NAME.fullmatch(name) or keyword.iskeyword(name)
           for name in variables):
        raise PolynomialError("variables must be ASCII identifiers beginning with a letter")
    if len(set(variables)) != len(variables):
        raise PolynomialError("variables must be distinct")
    return tuple(variables)


def _tree(expression: str) -> ast.expr:
    if type(expression) is not str or not expression.strip():
        raise PolynomialError("expression must be a nonempty string")
    if len(expression) > MAX_SOURCE_LENGTH:
        raise PolynomialError("expression exceeds source length limit")
    try:
        tree = ast.parse(expression.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise PolynomialError("expression is not valid supported syntax") from exc
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise PolynomialError("expression exceeds AST size or depth limit")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree


def _literal_integer(node: ast.AST) -> int:
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if not isinstance(node, ast.Constant) or type(node.value) is not int:
        raise PolynomialError("a bounded integer literal is required")
    if node.value.bit_length() > MAX_INTEGER_BITS:
        raise PolynomialError("integer literal exceeds magnitude limit")
    return sign * node.value


@dataclass
class _Ring:
    variables: tuple[str, ...]
    products: int = 0

    @property
    def zero_power(self) -> _Monomial:
        return (0,) * len(self.variables)

    def constant(self, value: int | Fraction) -> _Polynomial:
        return {} if not value else {self.zero_power: Fraction(value)}

    def checked(self, polynomial: _Polynomial) -> _Polynomial:
        if len(polynomial) > MAX_TERMS:
            raise PolynomialError("polynomial exceeds term limit")
        for power, coefficient in polynomial.items():
            if sum(power) > MAX_DEGREE:
                raise PolynomialError("polynomial exceeds total degree limit")
            if (abs(coefficient.numerator).bit_length() > MAX_COEFFICIENT_BITS
                    or coefficient.denominator.bit_length() > MAX_COEFFICIENT_BITS):
                raise PolynomialError("polynomial coefficient exceeds magnitude limit")
        return polynomial

    def add(self, left: _Polynomial, right: _Polynomial, sign: int = 1) -> _Polynomial:
        result = left.copy()
        for power, coefficient in right.items():
            value = result.get(power, Fraction(0)) + sign * coefficient
            if value:
                result[power] = value
            else:
                result.pop(power, None)
        return self.checked(result)

    def multiply(self, left: _Polynomial, right: _Polynomial) -> _Polynomial:
        self.products += len(left) * len(right)
        if self.products > MAX_PRODUCTS:
            raise PolynomialError("polynomial expansion exceeds product budget")
        result: _Polynomial = {}
        for lpower, lcoefficient in left.items():
            for rpower, rcoefficient in right.items():
                power = tuple(a + b for a, b in zip(lpower, rpower))
                if sum(power) > MAX_DEGREE:
                    raise PolynomialError("polynomial exceeds total degree limit")
                value = result.get(power, Fraction(0)) + lcoefficient * rcoefficient
                if value:
                    result[power] = value
                else:
                    result.pop(power, None)
                if len(result) > MAX_TERMS:
                    raise PolynomialError("polynomial exceeds term limit")
        return self.checked(result)

    def read(self, node: ast.expr) -> _Polynomial:
        if isinstance(node, ast.Constant):
            return self.constant(_literal_integer(node))
        if isinstance(node, ast.Name):
            if node.id not in self.variables:
                raise PolynomialError(f"undeclared variable: {node.id}")
            power = list(self.zero_power)
            power[self.variables.index(node.id)] = 1
            return {tuple(power): Fraction(1)}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self.read(node.operand)
            return value if isinstance(node.op, ast.UAdd) else {
                power: -coefficient for power, coefficient in value.items()
            }
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, ast.Div):
                denominator = _literal_integer(node.right)
                if not denominator:
                    raise PolynomialError("division by zero is not a polynomial expression")
                return self.checked({power: coefficient / denominator
                                     for power, coefficient in self.read(node.left).items()})
            if isinstance(node.op, ast.Pow):
                exponent = _literal_integer(node.right)
                if not 0 <= exponent <= MAX_EXPONENT:
                    raise PolynomialError(f"exponent must lie between 0 and {MAX_EXPONENT}")
                # Validate the base even when exponent is zero: forbidden
                # syntax and undeclared symbols never vanish behind x**0.
                base = self.read(node.left)
                result = self.constant(1)
                for _ in range(exponent):
                    result = self.multiply(result, base)
                return result
            if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
                left, right = self.read(node.left), self.read(node.right)
                if isinstance(node.op, ast.Mult):
                    return self.multiply(left, right)
                return self.add(left, right, -1 if isinstance(node.op, ast.Sub) else 1)
        raise PolynomialError(f"unsupported expression node: {type(node).__name__}")


def _result(status: str, reason: str) -> Verification:
    return Verification(status=status, reason=reason, verifier_id=VERIFIER_ID)


def _coefficient_mismatch(left: _Polynomial, right: _Polynomial,
                          variables: tuple[str, ...]) -> str:
    """Explain one exact mismatch, not the complete expansion of either side.

    Ordering uses exponent tuples in the declared variable order. Existing
    variable, degree and coefficient limits bound the diagnostic's size.
    """
    power = next(power for power in sorted(left.keys() | right.keys())
                 if left.get(power, 0) != right.get(power, 0))
    monomial = "*".join(name if exponent == 1 else f"{name}**{exponent}"
                        for name, exponent in zip(variables, power) if exponent) or "1"
    return (f"exact polynomial coefficients differ: monomial={monomial}; "
            f"lhs coefficient={left.get(power, Fraction(0))}; "
            f"rhs coefficient={right.get(power, Fraction(0))}")


def verify_identity(lhs: str, rhs: str, variables: Sequence[str]) -> Verification:
    """Prove an identity by comparing all exact polynomial coefficients.

    A ``verified`` result covers every rational/real assignment of the declared
    variables. ``rejected`` means two supported polynomials differ; ``unknown``
    means the grammar or resource bounds prevent this verifier deciding.
    A rejected result identifies one exact coefficient mismatch for correction;
    it does not return the complete expanded answer.
    """
    try:
        ring = _Ring(_variables(variables))
        left, right = ring.read(_tree(lhs)), ring.read(_tree(rhs))
    except PolynomialError as exc:
        return _result("unknown", str(exc))
    if left == right:
        return _result("verified", "all exact rational polynomial coefficients agree")
    return _result("rejected", _coefficient_mismatch(left, right, ring.variables))


def validate_expression(expression: str, variables: Sequence[str]) -> None:
    """Validate bounded grammar and expansion; raise ``PolynomialError`` on failure."""
    _Ring(_variables(variables)).read(_tree(expression))


def _same_expression(left: str, right: str) -> bool:
    return ast.dump(_tree(left), include_attributes=False) == ast.dump(
        _tree(right), include_attributes=False
    )


def nonzero_assumption(factor: str) -> str:
    """Canonical assumption spelling for an equation-cancellation certificate."""
    return f"{ast.unparse(_tree(factor))} != 0"


class AlgebraVerifier:
    """Verify a bounded certificate before a library can admit an algebra rule.

    ``polynomial_identity`` statements contain ``lhs``, ``rhs``, and
    ``variables``. Each derivation item contains ``lhs`` and ``rhs``. The chain
    must start/end at the statement, join structurally, and pass exact checking
    at every step; a successful sample table is never a certificate.

    ``equation_cancellation`` statements contain ``factor``, ``lhs``, ``rhs``,
    and ``variables``. They assert equivalence of ``factor*lhs = factor*rhs``
    and ``lhs = rhs`` under the explicit canonical ``factor != 0`` assumption.
    Their one-step certificate is ``{"operation": "cancel_nonzero_factor",
    "factor": ..., "lhs": ..., "rhs": ...}``. It proves the guarded
    transformation, not that either equation is true or a task is solved.
    """

    verifier_id = VERIFIER_ID

    def verify(self, candidate: KnowledgeCandidate) -> Verification:
        if candidate.domain != "algebra":
            return _result("unknown", "verifier supports the algebra domain only")
        if candidate.kind not in {"polynomial_identity", "equation_cancellation"}:
            return _result("unknown", "unsupported algebra knowledge kind")
        if not isinstance(candidate.statement, Mapping):
            return _result("rejected", "statement must be a mapping")
        fields = {"lhs", "rhs", "variables"}
        if candidate.kind == "equation_cancellation":
            fields.add("factor")
        if set(candidate.statement) != fields:
            # An ignored field could change the claimed number system (e.g.
            # integers modulo 6, where nonzero factors cannot always cancel).
            return _result("unknown", "unsupported or missing algebra statement fields")
        try:
            variables = _variables(candidate.statement.get("variables"))
            if candidate.kind == "equation_cancellation":
                return self._cancellation(candidate, variables)
            return self._identity(candidate, variables)
        except PolynomialError as exc:
            return _result("unknown", str(exc))

    def _identity(self, candidate: KnowledgeCandidate, variables: tuple[str, ...]) -> Verification:
        lhs, rhs = candidate.statement.get("lhs"), candidate.statement.get("rhs")
        # Validate both endpoints even if certificate metadata is malformed.
        endpoint = verify_identity(lhs, rhs, variables)
        if endpoint.status != "verified":
            return endpoint
        if (not isinstance(candidate.derivation, (list, tuple))
                or not 1 <= len(candidate.derivation) <= MAX_DERIVATION_STEPS):
            return _result("rejected", "a nonempty bounded derivation certificate is required")
        previous = lhs
        for index, step in enumerate(candidate.derivation):
            if not isinstance(step, Mapping) or "lhs" not in step or "rhs" not in step:
                return _result("rejected", f"derivation step {index} must contain lhs and rhs")
            if not _same_expression(previous, step["lhs"]):
                return _result("rejected", f"derivation step {index} does not continue the chain")
            verdict = verify_identity(step["lhs"], step["rhs"], variables)
            if verdict.status != "verified":
                return _result(verdict.status, f"derivation step {index}: {verdict.reason}")
            previous = step["rhs"]
        if not _same_expression(previous, rhs):
            return _result("rejected", "derivation does not end at the claimed right-hand side")
        return _result("verified", "complete derivation and identity checked with exact coefficients")

    def _cancellation(self, candidate: KnowledgeCandidate, variables: tuple[str, ...]) -> Verification:
        statement = candidate.statement
        factor, lhs, rhs = (statement.get(name) for name in ("factor", "lhs", "rhs"))
        ring = _Ring(variables)
        factor_poly = ring.read(_tree(factor))
        ring.read(_tree(lhs))
        ring.read(_tree(rhs))
        if not factor_poly:
            return _result("rejected", "zero polynomial cannot be a cancellable factor")
        required = nonzero_assumption(factor)
        if required not in candidate.assumptions:
            return _result("rejected", f"missing explicit assumption: {required}")
        if not isinstance(candidate.derivation, (tuple, list)) or len(candidate.derivation) != 1:
            return _result("rejected", "one explicit cancellation certificate is required")
        step = candidate.derivation[0]
        if not isinstance(step, Mapping) or step.get("operation") != "cancel_nonzero_factor":
            return _result("rejected", "certificate must explicitly cancel a nonzero factor")
        if any(not _same_expression(step.get(name), statement[name])
               for name in ("factor", "lhs", "rhs")):
            return _result("rejected", "cancellation certificate does not match the statement")
        return _result("verified", "equation cancellation proved under the recorded nonzero assumption")
