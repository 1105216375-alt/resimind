"""Exact benchmark oracle for the declared synthetic two-span line beam.

This independently implements the same linear Euler--Bernoulli physics as the
adapter: a three-rotation stiffness system is solved with rational arithmetic.
It does not call the adapter's proposer, verifier, or private calculation
helpers. Agreement is an implementation cross-check, not independent validation
of the physical model and not a real bridge safety or design-code assessment.
Only the benchmark's m/Nm2/kNpm/kNm/mm input units are supported.
"""
from __future__ import annotations

from fractions import Fraction

from resimind.domains.bridge import ContinuousBridgeProblem


def _rational(value: int | str) -> Fraction:
    if type(value) not in (int, str):
        raise ValueError("oracle inputs must be exact integers or rational strings")
    return Fraction(value)


def _solve(matrix: list[list[Fraction]], rhs: list[Fraction]) -> tuple[Fraction, ...]:
    """Gauss--Jordan elimination; no floating-point stiffness calculation."""
    size = len(rhs)
    rows = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = next(i for i in range(column, size) if rows[i][column])
        rows[column], rows[pivot] = rows[pivot], rows[column]
        scale = rows[column][column]
        rows[column] = [entry / scale for entry in rows[column]]
        for i in range(size):
            if i != column:
                scale = rows[i][column]
                rows[i] = [a - scale * b for a, b in zip(rows[i], rows[column])]
    return tuple(row[-1] for row in rows)


