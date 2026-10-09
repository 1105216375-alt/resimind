"""Cluster-aware inference and complete-grid validation for paired studies."""
from copy import deepcopy
from itertools import product

import pytest

from benchmarks.paired_algebra_stats import (
    ADAPTIVE_ARMS, ADAPTIVE_COMPARISONS, COMPARISONS, STATEFUL_ARMS,
    STATEFUL_COMPARISONS, _exact_sign_swap, _holm, analyze,
)


ARMS = ("strong_verify_retry", "plain_residual", "structured_residual", "structured_growth")


def rows_for(patterns=None, *, cases=4):
    patterns = patterns or {}
    return [
        {"task_id": f"case-{case:02d}", "group": f"family-{case % 2}",
         "repeat": repeat, "arm": arm,
         "success": patterns.get(arm, [(True, True)] * cases)[case][repeat],
         "model_calls": 1}
        for case in range(cases) for repeat in range(2) for arm in ARMS
    ]


@pytest.mark.parametrize("weights", [[], [0, 0], [2], [1, -1], [2, 2, 2], [2, 2, -1], [0, 1, -2]])
def test_exact_dynamic_program_matches_all_sign_assignments(weights):
    nonzero = [abs(weight) for weight in weights if weight]
    totals = [sum(sign * weight for sign, weight in zip(signs, nonzero))
              for signs in product((-1, 1), repeat=len(nonzero))]
    tail = sum(abs(total) >= abs(sum(weights)) for total in totals)
    result = _exact_sign_swap(weights)
    assert result["p_value"] == tail / len(totals)
    assert result["tail_assignments"] == tail
    assert result["total_assignments"] == len(totals)


def test_repeated_successes_are_clustered_for_permutation():
    result = _exact_sign_swap([2, 2, 2])
    assert result["nonzero_case_clusters"] == 3
    assert result["total_assignments"] == 8
    assert result["p_value"] == 0.25  # Not 2 / 64 from treating six repeats as independent.


def test_holm_respects_original_order_and_step_down_monotonicity():
    assert _holm([0.04, 0.01]) == [0.04, 0.02]
    assert _holm([0.03, 0.04]) == [0.06, 0.06]
    assert _holm([1.0, 1.0]) == [1.0, 1.0]


def test_ties_keep_pairs_repeats_and_case_counts():
    outcomes = [(False, True), (True, False), (False, False), (True, True)]
    result = analyze(rows_for({arm: outcomes for arm in ARMS}), ARMS)
    assert result["episodes"] == 32
    assert result["case_clusters"] == 4
    assert result["repeats"] == [0, 1]
    assert result["groups"] == {"family-0": 2, "family-1": 2}
    assert result["bootstrap"]["samples"] == 10_000
    assert result["bootstrap"]["seed"] == 20_261_009
    for arm in ARMS:
        assert result["per_arm"][arm] == {
            "successes": 4, "episodes": 8, "case_clusters": 4, "success_rate": 0.5,
        }
    for comparison in result["comparisons"]:
        assert comparison["delta_success_rate"] == 0
        assert comparison["ci95"] == {"lower": 0, "upper": 0}
        assert comparison["exact_two_sided_p"] == 1
        assert comparison["supported"] is (False if comparison["primary"] else None)


def test_bootstrap_keeps_fixed_family_mix_and_is_input_order_independent():
    patterns = {
        "plain_residual": [(False, False), (True, True), (False, False), (True, True)],
        "structured_residual": [(True, True), (False, False), (True, True), (False, False)],
    }
    rows = rows_for(patterns)
    result = analyze(rows, ARMS)
    # Every resample contains equal numbers from the +1 and -1 families.
    assert result["comparisons"][0]["ci95"] == {"lower": 0, "upper": 0}
    assert analyze(list(reversed(rows)), ARMS) == result


def test_supported_requires_primary_delta_and_corrected_significance():
    rows = rows_for({"plain_residual": [(False, False)] * 8}, cases=8)
    result = analyze(rows, ARMS)
    improved, unchanged, secondary = result["comparisons"]
    assert improved["delta_success_rate"] == 1
    assert improved["exact_two_sided_p"] == 2 / 256
    assert improved["holm_adjusted_p"] == 4 / 256
    assert improved["supported"] is True
    assert unchanged["supported"] is False
    assert secondary["holm_adjusted_p"] is None
    assert secondary["supported"] is None


