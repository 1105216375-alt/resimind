"""Heuristics for progress toward expansion, never proofs of an identity.

An exact identity check must precede this assessment. Completion and recognized
local expansion are useful progress signals. Clearly equivalent sign notation
is cosmetic; other changes can require a bounded exploratory detour. In
particular, rearranging factors can expose a rule and is not automatically
rejected, and a valid distributive step can increase both size and goal count.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import asdict, dataclass
import time

from .algebra import _tree
from .polynomial_learning import (
    WorkCounts, _needs_expansion, _primitive, _text,
    _unresolved_subexpressions, is_expanded,
)
from .polynomial_scheduling import expression_cost

MAX_LOCAL_PROGRESS_PROBES = 16


def _signed_shape(node: ast.AST) -> tuple:
    """Preserve operand order and grouping while removing sign notation only.

    No sorting, reassociation, collection of terms, constant arithmetic, or
    distribution occurs. Thus this does not label every equivalent polynomial
    cosmetic, and does not hide a possible rearrangement-based search step.
    """
    if isinstance(node, ast.Constant):
        value = node.value
        if type(value) is not int:
            raise ValueError("unsupported progress constant")
        return (-1 if value < 0 else 1), ("integer", abs(value))
    if isinstance(node, ast.Name):
        return 1, ("name", node.id)
    if isinstance(node, ast.UnaryOp):
        sign, shape = _signed_shape(node.operand)
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise ValueError("unsupported progress sign")
        return (-sign if isinstance(node.op, ast.USub) else sign), shape
    if not isinstance(node, ast.BinOp):
        raise ValueError("unsupported progress syntax")
    left_sign, left = _signed_shape(node.left)
    right_sign, right = _signed_shape(node.right)
    if isinstance(node.op, (ast.Add, ast.Sub)):
        if isinstance(node.op, ast.Sub):
            right_sign = -right_sign
        # Canonicalize an overall sign without changing term order or grouping.
        return left_sign, ("sum", (1, left), (left_sign * right_sign, right))
    if isinstance(node.op, (ast.Mult, ast.Div)):
        return left_sign * right_sign, ("product" if isinstance(node.op, ast.Mult) else "division", left, right)
    if isinstance(node.op, ast.Pow):
        if right[0] != "integer" or right_sign < 0:
            raise ValueError("unsupported progress exponent")
        return left_sign ** right[1], ("power", left, right)
    raise ValueError("unsupported progress operator")


def _same_shape(left: ast.AST, right: ast.AST) -> bool:
    return _signed_shape(left) == _signed_shape(right)


def _route_shape(signed: tuple) -> tuple:
    """Recognize regrouping/reordering as exploration, not cosmetic rejection."""
    sign, shape = signed
    if shape[0] == "sum":
        terms = []

        def collect_sum(item_sign, item):
            if item[0] == "sum":
                for child_sign, child in item[1:]:
                    collect_sum(item_sign * child_sign, child)
            else:
                terms.append(_route_shape((item_sign, item)))

        collect_sum(sign, shape)
        return "sum", tuple(sorted(terms, key=repr))
    if shape[0] == "product":
        factors = []

        def collect_product(item):
            if item[0] == "product":
                collect_product(item[1])
                collect_product(item[2])
            else:
                factors.append(_route_shape((1, item)))

        collect_product(shape)
        return "signed_product", sign, tuple(sorted(factors, key=repr))
    if shape[0] in ("division", "power"):
        return sign, shape[0], _route_shape((1, shape[1])), _route_shape((1, shape[2]))
    return sign, shape


def _local_expansion(before: ast.expr, after: ast.expr) -> tuple[bool, int]:
    """Recognize a bounded primitive expansion anywhere in the expression.

    Generate at most 16 local candidates, using the existing AST rewrite
    grammar. Comparing signatures is a progress heuristic, not verification.
    """
    paths: list[tuple[tuple[str, ...], ast.expr]] = []

    def visit(node, path):
        if _needs_expansion(node):
            paths.append((path, node))
        for name in ("left", "right", "operand"):
            child = getattr(node, name, None)
            if isinstance(child, ast.expr):
                visit(child, path + (name,))

    visit(before, ())
    # The encompassing expression plus deepest subexpressions cover ordinary
    # primitive and selected-local proposals without an unbounded search.
    paths.sort(key=lambda item: -len(item[0]))
    candidates = [((), before)] + [item for item in paths if item[0]]
    probes = 0
    for path, node in candidates[:MAX_LOCAL_PROGRESS_PROBES]:
        try:
            rewritten = _primitive(_text(node), WorkCounts())
            if rewritten is None:
                continue
            replacements = [_tree(rewritten)]
            # When both operands are sums, the primitive prefers the left.
            # A model may legitimately distribute the right instead.
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)
                    and isinstance(node.left, ast.BinOp) and isinstance(node.left.op, (ast.Add, ast.Sub))
                    and isinstance(node.right, ast.BinOp) and isinstance(node.right.op, (ast.Add, ast.Sub))):
                replacements.append(ast.BinOp(
                    ast.BinOp(deepcopy(node.left), ast.Mult(), deepcopy(node.right.left)),
                    deepcopy(node.right.op),
                    ast.BinOp(deepcopy(node.left), ast.Mult(), deepcopy(node.right.right))))
            for replacement in replacements:
                if probes >= MAX_LOCAL_PROGRESS_PROBES:
                    return False, probes
                probes += 1
                proposed = deepcopy(before)
                if not path:
                    proposed = replacement
                else:
                    parent = proposed
                    for name in path[:-1]:
                        parent = getattr(parent, name)
                    setattr(parent, path[-1], replacement)
                # A local rewrite may duplicate a large sibling. Keep the
                # whole-expression bounds before comparing syntactic shapes.
                proposed = _tree(_text(proposed))
                if (_same_shape(proposed, after)
                        or _route_shape(_signed_shape(proposed)) == _route_shape(_signed_shape(after))):
                    return True, probes
        except (ValueError, RecursionError):
            continue
    return False, probes


@dataclass(frozen=True, slots=True)
class ProgressAssessment:
    classification: str
    reason: str
    before: dict
    after: dict
    unexpanded_subexpressions: dict
    local_probes: int = 0
    elapsed_seconds: float = 0.0
    heuristic_only: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def assess_polynomial_progress(before: str, after: str, variables: tuple[str, ...]) -> ProgressAssessment:
    """Assess a previously verified equality against the expansion objective.

    ``variables`` documents the caller's domain binding; it does not authorize
    this equality, and this helper deliberately does not invoke a verifier or
    compute a polynomial answer. The caller controls any exploratory budget.
    """
    started = time.perf_counter()
    left, right = _tree(before), _tree(after)
    before_cost, after_cost = expression_cost(before), expression_cost(after)
    remaining = _unresolved_subexpressions(after)
    probes = 0
    if is_expanded(after):
        classification, reason = "complete", "expansion_goal_reached"
    elif _same_shape(left, right):
        classification, reason = "cosmetic", "sign_or_surface_notation_only"
    else:
        recognized, probes = _local_expansion(left, right)
        if recognized:
            classification, reason = "structural", "recognized_local_expansion"
        elif _route_shape(_signed_shape(left)) == _route_shape(_signed_shape(right)):
            classification, reason = "uncertain", "rearrangement_may_expose_an_alternative_route"
        elif (after_cost["remaining_actions"] < before_cost["remaining_actions"]
              or after_cost["remaining_work"] < before_cost["remaining_work"]):
            classification, reason = "structural", "reduced_unexpanded_work"
        else:
            classification, reason = "uncertain", "equivalent_step_with_uncertain_goal_progress"
    return ProgressAssessment(classification, reason, before_cost, after_cost, remaining,
                              probes, time.perf_counter() - started)
