"""Public development challenge with an optional independent SymPy scorer.

No models, network requests, result submission or hidden leaderboard. The
scorer constructs SymPy objects from a bounded AST; it never sympifies strings.
"""
from __future__ import annotations

import ast
import json
import math
from pathlib import Path
import time

SUITE = "catch-the-wrong-step-v1"


def tasks():
    """Twelve identity families, their mutations, and four scope controls."""
    pairs = []
    for a in (1, 2, 3, 5):
        pairs.append((f"(x+{a})**2", f"x**2+{2*a}*x+{a*a}", ["x"]))
        pairs.append((f"(x+{a}*y)**3", f"x**3+{3*a}*x**2*y+{3*a*a}*x*y**2+{a**3}*y**3", ["x", "y"]))
        pairs.append((f"(x+{a})*(x-{a})/2", f"x**2/2-{a*a}/2", ["x"]))
    rows = []
    for i, (source, expanded, variables) in enumerate(pairs):
        rows.append({"id": f"identity-{i:02d}", "variables": variables,
                     "steps": [source, f"({source})+0", expanded]})
        # Alternate an early and a late error. A later repair must not erase
        # the first invalid step from the score.
        steps = ([source, f"({expanded})+x", expanded] if i % 2 == 0 else
                 [source, expanded, f"({expanded})+x"])
        rows.append({"id": f"mutation-{i:02d}", "variables": variables, "steps": steps})
    for i, steps in enumerate((["1/x", "x"], ["sin(x)", "x"],
                              ["x**100", "x"], ["x", "1/x", "x+1"])):
        rows.append({"id": f"scope-{i:02d}", "variables": ["x"], "steps": steps})
    return rows


def baseline():
    """ResiMind exact tools baseline; this is not a model/Agent comparison."""
    from .tool_api import ToolSession
    session = ToolSession()
    answers = []
    for task in tasks():
        started = time.perf_counter()
        status, first_error, repair = "verified", None, None
        for index, (lhs, rhs) in enumerate(zip(task["steps"], task["steps"][1:]), 1):
            result = session.call("verify_identity", {"lhs": lhs, "rhs": rhs, "variables": task["variables"]})
            if result["status"] != "verified":
                status = "unresolved" if result["status"] != "rejected" else "rejected"
                if status == "rejected":
                    first_error = index
                    from .domains.compact_algebra import run_compact
                    run = run_compact(task["steps"][0], tuple(task["variables"]))
                    if run.verification.status == "verified":
                        repair = run.certificate.render()
                break
        answers.append({"id": task["id"], "status": status, "first_error": first_error,
                        "repair": repair, "model_calls": 0,
                        "elapsed_seconds": time.perf_counter() - started})
    return answers


def _polynomial(source, variables, *, require_expanded=False):
    """Independent bounded parser for this SMALL challenge, not core's verifier."""
    import sympy as sp
    if type(source) is not str or not 0 < len(source) <= 2048:
        raise ValueError("expression size")
    tree = ast.parse(source, mode="eval")
    if len(list(ast.walk(tree))) > 256:
        raise ValueError("expression nodes")
    symbols = {v: sp.Symbol(v) for v in variables}

    def read(node, depth=0):
        if depth > 32:
            raise ValueError("expression depth")
        if isinstance(node, ast.Name) and node.id in symbols:
            return symbols[node.id], 1
        if isinstance(node, ast.Constant) and type(node.value) is int and abs(node.value) <= 100000:
            return sp.Integer(node.value), 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value, degree = read(node.operand, depth+1)
            return (value if isinstance(node.op, ast.UAdd) else -value), degree
        if not isinstance(node, ast.BinOp):
            raise ValueError("unsupported grammar")
        left, dl = read(node.left, depth+1)
        if isinstance(node.op, ast.Pow):
            if not isinstance(node.right, ast.Constant) or type(node.right.value) is not int or not 0 <= node.right.value <= 6:
                raise ValueError("power bound")
            degree = dl * node.right.value
            if degree > 6 or (require_expanded and not isinstance(node.left, (ast.Name, ast.Constant))):
                raise ValueError("degree or expanded-form bound")
            return left ** node.right.value, degree
        right, dr = read(node.right, depth+1)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            return (left+right if isinstance(node.op, ast.Add) else left-right), max(dl, dr)
        if isinstance(node.op, ast.Mult):
            if dl+dr > 6:
                raise ValueError("degree bound")
            if require_expanded and any(isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)) for n in ast.walk(node)):
                raise ValueError("repair must be expanded")
            return left*right, dl+dr
        if isinstance(node.op, ast.Div):
            if not isinstance(node.right, ast.Constant) or type(node.right.value) is not int or not 0 < abs(node.right.value) <= 1000:
                raise ValueError("only literal integer denominators")
            return left/right, dl
        raise ValueError("unsupported operator")
    expression, _ = read(tree.body)
    return sp.Poly(expression, *symbols.values(), domain=sp.QQ)