def test_nominal_significance_is_not_enough_after_holm_correction():
    rows = rows_for({"plain_residual": [(False, False)] * 6}, cases=6)
    result = analyze(rows, ARMS)
    comparison = result["comparisons"][0]
    assert comparison["exact_two_sided_p"] == 0.03125
    assert comparison["holm_adjusted_p"] == 0.0625
    assert comparison["supported"] is False


@pytest.mark.parametrize("mutation, message", [
    (lambda rows: rows.append(deepcopy(rows[0])), "duplicate"),
    (lambda rows: rows.pop(), "incomplete"),
    (lambda rows: rows[0].update(group="another-family"), "change family"),
    (lambda rows: rows[0].update(success=1), "boolean"),
    (lambda rows: rows[0].update(success=None), "boolean"),
    (lambda rows: rows[0].update(repeat=True), "nonnegative integer"),
    (lambda rows: rows[0].update(repeat=-1), "nonnegative integer"),
    (lambda rows: rows[0].update(repeat=9), "two common repeat"),
    (lambda rows: rows[0].update(arm="unregistered"), "unexpected arm"),
    (lambda rows: rows[0].update(task_id=""), "nonempty"),
    (lambda rows: rows[0].update(model_calls=-1), "model_calls"),
    (lambda rows: rows[0].update(model_calls=True), "model_calls"),
])
def test_invalid_or_missing_rows_fail_instead_of_dropping_failures(mutation, message):
    rows = rows_for()
    mutation(rows)
    with pytest.raises(ValueError, match=message):
        analyze(rows, ARMS)


def test_whole_missing_case_is_detected_if_it_unbalances_families():
    rows = [row for row in rows_for() if row["task_id"] != "case-00"]
    with pytest.raises(ValueError, match="equal case counts"):
        analyze(rows, ARMS)


def test_missing_entire_repeat_is_not_silently_single_run_analysis():
    rows = [row for row in rows_for() if row["repeat"] == 0]
    with pytest.raises(ValueError, match="two common repeat"):
        analyze(rows, ARMS)


@pytest.mark.parametrize("arms", [ARMS[:-1], ARMS + (ARMS[0],), list(ARMS),
                                 (ARMS[0], ARMS[0], ARMS[2], ARMS[3])])
def test_arm_inventory_must_match_preregistered_contrasts(arms):
    with pytest.raises(ValueError, match="preregistered arms"):
        analyze(rows_for(), arms)


def test_empty_study_and_singleton_families_cannot_produce_claims():
    with pytest.raises(ValueError, match="complete study"):
        analyze([], ARMS)
    with pytest.raises(ValueError, match="at least two"):
        analyze(rows_for(cases=2), ARMS)


def stateful_rows(rows=None):
    names = dict(zip(ARMS, STATEFUL_ARMS))
    return [{**row, "arm": names[row["arm"]]} for row in (rows_for() if rows is None else rows)]


def test_explicit_v3_configuration_keeps_default_output_unchanged():
    rows = rows_for({"plain_residual": [(False, False)] * 8}, cases=8)
    assert analyze(rows, ARMS, comparisons=COMPARISONS) == analyze(rows, ARMS)


def test_adaptive_star_tests_tools_control_without_changing_inference_rules():
    names = dict(zip(ARMS, ADAPTIVE_ARMS))
    rows = [{**row, "arm": names[row["arm"]]} for row in rows_for({
        "plain_residual": [(False, False)] * 8,
        "structured_growth": [(True, True)] * 8,
    }, cases=8)]
    result = analyze(rows, ADAPTIVE_ARMS, comparisons=ADAPTIVE_COMPARISONS)
    first, second, reference = result["comparisons"]
    assert first["baseline"] == "executable_growth"
    assert first["delta_success_rate"] == 1 and first["supported"] is True
    assert first["holm_adjusted_p"] == 4 / 256
    assert second["baseline"] == "symbolic_growth"
    assert second["delta_success_rate"] == 0 and second["supported"] is False
    assert reference["primary"] is False and reference["supported"] is None
    assert analyze(list(reversed(rows)), tuple(reversed(ADAPTIVE_ARMS)),
                   comparisons=ADAPTIVE_COMPARISONS)["comparisons"] == result["comparisons"]


def test_adaptive_star_is_not_an_arbitrary_post_hoc_contrast_escape_hatch():
    names = dict(zip(ARMS, ADAPTIVE_ARMS))
    rows = [{**row, "arm": names[row["arm"]]} for row in rows_for()]
    swapped = (ADAPTIVE_COMPARISONS[1], ADAPTIVE_COMPARISONS[0], ADAPTIVE_COMPARISONS[2])
    with pytest.raises(ValueError, match="must form"):
        analyze(rows, ADAPTIVE_ARMS, comparisons=swapped)


