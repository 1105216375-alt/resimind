"""Bounded structural retrieval for already admitted polynomial schemas.

Retrieval determines applicability candidates, never correctness. Each use
still checks the live record, whole identity, and optional formal proof.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
from ..knowledge import KnowledgeLibrary
from .polynomial_learning import _match


def _shape(node):
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow): return ('Pow', ast.dump(node.right))
        return (type(node.op).__name__,)
    if isinstance(node, ast.UnaryOp): return (type(node.op).__name__,)
    return (type(node).__name__,)


@dataclass
class RetrievalStats:
    indexed_rules: int = 0
    skipped_rules: int = 0
    queries: int = 0
    pattern_checks: int = 0
    cache_hits: int = 0


class RuleIndex:
    def __init__(self, library: KnowledgeLibrary, *, max_rules: int = 256):
        if type(max_rules) is not int or not 1 <= max_rules <= 4096:
            raise ValueError('max_rules must be in 1..4096')
        self.stats = RetrievalStats()
        self._buckets, self._cache = {}, {}
        records = library.lookup('algebra', kind='polynomial_identity', assumptions=())
        self.stats.skipped_rules = max(0, len(records) - max_rules)
        for record in records[:max_rules]:
            if record.candidate.assumptions: continue
            statement = record.candidate.statement
            pattern = ast.parse(statement['lhs'], mode='eval').body
            variables = set(statement['variables'])
            # A bare metavariable can rewrite everything and is not a useful
            # expansion schema. Keep that general operation out of retrieval.
            if isinstance(pattern, ast.Name): continue
            self._buckets.setdefault(_shape(pattern), []).append((record, pattern, variables))
            self.stats.indexed_rules += 1

    def lookup(self, expression: str, *, limit: int = 16):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError('limit must lie in 1..256')
        self.stats.queries += 1
        if expression in self._cache:
            self.stats.cache_hits += 1
            return self._cache[expression][:limit]
        root = ast.parse(expression, mode='eval').body
        found = {}
        for node in ast.walk(root):
            if not isinstance(node, ast.expr): continue
            for record, pattern, variables in self._buckets.get(_shape(node), ()):
                if record.candidate.id in found: continue
                self.stats.pattern_checks += 1
                if _match(pattern, node, variables, {}):
                    found[record.candidate.id] = record
        result = tuple(found.values())
        self._cache[expression] = result
        return result[:limit]