def oracle(task):
    for index, (lhs, rhs) in enumerate(zip(task["steps"], task["steps"][1:]), 1):
        try:
            equal = _polynomial(lhs, task["variables"]) == _polynomial(rhs, task["variables"])
        except (ValueError, SyntaxError, RecursionError):
            return "unresolved", None
        if not equal:
            return "rejected", index
    return "verified", None


def score(answers):
    import sympy  # Fail clearly before partial scoring if the optional extra is missing.
    if type(answers) is not list or len(answers) > len(tasks()):
        raise ValueError("answers must be a list with at most 28 rows")
    task_ids = {t["id"] for t in tasks()}
    indexed = {}
    for row in answers:
        if type(row) is not dict or set(row) - {"id", "status", "first_error", "repair", "model_calls", "elapsed_seconds"}:
            raise ValueError("invalid answer fields")
        if not {"id", "status", "first_error", "repair"} <= set(row):
            raise ValueError("missing answer fields")
        ident = row["id"]
        if type(ident) is not str or ident not in task_ids or ident in indexed:
            raise ValueError("unknown or duplicate task id")
        if type(row["status"]) is not str or row["status"] not in {"verified", "rejected", "unresolved"}:
            raise ValueError("invalid status")
        if row["first_error"] is not None and (type(row["first_error"]) is not int or row["first_error"] not in (1, 2)):
            raise ValueError("invalid first_error")
        if row["repair"] is not None and (type(row["repair"]) is not str or len(row["repair"]) > 2048):
            raise ValueError("invalid repair")
        if row["status"] != "rejected" and (row["first_error"] is not None or row["repair"] is not None):
            raise ValueError("non-rejected answers require null first_error and repair")
        if row.get("model_calls") is not None and (type(row["model_calls"]) is not int or row["model_calls"] < 0):
            raise ValueError("model_calls must be a nonnegative integer or null")
        elapsed = row.get("elapsed_seconds")
        if elapsed is not None and (type(elapsed) not in (int, float) or not 0 <= elapsed <= 1e12 or not math.isfinite(elapsed)):
            raise ValueError("elapsed_seconds must be finite and nonnegative or null")
        indexed[ident] = row
    correct = false_accepts = located = repaired = 0
    details = []
    for task in tasks():
        expected, first = oracle(task)
        answer = indexed.get(task["id"])
        matches = answer is not None and answer["status"] == expected
        correct += matches
        false_accepts += bool(answer and answer["status"] == "verified" and expected != "verified")
        localized = expected == "rejected" and matches and answer["first_error"] == first
        located += localized
        fixed = False
        if localized and answer["repair"] is not None:
            try:
                fixed = (_polynomial(answer["repair"], task["variables"], require_expanded=True)
                         == _polynomial(task["steps"][0], task["variables"]))
            except (ValueError, SyntaxError, RecursionError):
                pass
        repaired += fixed
        details.append({"id": task["id"], "expected_status": expected, "expected_first_error": first,
                        "status_correct": bool(matches), "localized": bool(localized), "repair_correct": bool(fixed)})
    return {"suite": SUITE, "scope": "public development challenge; no external Agent ranking",
            "oracle": "independent bounded AST to SymPy Poly over QQ", "sympy_version": sympy.__version__,
            "submitted": len(indexed), "total": 28, "status_correct": correct,
            "status_accuracy": correct/28, "false_acceptances": false_accepts,
            "false_acceptance_denominator": 16, "false_acceptance_rate": false_accepts/16,
            "error_localization_correct": located, "error_localization_total": 12,
            "repairs_correct": repaired, "repairs_total": 12,
            "cost_note": "external cost fields are self-reported and are not scored or independently measured",
            "details": details}


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate JSON field")
        result[k] = v
    return result


def main(args):
    import sys
    try:
        if args.challenge_command == "export":
            result = {"suite": SUITE, "tasks": tasks()}
        elif args.challenge_command == "baseline":
            result = baseline()
        else:
            with Path(args.answers).open(encoding="utf-8") as f:
                source = f.read(1024 * 1024 + 1)
            if len(source) > 1024 * 1024:
                raise ValueError("answer file too large")
            result = score(json.loads(source, object_pairs_hook=_unique))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except ImportError:
        print("Independent scoring requires the optional extra: pip install '.[challenge]'", file=sys.stderr)
    except (ValueError, OSError, RecursionError) as exc:
        print(f"Challenge input error: {exc}", file=sys.stderr)
    return 2
