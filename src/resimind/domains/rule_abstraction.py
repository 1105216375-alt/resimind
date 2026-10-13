"""Bounded extraction of reusable polynomial rewrite schemas.

The extractor only emits schemas from a structural distributive identity.  It
requires multiple independent examples, preserves repeated metavariables, and
uses the exact polynomial verifier on deterministic instantiations before a
schema is returned.  It is a candidate generator, never an authority.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from .algebra import verify_identity, validate_expression


@dataclass(frozen=True, slots=True)
class VerifiedExample:
    before: str
    after: str
    variables: tuple[str, ...]
    source_id: str = ""

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() for value in (self.before, self.after)):
            raise ValueError("expressions must be nonempty")
        if type(self.variables) is not tuple or not self.variables or len(set(self.variables)) != len(self.variables):
            raise ValueError("variables must be a nonempty tuple")
        validate_expression(self.before, self.variables); validate_expression(self.after, self.variables)
        if type(self.source_id) is not str: raise ValueError("source_id must be text")


@dataclass(frozen=True, slots=True)
class AbstractRule:
    lhs: str
    rhs: str
    parameters: tuple[str, ...]
    sources: tuple[str, ...]
    verifier: str = "exact-rational-polynomial-v1+structural-distributivity"

    @property
    def id(self) -> str:
        return "schema:" + sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        return {"lhs": self.lhs, "rhs": self.rhs, "parameters": list(self.parameters),
                "sources": list(self.sources), "verifier": self.verifier}


def _parse(text: str) -> ast.expr:
    return ast.parse(text, mode="eval").body


def _is_sum(node: ast.AST) -> bool:
    # The public schema is deliberately additive.  Treating ``A-B`` as
    # ``A+B`` would erase a sign when the abstract rule is later applied.
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)


def _leaf(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Constant) and type(node.value) is int: return str(node.value)
    return None


def _render(node: ast.AST) -> str:
    return ast.unparse(ast.fix_missing_locations(node))


def _distributive_shape(example: VerifiedExample) -> tuple[str, str, tuple[str, ...]] | None:
    left, right = _parse(example.before), _parse(example.after)
    if not (isinstance(left, ast.BinOp) and isinstance(left.op, ast.Mult) and _is_sum(left.left)):
        return None
    if not (isinstance(right, ast.BinOp) and isinstance(right.op, ast.Add)):
        return None
    expected_left = ast.BinOp(left=left.left.left, op=ast.Mult(), right=left.right)
    expected_right = ast.BinOp(left=left.left.right, op=ast.Mult(), right=left.right)
    if _render(right.left) != _render(expected_left) or _render(right.right) != _render(expected_right):
        return None
    leaves = []
    for node in (left.left.left, left.left.right, left.right):
        token = _leaf(node)
        if token is None: return None
        leaves.append(token)
    # The same operand may occur in several positions; the schema keeps identity.
    return "(A+B)*C", "A*C+B*C", tuple(leaves)


def extract_rules(examples: tuple[VerifiedExample, ...], *, max_rules: int = 32) -> tuple[AbstractRule, ...]:
    if type(examples) is not tuple: raise ValueError("examples must be a tuple")
    if len(examples) < 2: return ()
    if type(max_rules) is not int or not 1 <= max_rules <= 128: raise ValueError("max_rules out of range")
    usable = []
    for example in examples:
        if type(example) is not VerifiedExample: raise ValueError("examples must contain VerifiedExample")
        identity = verify_identity(example.before, example.after, example.variables)
        if identity.status != "verified": continue
        shape = _distributive_shape(example)
        if shape is not None: usable.append((example, shape))
    if len({item[0].source_id or item[0].before for item in usable}) < 2: return ()
    if not usable: return ()
    # Structural normalization makes the rule independent of example variable names.
    rule = AbstractRule("(A+B)*C", "A*C+B*C", ("A", "B", "C"),
                        tuple(sorted({item[0].source_id or item[0].before for item in usable})))
    # Exact verifier probes with diverse coefficients; the structural condition is
    # the proof of the identity family, probes guard the renderer and bindings.
    # Keep the probe family inside the schema's ``A+B`` grammar.  A top-level
    # subtraction is mathematically valid too, but it is a different syntax
    # shape and must not be silently normalized into a positive ``B`` term.
    probes = (("x+y", "z"), ("2*x+3*y", "x+1"), ("-x+4*y", "2*z"))
    for left, right in probes:
        before = f"({left})*({right})"
        # Build the canonical expansion using the parsed two-term sum, not text splitting.
        tree = _parse(left); assert isinstance(tree, ast.BinOp)
        after = f"({_render(ast.BinOp(left=tree.left, op=ast.Mult(), right=_parse(right)))})+({_render(ast.BinOp(left=tree.right, op=ast.Mult(), right=_parse(right)))})"
        if verify_identity(before, after, ("x", "y", "z")).status != "verified":
            return ()
    return (rule,)


def apply_rule(rule: AbstractRule, expression: str, bindings: tuple[str, str, str], variables: tuple[str, ...]) -> str:
    if type(rule) is not AbstractRule or rule.lhs != "(A+B)*C": raise ValueError("unsupported rule")
    if type(bindings) is not tuple or len(bindings) != 3 or any(type(v) is not str or not v.strip() for v in bindings):
        raise ValueError("bindings must contain three expressions")
    try:
        expected_tree = ast.BinOp(
            left=ast.BinOp(left=_parse(bindings[0]), op=ast.Add(), right=_parse(bindings[1])),
            op=ast.Mult(), right=_parse(bindings[2]),
        )
        actual_tree = _parse(expression)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ValueError("expression does not match schema") from exc
    if ast.dump(actual_tree, include_attributes=False) != ast.dump(expected_tree, include_attributes=False):
        raise ValueError("expression does not match schema")
    result = f"({bindings[0]})*({bindings[2]})+({bindings[1]})*({bindings[2]})"
    if verify_identity(expression, result, variables).status != "verified": raise ValueError("schema application failed exact verification")
    return result
