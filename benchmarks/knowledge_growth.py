"""Deterministic development study of verified, cross-task knowledge reuse.

Run from the repository root: ``python -m benchmarks.knowledge_growth``.
This is not a model-performance benchmark. The independent scoring oracle uses
exact values on a full degree-bounded grid, never the production polynomial ring.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
from itertools import product
import json
from pathlib import Path
import tempfile

from resimind.agent import Task
from resimind.core import Decision
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import PolynomialProblem, WorkCounts, build_learning_agent
from resimind.knowledge import KnowledgeCandidate, KnowledgeLibrary


MAX_GRID_POINTS = 65_536
MAX_SCORER_NODES = 4096
MAX_SCORER_DEPTH = 64
MAX_SCORER_DEGREE = 16
MAX_SCORER_WORK = 2_000_000
MAX_VALUE_BITS = 4096


class OracleUnsupported(ValueError):
    """The independent oracle cannot certify this input within its limits."""


@dataclass(frozen=True)
class Case:
    id: str
    expression: str
    variables: tuple[str, ...]
    group: str


DISCOVERY = (
    Case("discover-square", "(u+v)**2", ("u", "v"), "discovery"),
    Case("discover-cube", "(u+v)**3", ("u", "v"), "discovery"),
)

# Published development cases, fixed before this script's first result. These
# are disjoint source expressions, not an unseen or randomized model test set.
TRANSFER = (
    Case("square-coefficients", "(2*x+3*y)**2", ("x", "y"), "matching"),
    Case("square-negative", "(-2*x+3*y)**2", ("x", "y"), "matching"),
    Case("square-zero", "(0*x+3*y)**2", ("x", "y"), "matching"),
    Case("square-repeated", "(x+2*x)**2", ("x",), "matching"),
    Case("square-nested", "((x+y)+(z+w))**2", ("x", "y", "z", "w"), "matching"),
    Case("cube-coefficients", "(2*x+3*y)**3", ("x", "y"), "matching"),
    Case("cube-negative", "(-x+2*y)**3", ("x", "y"), "matching"),
    Case("cube-repeated", "(x+2*x)**3", ("x",), "matching"),
    Case("unmatched-product", "(2*x+3*y)*(x-4*y)", ("x", "y"), "unmatched-control"),
    Case("already-expanded", "3*x*x-2*x*y+5*y*y", ("x", "y"), "expanded-control"),
)


def _integer(node: ast.AST) -> int:
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if not isinstance(node, ast.Constant) or type(node.value) is not int or node.value.bit_length() > 128:
        raise OracleUnsupported("expected a bounded integer literal")
    return sign * node.value


def _parse(expression: str, variables: tuple[str, ...]) -> tuple[ast.expr, tuple[int, ...]]:
    if (type(expression) is not str or not expression.strip() or len(expression) > 32_768
            or type(variables) is not tuple or len(variables) > 8
            or len(set(variables)) != len(variables)
            or any(type(name) is not str or not name.isidentifier() for name in variables)):
        raise OracleUnsupported("unsupported expression or variable declarations")
    try:
        tree = ast.parse(expression.strip(), mode="eval").body
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise OracleUnsupported("invalid expression syntax") from exc
    count = 0
    zero = (0,) * len(variables)

    def degree(node: ast.AST, depth: int = 0) -> tuple[int, ...]:
        nonlocal count
        count += 1
        if count > MAX_SCORER_NODES or depth > MAX_SCORER_DEPTH:
            raise OracleUnsupported("expression exceeds oracle AST limits")
        if isinstance(node, ast.Constant):
            _integer(node)
            return zero
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise OracleUnsupported("undeclared variable")
            return tuple(int(name == node.id) for name in variables)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return degree(node.operand, depth + 1)
        if not isinstance(node, ast.BinOp):
            raise OracleUnsupported("unsupported oracle syntax")
        left = degree(node.left, depth + 1)
        if isinstance(node.op, ast.Pow):
            exponent = _integer(node.right)
            if not 0 <= exponent <= MAX_SCORER_DEGREE:
                raise OracleUnsupported("unsupported exponent")
            result = tuple(value * exponent for value in left)
        elif isinstance(node.op, ast.Div):
            if _integer(node.right) == 0:
                raise OracleUnsupported("zero denominator")
            result = left
        else:
            right = degree(node.right, depth + 1)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                result = tuple(max(a, b) for a, b in zip(left, right))
            elif isinstance(node.op, ast.Mult):
                result = tuple(a + b for a, b in zip(left, right))
            else:
                raise OracleUnsupported("unsupported operator")
        if any(value > MAX_SCORER_DEGREE for value in result):
            raise OracleUnsupported("expression exceeds oracle degree limit")
        return result

    return tree, degree(tree)


def _value(node: ast.AST, point: dict[str, int]) -> Fraction:
    def checked(value):
        if value.numerator.bit_length() > MAX_VALUE_BITS or value.denominator.bit_length() > MAX_VALUE_BITS:
            raise OracleUnsupported("exact value exceeds oracle magnitude limit")
        return value

    if isinstance(node, ast.Constant):
        return Fraction(node.value)
    if isinstance(node, ast.Name):
        return Fraction(point[node.id])
    if isinstance(node, ast.UnaryOp):
        value = _value(node.operand, point)
        return -value if isinstance(node.op, ast.USub) else value
    left = _value(node.left, point)
    if isinstance(node.op, ast.Pow):
        return checked(left ** _integer(node.right))
    if isinstance(node.op, ast.Div):
        return checked(left / _integer(node.right))
    right = _value(node.right, point)
    if isinstance(node.op, ast.Add):
        return checked(left + right)
    if isinstance(node.op, ast.Sub):
        return checked(left - right)
    return checked(left * right)


def _expanded(tree: ast.AST) -> bool:
    """Independent structural goal check; combining like terms is not required."""
    def contains_sum(node):
        return any(isinstance(child, ast.BinOp) and isinstance(child.op, (ast.Add, ast.Sub))
                   for child in ast.walk(node))
    return not any(
        isinstance(node, ast.BinOp)
        and ((isinstance(node.op, ast.Mult) and (contains_sum(node.left) or contains_sum(node.right)))
             or (isinstance(node.op, ast.Pow) and contains_sum(node.left)))
        for node in ast.walk(tree)
    )


def score_expansion(lhs: str, rhs: str, variables: tuple[str, ...],
                    *, max_points: int = MAX_GRID_POINTS) -> dict:
    """Certify polynomial identity by an exact, degree-complete Cartesian grid.

    A polynomial of degree at most d_i in variable i that vanishes on a product
    of d_i+1 distinct rational values is identically zero. Degrees are computed
    independently from both input ASTs, without production polynomial code.
    Capped/unsupported inputs return unknown rather than passing by sampling.
    """
    if type(max_points) is not int or max_points < 1:
        raise ValueError("max_points must be a positive integer")
    try:
        left, ldegree = _parse(lhs, variables)
        right, rdegree = _parse(rhs, variables)
        degrees = tuple(max(a, b) for a, b in zip(ldegree, rdegree))
        required = 1
        for value in degrees:
            required *= value + 1
        if required > max_points:
            raise OracleUnsupported("complete grid exceeds point budget")
        nodes = sum(1 for _ in ast.walk(left)) + sum(1 for _ in ast.walk(right))
        if nodes * required > MAX_SCORER_WORK:
            raise OracleUnsupported("complete grid exceeds oracle work budget")
        checked = 0
        for values in product(*(range(-degree // 2, -degree // 2 + degree + 1) for degree in degrees)):
            point = dict(zip(variables, values))
            checked += 1
            if _value(left, point) != _value(right, point):
                return {"status": "invalid", "reason": "exact_counterexample", "points_checked": checked,
                        "required_points": required, "degree_bounds": list(degrees), "counterexample": point}
        expanded = _expanded(right)
        return {"status": "valid" if expanded else "incomplete", "reason": "exact_grid_identity",
                "points_checked": checked, "required_points": required, "degree_bounds": list(degrees),
                "expanded": expanded}
    except (OracleUnsupported, RecursionError) as exc:
        return {"status": "unknown", "reason": str(exc), "points_checked": 0}


def _snapshot(library: KnowledgeLibrary) -> str:
    # Serialize the entire store so even adding a rejected/unknown record would
    # violate the transfer freeze. Lookup alone exposes only verified records.
    with tempfile.TemporaryDirectory(prefix="resimind-knowledge-snapshot-") as folder:
        path = Path(folder) / "snapshot.json"
        library.save(path)
        return path.read_text(encoding="utf-8")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _run(case: Case, library: KnowledgeLibrary, *, learn: bool, max_steps: int) -> dict:
    counts = WorkCounts()
    learner = build_learning_agent(PolynomialProblem(case.expression, case.variables), library,
                                   max_steps=max_steps, counts=counts)
    outcome = learner.run(Task(case.id, "Expand the supplied polynomial", "algebra"), learn=learn)
    run = outcome.result.run_result
    output = run.state.facts[-1].value[1] if run.state.facts else None
    oracle = (score_expansion(case.expression, output, case.variables) if output is not None
              else {"status": "missing", "reason": "no_committed_expression", "points_checked": 0})
    uses = []
    for fact in run.state.facts:
        _, _, rule_id, fingerprint = fact.value
        if rule_id:
            record = library.get(rule_id)
            uses.append({"rule_id": rule_id, "fingerprint": fingerprint,
                         "source_task_id": record.candidate.source_task_id,
                         "cross_task": record.candidate.source_task_id != case.id})
    return {"task_id": case.id, "expression": case.expression, "variables": list(case.variables),
            "group": case.group, "status": run.status, "stop_reason": run.stop_reason,
            "success": run.status == "solved" and run.residual.solved and oracle["status"] == "valid",
            "output": output, "oracle": oracle, "work": asdict(counts),
            "accepted_steps": sum(event.step > 0 and event.decision is Decision.ACCEPT for event in run.trace),
            "retrieved_rule_ids": list(outcome.retrieved_ids), "committed_rule_uses": uses,
            "admissions": [{"id": record.candidate.id, "status": record.status,
                            "fingerprint": record.fingerprint} for record in outcome.admissions],
            "learning_errors": list(outcome.learning_errors)}


def _totals(rows: list[dict]) -> dict:
    return {"tasks": len(rows), "successes": sum(row["success"] for row in rows),
            "accepted_steps": sum(row["accepted_steps"] for row in rows),
            "work": {key: sum(row["work"][key] for row in rows) for key in asdict(WorkCounts())},
            "committed_rule_uses": sum(len(row["committed_rule_uses"]) for row in rows),
            "cross_task_rule_uses": sum(use["cross_task"] for row in rows for use in row["committed_rule_uses"]),
            "tasks_with_cross_task_reuse": sum(any(use["cross_task"] for use in row["committed_rule_uses"])
                                              for row in rows),
            "learning_errors": sum(len(row["learning_errors"]) for row in rows),
            "technical_errors": sum(row["status"] == "error" for row in rows)}


def _admission_probes() -> dict:
    """A separate, explicit denominator of invalid/unsupported candidate probes."""
    library = KnowledgeLibrary({"algebra": AlgebraVerifier()})
    probes = [
        KnowledgeCandidate("wrong-coefficient", "algebra", "polynomial_identity",
                           {"lhs": "(x+y)**2", "rhs": "x*x+y*y", "variables": ["x", "y"]},
                           derivation=({"lhs": "(x+y)**2", "rhs": "x*x+y*y"},), source_task_id="probe"),
        KnowledgeCandidate("broken-chain", "algebra", "polynomial_identity",
                           {"lhs": "x+x", "rhs": "2*x", "variables": ["x"]},
                           derivation=({"lhs": "x", "rhs": "x"},), source_task_id="probe"),
        KnowledgeCandidate("unproved-samples", "algebra", "polynomial_identity",
                           {"lhs": "x+x", "rhs": "2*x", "variables": ["x"]}, source_task_id="probe"),
        KnowledgeCandidate("symbolic-division", "algebra", "polynomial_identity",
                           {"lhs": "x/x", "rhs": "1", "variables": ["x"]},
                           derivation=({"lhs": "x/x", "rhs": "1"},), source_task_id="probe"),
        KnowledgeCandidate("missing-nonzero", "algebra", "equation_cancellation",
                           {"factor": "a", "lhs": "x", "rhs": "0", "variables": ["a", "x"]},
                           derivation=({"operation": "cancel_nonzero_factor", "factor": "a", "lhs": "x", "rhs": "0"},),
                           source_task_id="probe"),
        KnowledgeCandidate("zero-factor", "algebra", "equation_cancellation",
                           {"factor": "0", "lhs": "x", "rhs": "0", "variables": ["x"]},
                           assumptions=("0 != 0",),
                           derivation=({"operation": "cancel_nonzero_factor", "factor": "0", "lhs": "x", "rhs": "0"},),
                           source_task_id="probe"),
    ]
    results = [{"id": record.candidate.id, "status": record.status, "reason": record.verification.reason}
               for record in (library.admit(probe) for probe in probes)]
    return {"probes": len(results), "wrong_admissions": sum(row["status"] == "verified" for row in results),
            "results": results}


def run_study(*, max_steps: int = 64) -> dict:
    """Learn once, persist/reverify/freeze, compare fresh transfer task runs."""
    class CountedAdmissionVerifier:
        calls = 0

        def verify(self, candidate):
            self.calls += 1
            return AlgebraVerifier().verify(candidate)

    admission_verifier = CountedAdmissionVerifier()
    verifiers = {"algebra": admission_verifier}
    growing = KnowledgeLibrary(verifiers)
    learning = [_run(case, growing, learn=True, max_steps=max_steps) for case in DISCOVERY]
    discovery_admission_calls = admission_verifier.calls
    before_reload = _snapshot(growing)
    with tempfile.TemporaryDirectory(prefix="resimind-knowledge-eval-") as folder:
        path = Path(folder) / "knowledge.json"
        growing.save(path)
        frozen_file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        growing = KnowledgeLibrary.load(path, verifiers)
    reload_admission_calls = admission_verifier.calls - discovery_admission_calls
    frozen = _snapshot(growing)
    if frozen != before_reload:
        raise RuntimeError("reverified library changed on reload")
    fixed = KnowledgeLibrary(verifiers)
    fixed_initial = _snapshot(fixed)
    rows = []
    for case in TRANSFER:
        baseline = _run(case, fixed, learn=False, max_steps=max_steps)
        learned = _run(case, growing, learn=False, max_steps=max_steps)
        if _snapshot(fixed) != fixed_initial or _snapshot(growing) != frozen:
            raise RuntimeError("a transfer run mutated a frozen library")
        rows.append({"task_id": case.id, "fixed": baseline, "growing": learned})
    fixed_totals = _totals([row["fixed"] for row in rows])
    growing_totals = _totals([row["growing"] for row in rows])
    return {"schema_version": 1, "study": "verified-polynomial-knowledge-growth-v1",
            "evaluation_kind": "deterministic_development_mechanism_study", "model_calls": 0,
            "max_steps_per_task": max_steps, "discovery": learning, "discovery_totals": _totals(learning),
            "knowledge_verifier_calls": {"discovery_admission": discovery_admission_calls,
                                         "reload_reverification": reload_admission_calls,
                                         "transfer_admission": admission_verifier.calls - discovery_admission_calls - reload_admission_calls},
            "library": {"verified_rules": len(growing.lookup("algebra")),
                        "snapshot_sha256": _digest(frozen), "saved_file_sha256": frozen_file_digest,
                        "reload_reverified": True, "frozen_during_transfer": True,
                        "rules": [{"id": record.candidate.id, "source_task_id": record.candidate.source_task_id,
                                   "statement": record.candidate.statement, "fingerprint": record.fingerprint}
                                  for record in growing.lookup("algebra")]},
            "transfer": rows, "totals": {"fixed": fixed_totals, "growing": growing_totals},
            "admission_safety_probes": _admission_probes(),
            "limitations": ["Handmade development tasks; no randomized or held-out model evaluation.",
                            "Deterministic rewrite grammar; no neural discovery or SOTA claim.",
                            "Fewer proposal/check calls do not establish wall-time, token or monetary savings.",
                            "Discovery work is reported separately, not erased from lifetime cost.",
                            "Six adverse probes are a finite test, not a general soundness proof.",
                            "Goal is equivalent expanded syntax; collecting like terms is not required."]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="also save the JSON report at this path")
    args = parser.parse_args()
    report = run_study()
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