def test_stateful_contrasts_change_names_without_changing_statistics_or_thresholds():
    rows = rows_for({
        "plain_residual": [(False, False)] * 8,
        "structured_residual": [(True, False)] * 8,
    }, cases=8)
    expected = deepcopy(analyze(rows, ARMS))
    names = dict(zip(ARMS, STATEFUL_ARMS))
    expected["per_arm"] = {names[arm]: values for arm, values in expected["per_arm"].items()}
    for comparison in expected["comparisons"]:
        comparison["baseline"] = names[comparison["baseline"]]
        comparison["treatment"] = names[comparison["treatment"]]
    result = analyze(stateful_rows(rows), STATEFUL_ARMS, comparisons=STATEFUL_COMPARISONS)
    assert result == expected
    assert result["case_clusters"] == 8 and result["episodes"] == 64
    assert result["per_arm"]["state_bound_growth"]["successes"] == 8
    assert result["per_arm"]["executable_growth"]["successes"] == 16
    assert [(c["baseline"], c["treatment"], c["primary"]) for c in result["comparisons"]] == list(STATEFUL_COMPARISONS)
    assert [c["supported"] for c in result["comparisons"]] == [True, True, None]


def test_stateful_names_cannot_silently_use_v3_default_contrasts():
    with pytest.raises(ValueError, match="default comparisons"):
        analyze(stateful_rows(), STATEFUL_ARMS)


@pytest.mark.parametrize("comparisons", [
    (), STATEFUL_COMPARISONS[:-1], STATEFUL_COMPARISONS + (STATEFUL_COMPARISONS[0],),
    list(STATEFUL_COMPARISONS),
    (list(STATEFUL_COMPARISONS[0]), *STATEFUL_COMPARISONS[1:]),
    (("legacy_structured_growth", "state_bound_growth"), *STATEFUL_COMPARISONS[1:]),
    (("legacy_structured_growth", "state_bound_growth", 1), *STATEFUL_COMPARISONS[1:]),
    (("legacy_structured_growth", "state_bound_growth", False), *STATEFUL_COMPARISONS[1:]),
    (*STATEFUL_COMPARISONS[:2], ("strong_verify_retry", "executable_growth", True)),
    (("legacy_structured_growth", "legacy_structured_growth", True), *STATEFUL_COMPARISONS[1:]),
    (("outside-inventory", "state_bound_growth", True), *STATEFUL_COMPARISONS[1:]),
    ((True, "state_bound_growth", True), *STATEFUL_COMPARISONS[1:]),
    (STATEFUL_COMPARISONS[0], ("strong_verify_retry", "executable_growth", True), STATEFUL_COMPARISONS[2]),
    (*STATEFUL_COMPARISONS[:2], ("strong_verify_retry", "state_bound_growth", False)),
    (*STATEFUL_COMPARISONS[:2], ("legacy_structured_growth", "executable_growth", False)),
])
def test_configured_contrasts_cannot_change_inventory_roles_or_comparison_chain(comparisons):
    with pytest.raises(ValueError, match="comparisons"):
        analyze(stateful_rows(), STATEFUL_ARMS, comparisons=comparisons)


@pytest.mark.parametrize("mutation, message", [
    (lambda rows: rows.append(deepcopy(rows[0])), "duplicate"),
    (lambda rows: rows.pop(), "incomplete"),
    (lambda rows: rows[0].update(group="changed"), "change family"),
    (lambda rows: rows[0].update(arm="plain_residual"), "unexpected arm"),
    (lambda rows: rows[0].update(success=1), "boolean"),
])
def test_stateful_analysis_keeps_complete_case_and_failure_validation(mutation, message):
    rows = stateful_rows()
    mutation(rows)
    with pytest.raises(ValueError, match=message):
        analyze(rows, STATEFUL_ARMS, comparisons=STATEFUL_COMPARISONS)


@pytest.mark.parametrize("arms", [
    STATEFUL_ARMS[:-1], list(STATEFUL_ARMS),
    (STATEFUL_ARMS[0], STATEFUL_ARMS[0], STATEFUL_ARMS[2], STATEFUL_ARMS[3]),
    ("", *STATEFUL_ARMS[1:]), (" strong_verify_retry", *STATEFUL_ARMS[1:]),
])
def test_explicit_configuration_still_requires_four_distinct_named_arms(arms):
    with pytest.raises(ValueError, match="preregistered arms"):
        analyze(stateful_rows(), arms, comparisons=STATEFUL_COMPARISONS)
