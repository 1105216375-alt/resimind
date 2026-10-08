"""Independent benchmark-ground-truth tests, with no production verifier use."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from benchmarks.planning_cases import benchmark_cases
from benchmarks.planning_oracle import establish_ground_truth, score_itinerary


CASES = benchmark_cases()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_preregistered_ground_truth(case):
    truth = establish_ground_truth(case.problem)
    assert truth.status == case.expected_outcome
    assert truth.explored_prefixes > 0
    if truth.status == "feasible":
        assert score_itinerary(case.problem, truth.witness).valid
        assert score_itinerary(case.problem, json.dumps(truth.witness)).valid
    elif truth.status == "missing_evidence":
        assessment = score_itinerary(case.problem, truth.witness)
        assert assessment.status == "unknown"
        assert assessment.missing
    else:
        assert truth.witness is None


def test_case_labels_do_not_leak_to_brief_and_problem_id():
    assert len(CASES) == 12
    assert len({case.problem.brief for case in CASES}) == 1
    assert [case.problem.trip_id for case in CASES] == [f"trip-{i:02}" for i in range(1, 13)]
    assert [case.expected_outcome for case in CASES].count("feasible") == 8


@pytest.mark.parametrize("field", ["total_cost_cents", "total_walking_minutes"])
def test_fabricated_totals_are_rejected(field):
    problem = CASES[0].problem
    plan = deepcopy(establish_ground_truth(problem).witness)
    plan[field] += 1
    assert score_itinerary(problem, plan).status == "invalid"


def test_late_return_and_short_visit_are_rejected():
    problem = CASES[0].problem
    original = establish_ground_truth(problem).witness
    late = deepcopy(original)
    late["legs"][-1]["depart_minute"] = problem.day_end_minute
    assert any(reason.startswith("late_arrival:") for reason in score_itinerary(problem, late).violations)
    short = deepcopy(original)
    short["stops"][0]["end_minute"] = short["stops"][0]["start_minute"]
    assert any(reason.startswith("short_visit:") for reason in score_itinerary(problem, short).violations)


def test_route_endpoints_and_opening_windows_are_checked():
    problem = CASES[0].problem
    plan = deepcopy(establish_ground_truth(problem).witness)
    plan["legs"][0]["route_id"] = "gallery:station:transit"
    assert any(reason.startswith("disconnected_leg:") for reason in score_itinerary(problem, plan).violations)
    plan = deepcopy(establish_ground_truth(problem).witness)
    plan["stops"][0]["start_minute"] = 0
    assert any(reason.startswith("opening_window:") for reason in score_itinerary(problem, plan).violations)


def test_proven_impossible_constraints_are_independently_obvious():
    budget = CASES[8].problem
    assert min(p.cost_cents for p in budget.places if "art" in p.categories) > budget.budget_cents
    deadline = CASES[9].problem
    assert min(p.opens_minute + p.min_visit_minutes for p in deadline.places if "art" in p.categories) > deadline.day_end_minute


def test_unknown_routes_cannot_be_made_known_by_candidate_totals():
    for case in CASES[-2:]:
        plan = deepcopy(establish_ground_truth(case.problem).witness)
        plan["total_cost_cents"] = 0
        result = score_itinerary(case.problem, plan)
        assert not result.valid
        assert result.missing


def test_known_violation_takes_precedence_over_unknown_data():
    problem = CASES[-1].problem
    plan = establish_ground_truth(problem).witness
    result = score_itinerary(replace(problem, budget_cents=0), plan)
    assert result.status == "invalid"
    assert "over_budget" in result.violations
    assert result.missing


def test_oracle_accepts_more_than_one_solution_under_same_constraints():
    problem = CASES[0].problem
    first = establish_ground_truth(problem).witness
    # Remove a selected venue to force an independently found distinct plan.
    removed = first["stops"][0]["place_id"]
    alternate_problem = replace(problem, places=tuple(p for p in problem.places if p.id != removed),
                                travel=tuple(r for r in problem.travel if removed not in (r.origin, r.destination)))
    alternate = establish_ground_truth(alternate_problem).witness
    assert alternate is not None
    assert {s["place_id"] for s in first["stops"]} != {s["place_id"] for s in alternate["stops"]}
    assert score_itinerary(problem, alternate).valid


@pytest.mark.parametrize("malformed", [None, [], {"stops": []}, '{"stops":[],"stops":[]}'])
def test_malformed_claims_are_not_success(malformed):
    assert score_itinerary(CASES[0].problem, malformed).status == "invalid"


def test_boolean_total_is_not_an_integer_measurement():
    problem = CASES[0].problem
    plan = deepcopy(establish_ground_truth(problem).witness)
    plan["total_cost_cents"] = True
    assert score_itinerary(problem, plan).violations == ("invalid_schema",)


@pytest.mark.parametrize("change,reason", [
    ({"max_walking_minutes": 0}, "too_much_walking"),
    ({"budget_cents": 0}, "over_budget"),
    ({"required_categories": ("nonexistent-category",)}, "category_coverage"),
    ({"min_stops": 6}, "too_few_stops"),
])
def test_feasible_witness_fails_new_hard_constraint(change, reason):
    problem = CASES[0].problem
    plan = establish_ground_truth(problem).witness
    result = score_itinerary(replace(problem, **change), plan)
    assert result.status == "invalid"
    assert reason in result.violations


def test_rain_rule_covers_every_selected_venue():
    problem = CASES[0].problem
    plan = establish_ground_truth(problem).witness
    first_id = plan["stops"][0]["place_id"]
    rainy = replace(problem, rain=True, places=tuple(
        replace(p, indoor=False) if p.id == first_id else p for p in problem.places))
    assert f"outdoor_in_rain:{first_id}" in score_itinerary(rainy, plan).violations


def test_unknown_unused_route_does_not_taint_grounded_alternative():
    problem = CASES[0].problem
    plan = establish_ground_truth(problem).witness
    selected = {leg["route_id"] for leg in plan["legs"]}
    alternative_id = next(route.id for route in problem.travel if route.id not in selected)
    partial = replace(problem, travel=tuple(
        replace(route, minutes=None) if route.id == alternative_id else route for route in problem.travel))
    assert score_itinerary(partial, plan).valid
    assert establish_ground_truth(partial).status == "feasible"


def test_unknown_measurements_cannot_rescue_proven_budget_impossibility():
    problem = replace(CASES[-1].problem, budget_cents=2400)
    truth = establish_ground_truth(problem)
    assert truth.status == "infeasible"
    assert truth.witness is None
