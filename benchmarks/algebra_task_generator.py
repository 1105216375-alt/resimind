"""Seeded transfer expressions, with no expansion, scoring, or model calls.

The strata and coefficient distributions are public before the study seed is
drawn. Freshness means newly sampled expressions disjoint in Python AST from
the published development/discovery/transfer corpus and any explicitly supplied
exclusions; it does not mean unseen by a pretrained model or a new mathematical
family. This module deliberately has no default seed, reads no private study
paths, and generates nothing at import time.

All size estimates below are syntactic upper bounds. They neither construct
polynomial coefficients nor materialize an expected answer.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from math import comb, gcd, prod

from benchmarks.knowledge_growth import (
    Case, MAX_GRID_POINTS, MAX_SCORER_WORK, OracleUnsupported, _parse,
)
from resimind.domains.algebra import (
    MAX_AST_NODES, MAX_DEGREE, MAX_PRODUCTS, MAX_SOURCE_LENGTH, MAX_TERMS,
    PolynomialError, _tree, _variables,
)


GENERATOR_VERSION = "algebra-transfer-sha256-v1"
EXCLUSION_SCHEMA_VERSION = 1
EXCLUSION_POLICY_VERSION = "explicit-ast-exclusions-v1"
DEFAULT_PER_GROUP = 4
MAX_PER_GROUP = 64
MAX_DRAW_ATTEMPTS = 10_000
INTEGER_COEFFICIENTS = (-4, -3, -2, -1, 1, 2, 3, 4)
# Every pair is already reduced and nonintegral. Sampling is uniform over
# these ordered pairs, not uniform over denominator choices followed by a
# potentially biased simplification.
RATIONAL_COEFFICIENTS = tuple(
    (numerator, denominator)
    for numerator in range(-5, 6) if numerator
    for denominator in (2, 3, 5, 7)
    if gcd(abs(numerator), denominator) == 1
)


@dataclass(frozen=True)
class Group:
    id: str
    description: str
    variants: tuple[str, ...]


GROUPS = (
    Group("rational-binomial-square",
          "Square of a two-variable linear binomial; both signed nonintegral "
          "rational coefficients are sampled independently.", ("rational square",)),
    Group("affine-trinomial-cube",
          "Alternate a two-variable affine cube and a homogeneous three-variable "
          "trinomial cube; all three signed integer coefficients are independent.",
          ("affine cube", "trinomial cube")),
    Group("nested-square",
          "Square of the sum of a squared two-variable linear binomial and a "
          "second linear binomial; four signed integer coefficients are independent.",
          ("quadratic plus linear, squared",)),
    Group("conjugate-product",
          "Alternate (A+B)*(A-B) with A and B single-variable terms, and with A "
          "a two-variable linear binomial and B a third-variable term. Sample "
          "signed integer coefficients independently, then reuse them in the pair.",
          ("two-variable conjugates", "three-variable nested conjugates")),
    Group("three-factor-product",
          "Product of three homogeneous three-variable linear trinomials; "
          "all nine signed integer coefficients are independent.", ("three trinomials",)),
    Group("four-factor-product",
          "Product of four homogeneous three-variable linear trinomials; "
          "all twelve signed integer coefficients are independent.", ("four trinomials",)),
    Group("three-variable-high-power",
          "Alternate fifth and sixth powers of a homogeneous three-variable "
          "linear trinomial; three signed integer coefficients are independent.",
          ("fifth power", "sixth power")),
    Group("mixed-products-powers",
          "Cycle through a squared binomial times a cubed trinomial; a cubed "
          "trinomial times a squared binomial; the square of a product of two "
          "binomials plus a third-variable square; and a squared trinomial "
          "times two binomials. All signed integer coefficients are independent.",
          ("square times cube", "cube times square", "product plus square, squared",
           "square times two factors")),
)


def generator_metadata() -> dict:
    """Return a fresh JSON-safe public methods specification, without a seed."""
    return {
        "version": GENERATOR_VERSION,
        "default_per_group": DEFAULT_PER_GROUP,
        "default_task_count": len(GROUPS) * DEFAULT_PER_GROUP,
        "max_per_group": MAX_PER_GROUP,
        "sampler": (
            "For each group, SHA-256 of compact UTF-8 JSON [version, decimal seed "
            "string, group ID, counter], starting counter at zero. Interpret the "
            "digest as an unsigned big-endian integer; reject its incomplete "
            "modulo tail before selecting a coefficient uniformly. Separate "
            "group streams keep prefixes stable when per_group changes."
        ),
        "integer_coefficients": list(INTEGER_COEFFICIENTS),
        "rational_coefficients": [list(pair) for pair in RATIONAL_COEFFICIENTS],
        "coefficient_sampling": "Independent uniform draws with replacement, except conjugate reuse.",
        "variant_assignment": "Cycle through the listed variants by accepted case index within each group.",
        "groups": [{"id": group.id, "description": group.description,
                    "variants": list(group.variants)} for group in GROUPS],
        "duplicate_policy": (
            "Reject identical ast.dump(..., include_attributes=False) expression trees "
            "within the generated set and against all existing DEVELOPMENT, DISCOVERY, "
            "and TRANSFER cases in benchmarks.run_algebra_live_eval and "
            "benchmarks.knowledge_growth. This is AST disjointness, not algebraic inequivalence."
        ),
        "max_draw_attempts_per_group": MAX_DRAW_ATTEMPTS,
        "syntactic_bounds": {
            "variables": 3, "total_degree": 6, "monomials_in_compact_answer": 28,
            "compact_answer_ast_nodes": 810, "complete_oracle_grid_points": 343,
            "coefficient_numerator_bits": 24, "coefficient_denominator_bits": 24,
        },
        "validation": (
            "Syntactic grammar, degree, term-count, product-work and compact-answer "
            "AST/work upper bounds only; no coefficient expansion, expected answers, "
            "oracle scoring, model calls, or study seed generation."
        ),
        "scope": (
            "Fresh seed-sampled expressions from public mathematical families, AST-disjoint "
            "from the published corpus. No claim of pretrained-model unseen material "
            "or novel mathematical families."
        ),
    }


class _Sampler:
    """Versioned, runtime-independent uniform draws from a caller-supplied seed."""

    def __init__(self, seed: int, group: str):
        self.seed = str(seed)
        self.group = group
        self.counter = 0

    def choice(self, values: tuple):
        size = len(values)
        ceiling = (1 << 256) - (1 << 256) % size
        while True:
            payload = json.dumps(
                [GENERATOR_VERSION, self.seed, self.group, self.counter],
                ensure_ascii=True, separators=(",", ":"),
            ).encode("utf-8")
            self.counter += 1
            number = int.from_bytes(hashlib.sha256(payload).digest(), "big")
            if number < ceiling:
                return values[number % size]

    def integer(self) -> str:
        return str(self.choice(INTEGER_COEFFICIENTS))

    def rational(self) -> str:
        numerator, denominator = self.choice(RATIONAL_COEFFICIENTS)
        return f"({numerator}/{denominator})"


def _linear(sampler: _Sampler, variables: tuple[str, ...]) -> str:
    return "(" + "+".join(f"{sampler.integer()}*{name}" for name in variables) + ")"


def _draw_expression(sampler: _Sampler, group: str, index: int) -> tuple[str, tuple[str, ...]]:
    xy, xyz = ("x", "y"), ("x", "y", "z")
    if group == "rational-binomial-square":
        return f"({sampler.rational()}*x+{sampler.rational()}*y)**2", xy
    if group == "affine-trinomial-cube":
        if index % 2:
            return f"{_linear(sampler, xyz)}**3", xyz
        return f"({sampler.integer()}*x+{sampler.integer()}*y+{sampler.integer()})**3", xy
    if group == "nested-square":
        return f"({_linear(sampler, xy)}**2+{_linear(sampler, xy)})**2", xy
    if group == "conjugate-product":
        if index % 2:
            left, right = _linear(sampler, xy), f"{sampler.integer()}*z"
            return f"({left}+{right})*({left}-{right})", xyz
        left, right = f"{sampler.integer()}*x", f"{sampler.integer()}*y"
        return f"({left}+{right})*({left}-{right})", xy
    if group in ("three-factor-product", "four-factor-product"):
        factors = 3 if group == "three-factor-product" else 4
        return "*".join(_linear(sampler, xyz) for _ in range(factors)), xyz
    if group == "three-variable-high-power":
        return f"{_linear(sampler, xyz)}**{5 + index % 2}", xyz
    if group == "mixed-products-powers":
        variant = index % 4
        if variant == 0:
            return f"{_linear(sampler, xy)}**2*{_linear(sampler, xyz)}**3", xyz
        if variant == 1:
            return f"{_linear(sampler, xyz)}**3*{_linear(sampler, xy)}**2", xyz
        if variant == 2:
            return (f"({_linear(sampler, xy)}*{_linear(sampler, xy)}+"
                    f"{sampler.integer()}*z**2)**2"), xyz
        return (f"{_linear(sampler, xyz)}**2*{_linear(sampler, xy)}*"
                f"{_linear(sampler, ('x', 'z'))}"), xyz
    raise ValueError(f"Unknown generator group: {group}")


def _ast_key(expression: str) -> str:
    return ast.dump(ast.parse(expression.strip(), mode="eval").body, include_attributes=False)


def _extra_exclusions(exclude_expressions: tuple[str, ...] | list[str]) -> tuple[tuple[str, ...], set[str]]:
    """Validate explicit input syntactically, without evaluating coefficients."""
    if type(exclude_expressions) not in (tuple, list):
        raise TypeError("exclude_expressions must be an explicit tuple or list of strings")
    expressions = tuple(exclude_expressions)
    keys = set()
    for index, expression in enumerate(expressions):
        if type(expression) is not str:
            raise TypeError(f"exclude_expressions[{index}] must be a string")
        try:
            tree = _tree(expression)
            variables = tuple(sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}))
            _variables(variables)
            _parse(expression, variables)
        except (PolynomialError, OracleUnsupported, RecursionError) as exc:
            raise ValueError(f"exclude_expressions[{index}] must use bounded polynomial syntax") from exc
        keys.add(ast.dump(tree, include_attributes=False))
    return expressions, keys


def exclusion_metadata(exclude_expressions: tuple[str, ...] | list[str] = ()) -> dict:
    """Snapshot explicit exclusions for a runner's prospective manifest.

    The raw-list digest retains ordering, spelling and duplicates. The AST-set
    digest ignores redundant parentheses, whitespace, ordering and duplicates.
    This does not load, verify or authenticate any prior study: the caller must
    preserve its source manifest and bind that manifest's digest separately.
    Neither digest supplies a new task seed or changes coefficient sampling.
    """
    expressions, keys = _extra_exclusions(exclude_expressions)

    def digest(value):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    return {
        "schema_version": EXCLUSION_SCHEMA_VERSION,
        "exclusion_policy_version": EXCLUSION_POLICY_VERSION,
        "expressions": list(expressions),
        "expression_count": len(expressions),
        "unique_ast_count": len(keys),
        "expressions_sha256": digest(list(expressions)),
        "ast_set_sha256": digest(sorted(keys)),
        "policy": (
            "Reject exact AST matches against this explicit list in addition to the public-corpus "
            "and within-batch exclusions. Preserve this metadata and bind it to the source-study "
            "manifest before selecting a new seed. Strata and coefficient streams are unchanged."
        ),
        "hash_encoding": "Compact sorted-key JSON, UTF-8, ensure_ascii=False, allow_nan=False.",
    }


def _excluded_ast_keys() -> set[str]:
    # Lazy import avoids coupling import order to other benchmark harnesses.
    from benchmarks import knowledge_growth, run_algebra_live_eval

    return {
        _ast_key(case.expression)
        for module in (knowledge_growth, run_algebra_live_eval)
        for name in ("DEVELOPMENT", "DISCOVERY", "TRANSFER")
        for case in getattr(module, name, ())
    }


def _shape_bound(node: ast.AST, variables: int) -> tuple[int, int, int, int]:
    """Return min/max total degree, maximum terms, and multiplication work.

    Bounds ignore all coefficient values and cancellations. A homogeneous
    n-variable degree-d polynomial has at most C(d+n-1,n-1) terms; allowing
    smaller degrees gives C(d+n,n). Multiplication costs are bounded before
    combining terms, matching the production verifier's product accounting.
    """
    if isinstance(node, ast.Name):
        return 1, 1, 1, 0
    if isinstance(node, ast.Constant):
        return 0, 0, 1, 0
    if isinstance(node, ast.UnaryOp):
        return _shape_bound(node.operand, variables)
    low, high, terms, work = _shape_bound(node.left, variables)
    if isinstance(node.op, ast.Div):
        return low, high, terms, work

    def cap(minimum: int, maximum: int) -> int:
        return (comb(maximum + variables - 1, variables - 1) if minimum == maximum
                else comb(maximum + variables, variables))

    if isinstance(node.op, ast.Pow):
        # The independent syntax parser has already validated this literal.
        exponent = node.right.value
        current_low, current_high, current_terms = 0, 0, 1
        for _ in range(exponent):
            work += current_terms * terms
            current_low += low
            current_high += high
            current_terms = min(current_terms * terms, cap(current_low, current_high))
        return current_low, current_high, current_terms, work
    rlow, rhigh, rterms, rwork = _shape_bound(node.right, variables)
    if isinstance(node.op, ast.Mult):
        return (low + rlow, high + rhigh,
                min(terms * rterms, cap(low + rlow, high + rhigh)),
                work + rwork + terms * rterms)
    return (min(low, rlow), max(high, rhigh),
            min(terms + rterms, cap(min(low, rlow), max(high, rhigh))), work + rwork)


def _validate_bounds(expression: str, variables: tuple[str, ...]) -> None:
    """Check resource capacity without evaluating or expanding a polynomial."""
    production_tree = _tree(expression)
    _, degrees = _parse(expression, variables)
    _, total_degree, terms, products = _shape_bound(production_tree, len(variables))
    nodes = sum(1 for _ in ast.walk(production_tree))
    points = prod(degree + 1 for degree in degrees)
    # At most six AST nodes for a signed rational coefficient, five per
    # variable power, two per multiplication/addition. This intentionally
    # includes zero-degree factors rather than relying on simplification.
    answer_nodes = terms * (8 + 7 * len(variables)) - 2
    # Integer groups have |coefficient| <= 4 and at most degree 6, so the
    # sum of absolute coefficients is <= 12**6 < 2**22. Rational squares
    # have denominators <= 7**2. These loose 24-bit bounds cover both.
    answer_source_chars = terms * (20 + 10 * len(variables))
    # Building each compact monomial separately is conservative: <= one
    # scalar multiplication per variable and <= total_degree power steps.
    answer_products = terms * (len(variables) + total_degree)
    if (len(variables) > 3 or total_degree > min(6, MAX_DEGREE) or terms > min(28, MAX_TERMS)
            or answer_nodes > MAX_AST_NODES or answer_source_chars > MAX_SOURCE_LENGTH
            or points > MAX_GRID_POINTS or (nodes + answer_nodes) * points > MAX_SCORER_WORK
            or products + answer_products > MAX_PRODUCTS):
        raise ValueError("Generator shape exceeds predeclared syntax/work bounds")


def generate_cases(seed: int, per_group: int = DEFAULT_PER_GROUP, *,
                   exclude_expressions: tuple[str, ...] | list[str] = ()) -> tuple[Case, ...]:
    """Sample eight ordered strata from an explicit seed, without solutions.

    ``per_group`` must be in 1..64. The intended study uses the default four
    (32 total). Seed generation belongs to the frozen study runner, not here.
    ``exclude_expressions`` adds AST exclusions without changing strata or the
    sampler stream. Passing none preserves the original seeded output exactly.
    The caller supplies and freezes prior-study exclusions explicitly; no
    private paths, global random state, clock, or network are consulted.
    """
    if type(seed) is not int:
        raise TypeError("seed must be an explicit integer")
    if type(per_group) is not int or not 1 <= per_group <= MAX_PER_GROUP:
        raise ValueError(f"per_group must be an integer in 1..{MAX_PER_GROUP}")
    _, extra_keys = _extra_exclusions(exclude_expressions)
    seen = _excluded_ast_keys() | extra_keys
    cases = []
    for group in GROUPS:
        sampler = _Sampler(seed, group.id)
        accepted = attempts = 0
        while accepted < per_group:
            attempts += 1
            if attempts > MAX_DRAW_ATTEMPTS:
                raise RuntimeError(f"Exhausted duplicate rejection budget for {group.id}")
            expression, variables = _draw_expression(sampler, group.id, accepted)
            key = _ast_key(expression)
            if key in seen:
                continue
            _validate_bounds(expression, variables)
            seen.add(key)
            cases.append(Case(f"transfer-{group.id}-{accepted + 1:02d}", expression, variables, group.id))
            accepted += 1
    return tuple(cases)
