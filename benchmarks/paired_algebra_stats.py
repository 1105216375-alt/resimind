"""Paired statistics for the frozen four-arm, two-repeat algebra study.

The expression is the independent sampling unit. Repeats and arms stay together
when resampling within each family. The exact sign-swap test swaps both repeats
of an expression together; its null requires paired arm-label exchangeability.
These statistics do not establish mathematical novelty or causal attribution.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import random


BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20_261_009
REQUIRED_ARMS = frozenset({
    "strong_verify_retry", "plain_residual", "structured_residual", "structured_growth",
})
COMPARISONS = (
    ("plain_residual", "structured_residual", True),
    ("structured_residual", "structured_growth", True),
    ("strong_verify_retry", "structured_growth", False),
)
STATEFUL_ARMS = (
    "strong_verify_retry", "legacy_structured_growth", "state_bound_growth", "executable_growth",
)
STATEFUL_COMPARISONS = (
    ("legacy_structured_growth", "state_bound_growth", True),
    ("state_bound_growth", "executable_growth", True),
    ("strong_verify_retry", "executable_growth", False),
)
ADAPTIVE_ARMS = (
    "strong_verify_retry", "executable_growth", "adaptive_growth", "symbolic_growth",
)
ADAPTIVE_COMPARISONS = (
    ("executable_growth", "adaptive_growth", True),
    ("symbolic_growth", "adaptive_growth", True),
    ("strong_verify_retry", "adaptive_growth", False),
)


def _exact_sign_swap(weights: list[int]) -> dict:
    """Exact two-sided paired test; weights are differences of two-success sums."""
    if any(type(weight) is not int or not -2 <= weight <= 2 for weight in weights):
        raise ValueError("weights must be integer differences from two repeats")
    observed = abs(sum(weights))
    nonzero = [abs(weight) for weight in weights if weight]
    counts = {0: 1}
    for weight in nonzero:
        updated: dict[int, int] = defaultdict(int)
        for total, count in counts.items():
            updated[total - weight] += count
            updated[total + weight] += count
        counts = dict(updated)
    numerator = sum(count for total, count in counts.items() if abs(total) >= observed)
    denominator = 2 ** len(nonzero)
    return {"p_value": numerator / denominator,
            "tail_assignments": numerator, "total_assignments": denominator,
            "nonzero_case_clusters": len(nonzero)}


def _holm(p_values: list[float]) -> list[float]:
    """Holm adjusted p-values, returned in original comparison order."""
    adjusted = [0.0] * len(p_values)
    previous = 0.0
    for rank, index in enumerate(sorted(range(len(p_values)), key=p_values.__getitem__)):
        previous = max(previous, min(1.0, (len(p_values) - rank) * p_values[index]))
        adjusted[index] = previous
    return adjusted


def _quantile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _configured_comparisons(arms, comparisons):
    if (type(arms) is not tuple or len(arms) != 4
            or any(type(arm) is not str or not arm.strip() or arm != arm.strip() for arm in arms)
            or len(set(arms)) != 4):
        raise ValueError("arms must name each of the four preregistered arms exactly once")
    if comparisons is None:
        if set(arms) != REQUIRED_ARMS:
            raise ValueError("default comparisons require the four v3 preregistered arms")
        return COMPARISONS
    if (type(comparisons) is not tuple or len(comparisons) != 3
            or any(type(item) is not tuple or len(item) != 3 for item in comparisons)):
        raise ValueError("comparisons must contain exactly three contrast tuples")
    for index, (baseline, treatment, primary) in enumerate(comparisons):
        if (type(baseline) is not str or type(treatment) is not str
                or baseline not in arms or treatment not in arms or baseline == treatment):
            raise ValueError("comparisons must name distinct declared baseline and treatment arms")
        if type(primary) is not bool or primary != (index < 2):
            raise ValueError("comparisons must contain two primary contrasts followed by one secondary")
    # The adaptive study prespecifies a shared treatment against three controls.
    # Allow only that exact named star, preserving the older chain contract.
    if set(arms) == set(ADAPTIVE_ARMS) and comparisons == ADAPTIVE_COMPARISONS:
        return comparisons
    first, second, reference = comparisons
    chain = (first[0], first[1], second[1], reference[0])
    if (first[1] != second[0] or second[1] != reference[1]
            or len(set(chain)) != 4 or set(chain) != set(arms)):
        raise ValueError("comparisons must form A→B, B→C and reference D→C across four distinct arms")
    return comparisons


def _validate(rows, arms):
    allowed_arms = frozenset(arms)
    rows = list(rows)
    if not rows:
        raise ValueError("rows must contain a complete study")
    cells, case_groups, repeat_ids = {}, {}, set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("every row must be a mapping")
        for key in ("task_id", "group", "arm"):
            if type(row.get(key)) is not str or not row[key].strip():
                raise ValueError(f"{key} must be nonempty text")
        if row["arm"] not in allowed_arms:
            raise ValueError("unexpected arm")
        if type(row.get("repeat")) is not int or row["repeat"] < 0:
            raise ValueError("repeat must be a nonnegative integer")
        if type(row.get("success")) is not bool:
            raise ValueError("success must be a boolean, including failures")
        if ("model_calls" in row
                and (type(row["model_calls"]) is not int or row["model_calls"] < 0)):
            raise ValueError("model_calls must be a nonnegative integer when present")
        task_id, group = row["task_id"], row["group"]
        if task_id in case_groups and case_groups[task_id] != group:
            raise ValueError("a task cannot change family across arms or repeats")
        case_groups[task_id] = group
        repeat_ids.add(row["repeat"])
        key = task_id, row["repeat"], row["arm"]
        if key in cells:
            raise ValueError("duplicate task/repeat/arm row")
        cells[key] = row["success"]
    if len(repeat_ids) != 2:
        raise ValueError("exactly two common repeat IDs are required")
    repeats, case_ids = sorted(repeat_ids), sorted(case_groups)
    for task_id in case_ids:
        for repeat in repeats:
            for arm in arms:
                if (task_id, repeat, arm) not in cells:
                    raise ValueError("incomplete task/repeat/arm grid; failures must not be dropped")
    groups: dict[str, list[int]] = defaultdict(list)
    for index, task_id in enumerate(case_ids):
        groups[case_groups[task_id]].append(index)
    sizes = {len(indices) for indices in groups.values()}
    if len(sizes) != 1 or min(sizes) < 2:
        raise ValueError("families must have equal case counts and at least two cases each")
    successes = {arm: [sum(cells[task_id, repeat, arm] for repeat in repeats)
                       for task_id in case_ids] for arm in arms}
    return rows, case_ids, repeats, dict(sorted(groups.items())), successes


def analyze(rows, arms: tuple[str, ...], *, comparisons=None) -> dict:
    """Analyze every provided case/repeat/arm, retaining failed episodes.

    Each row requires task_id, group, repeat, arm and a boolean success.
    Additional fields are ignored; model_calls, if present, is validated.
    The caller must also compare task IDs to the frozen manifest: no statistics
    helper can infer that an entire case or family was omitted from its input.

    The two primary contrasts are Holm corrected. A supported improvement needs
    at least +0.10 absolute success rate AND adjusted p < .05. Bootstrap 95%
    intervals are descriptive, not multiplicity-adjusted decision intervals.
    Without comparisons, v3 arms and contrasts are required and output remains
    unchanged. Explicit comparisons must preserve the four-arm chain A→B,
    B→C (primary), D→C (secondary); they change names, not inference rules.
    For v4 use STATEFUL_ARMS and comparisons=STATEFUL_COMPARISONS. The exact
    ADAPTIVE_ARMS / ADAPTIVE_COMPARISONS configuration additionally permits
    the prespecified v5 star; all inference and decision rules stay identical.
    """
    configured = _configured_comparisons(arms, comparisons)
    rows, case_ids, repeats, groups, successes = _validate(rows, arms)
    case_count, repeat_count = len(case_ids), len(repeats)
    denominator = case_count * repeat_count
    weights = [[treatment - baseline for baseline, treatment in
                zip(successes[base], successes[treat])]
               for base, treat, _ in configured]
    bootstrap = [[] for _ in configured]
    rng = random.Random(BOOTSTRAP_SEED)
    for _ in range(BOOTSTRAP_SAMPLES):
        selected = [rng.choice(indices) for indices in groups.values()
                    for _ in range(len(indices))]
        for values, distribution in zip(weights, bootstrap):
            distribution.append(sum(values[index] for index in selected) / denominator)
    tests = [_exact_sign_swap(values) for values in weights]
    adjusted = _holm([test["p_value"] for test in tests[:2]])
    comparisons = []
    for index, ((base, treatment, primary), values, distribution, test) in enumerate(
            zip(configured, weights, bootstrap, tests)):
        distribution.sort()
        delta = sum(values) / denominator
        corrected = adjusted[index] if primary else None
        comparisons.append({
            "baseline": base, "treatment": treatment, "primary": primary,
            "delta_success_rate": delta,
            "ci95": {"lower": _quantile(distribution, 0.025),
                     "upper": _quantile(distribution, 0.975)},
            "exact_two_sided_p": test["p_value"],
            "permutation": test,
            "holm_adjusted_p": corrected,
            "supported": (delta >= 0.10 and corrected < 0.05) if primary else None,
        })
    return {
        "schema_version": 1, "episodes": len(rows), "case_clusters": case_count,
        "repeats": repeats, "groups": {group: len(indices) for group, indices in groups.items()},
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED,
                      "unit": "expression_cluster", "stratified_by_family": True,
                      "interval": "descriptive_percentile_95", "quantile": "linear_interpolation"},
        "per_arm": {arm: {"successes": sum(values), "episodes": denominator,
                          "case_clusters": case_count, "success_rate": sum(values) / denominator}
                    for arm, values in successes.items()},
        "comparisons": comparisons,
        "decision_rule": {"primary_comparisons": 2, "minimum_absolute_delta": 0.10,
                          "holm_adjusted_p_strictly_below": 0.05,
                          "secondary_comparison_is_exploratory": True},
        "limitations": [
            "Two repeats per expression are clustered, not independent samples.",
            "Exact sign-swap inference assumes paired arm-label exchangeability within expressions.",
            "Bootstrap intervals are descriptive and not adjusted for multiple comparisons.",
            "Unsupported improvement is inconclusive, not proof of equivalence.",
            "Caller must validate the complete task inventory against the frozen manifest.",
        ],
    }
