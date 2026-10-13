"""Shared expression DAG proposals and bounded sparse expansion certificates.

The proposer uses its own arithmetic and binary powering. The gate evaluates
the original source with the existing independent, sequential-power ring
verifier, then compares every coefficient. Neither sampling nor cached flags
authorize an answer. Output terms are data, so long expanded text does not
have to fit the input AST bound. This is exact algebra, not a Lean certificate.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, asdict
from fractions import Fraction
import math
import time

from ..core import content_digest
from ..knowledge import Verification
from .algebra import (MAX_COEFFICIENT_BITS, MAX_DEGREE, MAX_PRODUCTS, MAX_TERMS,
                      PolynomialError, _Ring, _tree, _variables)
from .polynomial_simplification import _parse, _integer, _terms_node


@dataclass(frozen=True, slots=True)
class SparseTerm:
    powers: tuple[int, ...]
    numerator: int
    denominator: int = 1


@dataclass(frozen=True, slots=True)
class ExpansionCertificate:
    source: str
    variables: tuple[str, ...]
    terms: tuple[SparseTerm, ...]

    @property
    def fingerprint(self):
        return content_digest(asdict(self))

    def render(self) -> str:
        """Human-readable balanced output, not a substitute for verification."""
        terms = _read_terms(self, self.variables)
        node = _terms_node(terms, self.variables)
        return ast.unparse(ast.fix_missing_locations(node))


@dataclass(frozen=True, slots=True)
class CompactBudget:
    products: int = 100_000
    terms: int = 4096
    nodes: int = 1024

    def __post_init__(self):
        for name, bound in (('products', MAX_PRODUCTS), ('terms', MAX_TERMS), ('nodes', 1024)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= bound:
                raise ValueError(f'{name} must lie in 1..{bound}')


@dataclass
class CompactStats:
    unique_nodes: int = 0
    subexpressions: int = 0
    shared_uses: int = 0
    products: int = 0
    peak_terms: int = 0
    verifier_products: int = 0


@dataclass(frozen=True, slots=True)
class CompactRun:
    status: str
    certificate: ExpansionCertificate | None
    verification: Verification
    stats: CompactStats
    seconds: float


def _read_terms(certificate, variables):
    if type(certificate) is not ExpansionCertificate or type(certificate.terms) is not tuple:
        raise PolynomialError('malformed sparse certificate')
    if len(certificate.terms) > MAX_TERMS:
        raise PolynomialError('sparse term bound exceeded')
    values = {}
    previous = None
    for term in certificate.terms:
        if (type(term) is not SparseTerm or type(term.powers) is not tuple
                or len(term.powers) != len(variables)
                or any(type(p) is not int or not 0 <= p <= MAX_DEGREE for p in term.powers)
                or sum(term.powers) > MAX_DEGREE
                or type(term.numerator) is not int or type(term.denominator) is not int
                or not term.numerator or term.denominator <= 0
                or abs(term.numerator).bit_length() > MAX_COEFFICIENT_BITS
                or term.denominator.bit_length() > MAX_COEFFICIENT_BITS
                or math.gcd(term.numerator, term.denominator) != 1):
            raise PolynomialError('invalid canonical sparse term')
        if previous is not None and term.powers <= previous:
            raise PolynomialError('sparse terms must be unique and sorted')
        previous = term.powers
        values[term.powers] = Fraction(term.numerator, term.denominator)
    return values


def verify_expansion(source: str, variables: tuple[str, ...], certificate: ExpansionCertificate,
                     *, stats: CompactStats | None = None) -> Verification:
    """Bind to the requested source and variables; re-evaluate independently."""
    verifier = 'exact-rational-sparse-certificate-v1'
    try:
        names = _variables(variables)
        if (type(certificate) is not ExpansionCertificate or type(certificate.variables) is not tuple
                or certificate.source != source or certificate.variables != names):
            return Verification('rejected', 'certificate input binding mismatch', verifier)
        proposed = _read_terms(certificate, names)
        ring = _Ring(names)
        expected = ring.read(_tree(source))
        if stats is not None: stats.verifier_products += ring.products
        if proposed != expected:
            return Verification('rejected', 'sparse coefficients do not match the input polynomial', verifier)
        return Verification('verified', 'all exact rational coefficients agree', verifier)
    except (ValueError, TypeError, RecursionError):
        return Verification('unknown', 'unsupported certificate or verifier resource bound', verifier)


def propose_expansion(source: str, variables: tuple[str, ...], *, budget: CompactBudget = CompactBudget(),
                      stats: CompactStats | None = None) -> ExpansionCertificate:
    """Compute a candidate without calling the equality verifier or its ring."""
    if type(budget) is not CompactBudget:
        raise ValueError('expected CompactBudget')
    root, names = _parse(source, variables)  # Bounded syntax only; no expansion.
    stats = CompactStats() if stats is None else stats
    nodes, ids = [], {}

    def intern(node):
        stats.subexpressions += 1
        if isinstance(node, ast.Name): key = ('var', names.index(node.id))
        elif isinstance(node, ast.Constant): key = ('int', _integer(node))
        elif isinstance(node, ast.UnaryOp): key = (type(node.op).__name__, intern(node.operand))
        elif isinstance(node.op, (ast.Pow, ast.Div)):
            key = (type(node.op).__name__, intern(node.left), _integer(node.right))
        else: key = (type(node.op).__name__, intern(node.left), intern(node.right))
        if key in ids:
            stats.shared_uses += 1
            return ids[key]
        if len(nodes) >= budget.nodes:
            raise ValueError('DAG node budget exhausted')
        ids[key] = len(nodes); nodes.append(key)
        return ids[key]

    target = intern(root)
    stats.unique_nodes = len(nodes)
    zero = (0,) * len(names)
    values = []

    def bounded(poly):
        if len(poly) > budget.terms: raise ValueError('sparse term budget exhausted')
        stats.peak_terms = max(stats.peak_terms, len(poly))
        for powers, value in poly.items():
            if (sum(powers) > MAX_DEGREE or abs(value.numerator).bit_length() > MAX_COEFFICIENT_BITS
                    or value.denominator.bit_length() > MAX_COEFFICIENT_BITS):
                raise ValueError('sparse arithmetic bound exceeded')
        return poly

    def multiply(a, b):
        cost = len(a) * len(b)
        if stats.products + cost > budget.products: raise ValueError('product budget exhausted')
        stats.products += cost
        result = {}
        for pa, ca in a.items():
            for pb, cb in b.items():
                power = tuple(x+y for x, y in zip(pa, pb))
                coefficient = result.get(power, Fraction()) + ca*cb
                if coefficient: result[power] = coefficient
                else: result.pop(power, None)
                if len(result) > budget.terms: raise ValueError('sparse term budget exhausted')
        return bounded(result)

    for node in nodes:
        op = node[0]
        if op == 'int': value = {zero: Fraction(node[1])} if node[1] else {}
        elif op == 'var': value = {tuple(int(i == node[1]) for i in range(len(names))): Fraction(1)}
        elif op in ('UAdd', 'USub'):
            value = {p: c * (-1 if op == 'USub' else 1) for p, c in values[node[1]].items()}
        elif op == 'Div': value = {p: c / node[2] for p, c in values[node[1]].items()}
        elif op == 'Pow':
            base, exponent, value = values[node[1]], node[2], {zero: Fraction(1)}
            while exponent:
                if exponent & 1: value = multiply(value, base)
                exponent >>= 1
                if exponent: base = multiply(base, base)
        elif op == 'Mult': value = multiply(values[node[1]], values[node[2]])
        else:
            value = dict(values[node[1]])
            for power, coefficient in values[node[2]].items():
                total = value.get(power, Fraction()) + coefficient * (-1 if op == 'Sub' else 1)
                if total: value[power] = total
                else: value.pop(power, None)
        values.append(bounded(value))
    return ExpansionCertificate(source, names, tuple(SparseTerm(p, c.numerator, c.denominator)
        for p, c in sorted(values[target].items())))


def run_compact(source: str, variables: tuple[str, ...], *, budget: CompactBudget = CompactBudget()) -> CompactRun:
    started = time.perf_counter(); stats = CompactStats()
    try:
        certificate = propose_expansion(source, variables, budget=budget, stats=stats)
        verdict = verify_expansion(source, variables, certificate, stats=stats)
    except (ValueError, TypeError, RecursionError):
        certificate = None
        verdict = Verification('unknown', 'proposal grammar or resource bound', 'exact-rational-sparse-certificate-v1')
    solved = verdict.status == 'verified'
    return CompactRun('solved' if solved else 'limited', certificate if solved else None,
                      verdict, stats, time.perf_counter() - started)