def _same(actual: object, expected: object) -> bool:
    """Do not accept floats or booleans that compare equal to exact numbers."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, (tuple, list)):
        return len(actual) == len(expected) and all(
            _same(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def score_bridge(problem: ContinuousBridgeProblem, cases: dict, summary: dict) -> bool:
    """Check every case and its envelope against an independent exact solution.

    ``cases`` and ``summary`` use the public ``verified_case_results`` and
    ``verified_summary`` formats. Invalid output returns False; unsupported or
    incomplete problem definitions raise ValueError instead of being scored.
    Envelope ties retain the first declared case and then the first location
    in the order 0, span length, interior zero-shear point.
    """
    units = (problem.span_unit, problem.rigidity_unit, problem.load_unit,
             problem.moment_unit, problem.deflection_unit)
    if units != ("m", "Nm2", "kNpm", "kNm", "mm"):
        raise ValueError("oracle supports only m/Nm2/kNpm/kNm/mm input units")
    flags = (problem.linear_elastic, problem.small_deflection,
             problem.prismatic_per_span, problem.no_support_settlement,
             problem.continuous_at_pier)
    if (any(flag is not True for flag in flags)
            or problem.model != "two_span_euler_bernoulli"
            or problem.supports != "three_vertical_restraints_free_end_rotations"):
        raise ValueError("oracle requires the declared continuous linear beam assumptions")

    lengths = tuple(map(_rational, problem.spans))
    rigidities = tuple(map(_rational, problem.flexural_rigidities))
    dead = tuple(_rational(value) * 1000 for value in problem.dead_loads)
    live = tuple(_rational(value) * 1000 for value in problem.live_loads)
    moment_limit = _rational(problem.moment_limit) * 1000
    displacement_limit = _rational(problem.midspan_deflection_limit) / 1000
    if (min(*lengths, *rigidities, moment_limit, displacement_limit) <= 0
            or min(*dead, *live) < 0):
        raise ValueError("oracle requires positive geometry, stiffness and limits; nonnegative loads")

    l1, l2 = lengths
    e1, e2 = rigidities
    k1, k2 = e1 / l1, e2 / l2
    zero = Fraction(0)
    matrix = [[4 * k1, 2 * k1, zero],
              [2 * k1, 4 * (k1 + k2), 2 * k2],
              [zero, 2 * k2, 4 * k2]]
    expected_cases = {}
    for combination in problem.combinations:
        dead_factor = _rational(combination.dead_factor)
        live_factor = _rational(combination.live_factor)
        if min(dead_factor, live_factor) < 0:
            raise ValueError("oracle requires nonnegative load factors")
        for pattern, active in (("none", (0, 0)), ("left", (1, 0)),
                                ("right", (0, 1)), ("both", (1, 1))):
            case = f"{combination.name}:{pattern}"
            q = tuple(dead[i] * dead_factor + live[i] * live_factor * active[i]
                      for i in range(2))
            # Consistent nodal moments for downward uniform loads. Vertical
            # displacements are restrained, leaving exactly three rotations.
            f1, f2 = q[0] * l1**2 / 12, q[1] * l2**2 / 12
            theta = _solve(matrix, [-f1, f1 - f2, f2])
            polynomials = []
            for i, (length, ei, load) in enumerate(zip(lengths, rigidities, q)):
                # Cubic Hermite displacement plus the uniform-load quartic.
                left, right = theta[i:i + 2]
                c1 = left
                c2 = -(2 * left + right) / length - load * length**2 / (24 * ei)
                c3 = (left + right) / length**2 + load * length / (12 * ei)
                c4 = -load / (24 * ei)
                polynomials.append((c1, c2, c3, c4))
            pier = 2 * e2 * polynomials[1][1]
            ra = 6 * e1 * polynomials[0][2]
            rc = q[1] * l2 - 6 * e2 * polynomials[1][2]
            rb = q[0] * l1 + q[1] * l2 - ra - rc
            expected_cases[case] = {"q": q, "pier_moment": pier,
                                    "reactions": (ra, rb, rc),
                                    "v1": polynomials[0], "v2": polynomials[1]}

    if not _same(cases, expected_cases):
        return False

    envelope = {"cases": list(expected_cases), "positive_max": [None, None],
                "midspan_abs_max": [None, None], "reaction_min": [None, None, None]}

    def retain(key: str, value: Fraction, case: str, *, minimum: bool = False,
               index: int | None = None, x: Fraction | None = None) -> None:
        previous = envelope.get(key) if index is None else envelope[key][index]
        if previous is not None:
            previous_value = Fraction(previous["value"])
            if not (value < previous_value if minimum else value > previous_value):
                return
        entry = {"value": str(value), "case": case}
        if x is not None:
            entry["x"] = str(x)
        if index is None:
            envelope[key] = entry
        else:
            envelope[key][index] = entry

    for case, row in expected_cases.items():
        retain("pier_min", row["pier_moment"], case, minimum=True)
        for i, reaction in enumerate(row["reactions"]):
            retain("reaction_min", reaction, case, minimum=True, index=i)
        for i, (length, ei) in enumerate(zip(lengths, rigidities)):
            coefficients = row[f"v{i + 1}"]
            c1, c2, c3, c4 = coefficients
            # Recover the quadratic bending moment from the independently
            # solved displacement, then inspect its endpoints and vertex.
            constant, linear, quadratic = 2 * ei * c2, 6 * ei * c3, 12 * ei * c4
            locations = [zero, length]
            if quadratic:
                vertex = -linear / (2 * quadratic)
                if 0 < vertex < length:
                    locations.append(vertex)
            for x in locations:
                moment = constant + linear * x + quadratic * x**2
                retain("positive_max", moment, case, index=i, x=x)
                retain("moment_abs_max", abs(moment), case)
            x = length / 2
            displacement = sum(coefficient * x**power
                               for power, coefficient in enumerate(coefficients, 1))
            retain("midspan_abs_max", abs(displacement), case, index=i)

    comparisons = {
        "moment_within_supplied_limit": Fraction(envelope["moment_abs_max"]["value"]) <= moment_limit,
        "midspans_within_supplied_limit": all(
            Fraction(row["value"]) <= displacement_limit for row in envelope["midspan_abs_max"]),
        "all_support_reactions_nonnegative": all(
            Fraction(row["value"]) >= 0 for row in envelope["reaction_min"]),
    }
    return _same(summary, {"envelope": envelope, "comparisons": comparisons})
