"""Bounded syntax estimates for choosing proposals, never mathematical evidence.

The scores describe the work left by a candidate's syntax. They neither call
the polynomial coefficient oracle nor predict actual runtime, money saved, or
correctness. Every selected candidate still requires the normal verifier.
"""
from __future__ import annotations

import ast

from .algebra import MAX_PRODUCTS, MAX_TERMS, _tree


def expression_cost(expression: str) -> dict[str, int | bool]:
    """Estimate unfinished local work using only a bounded expression AST.

    Counts saturate; a large factored expression cannot cause expansion here.
    Grammar correctness remains the domain verifier's responsibility. In
    particular, this metric does not reject expressions merely because a
    syntactic degree bound ignores terms that cancel or multiply by zero.
    """
    root = _tree(expression)

    def inspect(node):
        if isinstance(node, (ast.Constant, ast.Name)):
            return (0 if isinstance(node, ast.Constant) and node.value == 0 else 1), False, 0, 0
        if isinstance(node, ast.UnaryOp):
            return inspect(node.operand)
        if not isinstance(node, ast.BinOp):
            raise ValueError("unsupported scheduling syntax")
        left_terms, left_sum, left_actions, left_work = inspect(node.left)
        if isinstance(node.op, ast.Pow):
            exponent = node.right
            sign = 1
            if isinstance(exponent, ast.UnaryOp):
                sign = -1 if isinstance(exponent.op, ast.USub) else 1
                exponent = exponent.operand
            if not isinstance(exponent, ast.Constant) or type(exponent.value) is not int:
                raise ValueError("unsupported scheduling exponent")
            power = sign * exponent.value
            if not 0 <= power <= 16:
                raise ValueError("unsupported scheduling exponent")
            terms = 1
            for _ in range(power):
                terms = min(MAX_TERMS, terms * left_terms)
            actions = left_actions + (max(1, power - 1) if left_sum else 0)
            work = left_work + (max(1, terms) if left_sum else 0)
            return terms, left_sum, min(MAX_PRODUCTS, actions), min(MAX_PRODUCTS, work)
        if isinstance(node.op, ast.Div):
            return left_terms, left_sum, left_actions, left_work
        right_terms, right_sum, right_actions, right_work = inspect(node.right)
        actions, work = left_actions + right_actions, left_work + right_work
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return min(MAX_TERMS, left_terms + right_terms), True, min(MAX_PRODUCTS, actions), min(MAX_PRODUCTS, work)
        if isinstance(node.op, ast.Mult):
            terms = min(MAX_TERMS, left_terms * right_terms)
            if left_sum or right_sum:
                actions += 1
                work += max(1, terms)
            return terms, left_sum or right_sum, min(MAX_PRODUCTS, actions), min(MAX_PRODUCTS, work)
        raise ValueError("unsupported scheduling operator")

    _, _, actions, work = inspect(root)
    return {"ast_nodes": sum(1 for _ in ast.walk(root)), "source_length": len(expression),
            "remaining_work": work, "remaining_actions": actions, "expanded": actions == 0}


def estimated_local_work(after: dict) -> int:
    """A dimensionless bounded planning score, not measured execution work."""
    return max(1, after["remaining_work"] + (after["ast_nodes"] + 15) // 16)


def candidate_rank(before: dict, after: dict, strategy: str) -> tuple[int, ...]:
    """Prefer completion, then remaining work and substantial syntax growth.

    Complete candidates use 128-node size bands: within a similarly bounded
    one-step finish, a recalled rule wins the tie. This preserves useful rule
    reuse without letting a very large rule expansion beat a compact finish.
    Incomplete candidates prioritize remaining independent rewrite actions,
    then a term-pair work bound with a small penalty for growth. Reducing a
    crude pair estimate at the price of many more proof checks is not cheap.
    Merely shrinking the AST is insufficient.
    """
    tie = {"verified_rule": 0, "simplify": 1, "distribute": 2, "primitive": 3}.get(strategy, 4)
    if after["expanded"]:
        return (0, (after["ast_nodes"] + 127) // 128, tie,
                after["ast_nodes"], after["source_length"])
    growth = max(0, after["ast_nodes"] - before["ast_nodes"])
    return (1, after["remaining_actions"], after["remaining_work"] + growth // 8,
            after["ast_nodes"], tie, after["source_length"])


def cheap_progress(before: dict, after: dict) -> bool:
    """Recognize a useful finish or lower estimated remaining work/size."""
    return (after["expanded"] or after["remaining_work"] < before["remaining_work"]
            or after["remaining_actions"] < before["remaining_actions"]
            or after["ast_nodes"] < before["ast_nodes"])
