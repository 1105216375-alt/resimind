"""Anti-unify pairs of checked rewrites; independently prove every schema.

Shared syntax is retained, differing subtrees become shared metavariables.
This finite syntactic generalizer does not infer side conditions or induction.
Its output is universally checked in the bounded rational polynomial ring,
not established from sampled substitutions. Existing library admission and
application checks remain necessary.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations

from ..core import content_digest
from ..knowledge import KnowledgeCandidate, KnowledgeLibrary, KnowledgeRecord
from .algebra import validate_expression, verify_identity
from .polynomial_learning import WorkCounts, _bounded_macro
from .rule_abstraction import VerifiedExample


@dataclass(frozen=True, slots=True)
class SchemaDiscovery:
    candidates: tuple[KnowledgeCandidate, ...]
    input_checks: int
    schema_checks: int
    rejected_examples: int
    rejected_generalizations: int


def discover_schemas(examples: tuple[VerifiedExample, ...], *, max_pairs: int = 64) -> SchemaDiscovery:
    if type(examples) is not tuple or len(examples) > 32 or any(type(e) is not VerifiedExample for e in examples):
        raise ValueError("provide at most 32 VerifiedExample objects")
    if type(max_pairs) is not int or not 1 <= max_pairs <= 256:
        raise ValueError("max_pairs must be in 1..256")
    usable = []
    rejected = checks = bad_schemas = 0
    for example in examples:
        if (not example.source_id.strip() or
                verify_identity(example.before, example.after, example.variables).status != "verified"):
            rejected += 1
        else:
            usable.append(example)
    output: dict[str, KnowledgeCandidate] = {}
    for index, (one, two) in enumerate(combinations(usable, 2)):
        if index >= max_pairs:
            break
        if one.source_id == two.source_id or (one.before, one.after) == (two.before, two.after):
            continue
        holes: dict[tuple[str, str], str] = {}

        def generalize(a: ast.expr, b: ast.expr) -> ast.expr:
            if isinstance(a, ast.BinOp) and isinstance(b, ast.BinOp) and type(a.op) is type(b.op):
                return ast.BinOp(generalize(a.left, b.left), deepcopy(a.op), generalize(a.right, b.right))
            if isinstance(a, ast.UnaryOp) and isinstance(b, ast.UnaryOp) and type(a.op) is type(b.op):
                return ast.UnaryOp(deepcopy(a.op), generalize(a.operand, b.operand))
            if isinstance(a, ast.Constant) and isinstance(b, ast.Constant) and a.value == b.value:
                return deepcopy(a)
            key = (ast.dump(a), ast.dump(b))
            if key not in holes:
                holes[key] = f"P{len(holes)}"
            return ast.Name(holes[key], ast.Load())

        lhs_tree = generalize(ast.parse(one.before, mode="eval").body, ast.parse(two.before, mode="eval").body)
        rhs_tree = generalize(ast.parse(one.after, mode="eval").body, ast.parse(two.after, mode="eval").body)
        lhs, rhs = ast.unparse(ast.fix_missing_locations(lhs_tree)), ast.unparse(ast.fix_missing_locations(rhs_tree))
        variables = tuple(holes.values())
        lhs_names = {n.id for n in ast.walk(lhs_tree) if isinstance(n, ast.Name)}
        rhs_names = {n.id for n in ast.walk(rhs_tree) if isinstance(n, ast.Name)}
        if not variables or lhs == rhs or not rhs_names <= lhs_names:
            bad_schemas += 1
            continue
        checks += 1
        if verify_identity(lhs, rhs, variables).status != "verified":
            bad_schemas += 1
            continue
        statement = {"lhs": lhs, "rhs": rhs, "variables": list(variables)}
        ident = "abstract:" + content_digest(statement)[:24]
        prior = output.get(ident)
        sources = tuple(sorted({one.source_id, two.source_id} | (set(prior.evidence_refs) if prior else set())))
        output[ident] = KnowledgeCandidate(
            id=ident, domain="algebra", kind="polynomial_identity", statement=statement,
            derivation=({"lhs": lhs, "rhs": rhs},), source_task_id="schema-discovery",
            evidence_refs=sources,
        )
    return SchemaDiscovery(tuple(output[key] for key in sorted(output)), len(examples), checks, rejected, bad_schemas)


def apply_schema(library: KnowledgeLibrary, record: KnowledgeRecord, expression: str,
                 variables: tuple[str, ...], counts: WorkCounts | None = None) -> str | None:
    """Recheck current record, bounded substitution, and whole expression."""
    current = library.get(record.candidate.id)
    if current is None or current.status != "verified" or current.fingerprint != record.fingerprint:
        return None
    rule = current.candidate
    if rule.domain != "algebra" or rule.kind != "polynomial_identity" or rule.assumptions:
        return None
    validate_expression(expression, variables)
    counts = WorkCounts() if counts is None else counts
    after = _bounded_macro(expression, current, counts)
    if after is None:
        return None
    counts.identity_checks += 1
    if verify_identity(expression, after, variables).status != "verified":
        return None
    return after
