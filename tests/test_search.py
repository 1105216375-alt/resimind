"""Search may backtrack; only complete verification may release a plan."""
from collections import UserDict
from copy import deepcopy
import itertools
import json
import math

import pytest

import resimind.search as search_module
from resimind.search import SearchLimits, bounded_search


def search(initial=None, *, expand=None, verifier=None, **kwargs):
    return bounded_search(
        {"x": 0} if initial is None else initial,
        expand=expand or (lambda draft, checks: []),
        verifier=verifier or (lambda draft: {"complete": draft["x"] == 2}),
        **kwargs,
    )


def test_verified_initial_draft_needs_no_expansion_even_with_zero_expansion_budget():
    initial = {"x": 2, "nested": [1]}
    result = search(initial, limits=SearchLimits(max_generated=1, max_expansions=0, max_depth=0))
    assert result.accepted and result.plan == initial
    assert result.generated == result.evaluated == 1 and result.expansions == 0
    assert result.checks == result.best_checks == {"complete": True}
    result.plan["nested"].append(2)
    assert initial == result.best_draft == {"x": 2, "nested": [1]}


def test_backtracking_can_temporarily_lose_a_passed_check_and_still_complete():
    visited = []

    def expand(draft, checks):
        visited.append((draft["x"], dict(checks)))
        return [{"x": draft["x"] + 1}] if draft["x"] < 2 else []

    result = search(expand=expand, verifier=lambda p: {
        "originally_passed": p["x"] != 1, "goal": p["x"] == 2,
    })
    assert result.accepted and result.plan == {"x": 2}
    assert visited == [(0, {"originally_passed": True, "goal": False}),
                       (1, {"originally_passed": False, "goal": False})]
    assert result.generated == result.evaluated == 3


def test_unchanged_check_counts_do_not_prevent_exploration():
    result = search(expand=lambda p, _: [{"x": p["x"] + 1}])
    assert result.accepted and result.expansions == 2


def test_failed_best_draft_is_separate_from_a_deliverable_plan():
    result = search(expand=lambda p, _: [{"x": 1}] if p["x"] == 0 else [],
                    verifier=lambda p: {"progress": p["x"] == 1, "missing": None})
    assert result.status == "exhausted" and result.plan is None and result.checks == {}
    assert result.best_draft == {"x": 1}
    assert result.best_checks == {"progress": True, "missing": None}


def test_priority_is_deterministic_best_first_then_fifo_and_not_a_proof():
    visits = []

    def expand(p, _):
        visits.append(p["x"])
        return [{"x": x} for x in (1, 2, 3, 4)] if p["x"] == 0 else []

    def verify(p):
        return {"better": p["x"] in (2, 3, 4),
                "unknown": None if p["x"] == 2 else False,
                "never": False}

    first = search(expand=expand, verifier=verify)
    assert visits == [0, 3, 4, 2, 1]
    visits.clear()
    second = search(expand=expand, verifier=verify)
    assert visits == [0, 3, 4, 2, 1] and first.trace == second.trace
    assert first.plan is None and first.best_draft == {"x": 3}


def test_last_allowed_generated_candidate_is_checked_and_can_be_accepted():
    result = search(expand=lambda p, _: [{"x": 1}, {"x": 2}],
                    limits=SearchLimits(max_generated=3, max_expansions=1, max_frontier=1))
    assert result.accepted and result.generated == result.evaluated == 3


def test_last_allowed_expansion_can_return_a_complete_candidate():
    result = search(expand=lambda p, _: [{"x": 2}], limits=SearchLimits(max_expansions=1))
    assert result.accepted and result.expansions == 1


def test_generation_budget_includes_initial_duplicates_and_invalid_values():
    count = 0

    def expand(p, _):
        nonlocal count
        for candidate in ({"x": 0}, object(), {"x": 1}, {"x": 2}):
            count += 1
            yield candidate

    result = search(expand=expand, limits=SearchLimits(max_generated=4))
    assert result.status == "limited" and result.reason == "max_generated"
    assert result.generated == 4 and result.evaluated == 2
    assert result.duplicates == result.invalid == 1 and count == 3


def test_infinite_successor_generator_stops_without_one_extra_advance():
    advances = []

    def infinite(p, _):
        for value in itertools.count():
            advances.append(value)
            yield {"x": 0}

    result = search(expand=infinite, limits=SearchLimits(max_generated=4))
    assert result.status == "limited" and advances == [0, 1, 2]
    assert result.duplicates == 3 and result.evaluated == 1


def test_canonical_deduplication_stops_cycles_but_keeps_json_types_distinct():
    result = search({"x": 0, "y": 1}, expand=lambda p, _: [{"y": 1, "x": 0}],
                    verifier=lambda p: {"never": False})
    assert result.status == "exhausted" and result.duplicates == 1
    result = search({"x": 0}, expand=lambda p, _: [{"x": False}, {"x": 0.0}],
                    verifier=lambda p: {"never": False}, limits=SearchLimits(max_expansions=1))
    assert result.evaluated == 3 and result.duplicates == 0


def test_depth_limit_checks_terminal_draft_without_expanding_it():
    visits = []

    def expand(p, _):
        visits.append(p["x"])
        return [{"x": p["x"] + 1}]

    stopped = search(expand=expand, limits=SearchLimits(max_depth=1))
    assert stopped.status == "limited" and stopped.reason == "max_depth"
    assert visits == [0] and stopped.evaluated == 2
    accepted = search(expand=expand, limits=SearchLimits(max_depth=2))
    assert accepted.accepted and accepted.plan == {"x": 2}


def test_frontier_prunes_worst_but_verifies_all_generated_candidates():
    visits = []

    def expand(p, _):
        visits.append(p["x"])
        return [{"x": 1}, {"x": 2}, {"x": 3}] if p["x"] == 0 else []

    result = search(expand=expand, verifier=lambda p: {
        "better": p["x"] == 2, "unfinished": False,
    }, limits=SearchLimits(max_frontier=1))
    assert result.status == "limited" and result.reason == "max_frontier"
    assert result.evaluated == 4 and visits == [0, 2] and result.frontier_peak == 1


@pytest.mark.parametrize("checks", [{}, {"": True}, {" ": True}, {1: True},
                                   {"pass": 1}, {"pass": "yes"}, None, [True]])
def test_malformed_verifier_output_never_releases_a_plan(checks):
    result = search(verifier=lambda p: checks)
    assert result.status == "deferred" and result.reason == "invalid_assessment"
    assert result.plan is None and result.expansions == 0


def test_mapping_verifier_result_is_snapshotted_and_names_cannot_disappear():
    shared = UserDict({"required": False})

    def verify(p):
        shared.clear()
        shared.update({"required": False} if p["x"] == 0 else {"replacement": True})
        return shared

    result = search(expand=lambda p, _: [{"x": 2}], verifier=verify)
    assert result.status == "deferred" and result.reason == "changed_check_names"
    assert result.best_checks == {"required": False} and result.plan is None
    shared["required"] = True
    assert result.best_checks == {"required": False}


@pytest.mark.parametrize("check", [False, None])
def test_single_known_failure_or_unknown_cannot_be_accepted(check):
    result = search(verifier=lambda p: {"otherwise": True, "required": check})
    assert result.status == "exhausted" and not result.accepted and result.plan is None


def test_successor_structure_may_change_only_when_whole_verifier_approves():
    result = search({"partial": True}, expand=lambda p, _: [{"completed": [1, 2]}],
                    verifier=lambda p: {"schema": p == {"completed": [1, 2]}})
    assert result.accepted and result.plan == {"completed": [1, 2]}


@pytest.mark.parametrize("who", ["verifier", "expand", "iterator", "assessment"])
def test_mutating_callbacks_defer_and_leave_initial_data_untouched(who):
    initial = {"x": 0, "nested": [1]}
    original = deepcopy(initial)

    def verifier(p):
        if who == "verifier":
            p["nested"].append(2)
        return {"complete": p["x"] == 2}

    def expand(p, checks):
        if who == "expand":
            p["nested"].append(2)
        if who == "assessment":
            checks["complete"] = True

        def iterator():
            if who == "iterator":
                p["nested"].append(2)
            yield {"x": 2}

        return iterator()

    result = search(initial, expand=expand, verifier=verifier)
    assert result.status == "deferred" and result.reason == "callback_mutated_input"
    assert result.plan is None and initial == original


def test_mutation_when_generator_finishes_is_detected():
    def expand(p, _):
        yield {"x": 1}
        p["x"] = 2

    result = search(expand=expand)
    assert result.status == "deferred" and result.reason == "callback_mutated_input"


@pytest.mark.parametrize("who", ["verifier", "expand", "iterator"])
def test_callback_exceptions_never_release_a_partial_draft(who):
    def verifier(p):
        if who == "verifier":
            raise RuntimeError("private callback data should not enter trace")
        return {"complete": False}

    def expand(p, _):
        if who == "expand":
            raise RuntimeError("private callback data should not enter trace")

        def iterator():
            yield {"x": 1}
            raise RuntimeError("private callback data should not enter trace")

        return iterator()

    result = search(expand=expand, verifier=verifier)
    assert result.status == "deferred" and result.plan is None
    assert "private callback" not in json.dumps(result.trace)


@pytest.mark.parametrize("who", ["initial", "verifier", "expand", "iterator"])
def test_deadline_before_or_after_callback_cannot_accept_late_result(monkeypatch, who):
    now = [100.0 if who == "initial" else 0.0]
    monkeypatch.setattr(search_module, "monotonic", lambda: now[0])
    calls = []

    def verifier(p):
        calls.append("verify")
        if who == "verifier":
            now[0] = 100.0
        return {"complete": p["x"] == 2 or who == "verifier"}

    def expand(p, _):
        calls.append("expand")
        if who == "expand":
            now[0] = 100.0

        def iterator():
            calls.append("next")
            if who == "iterator":
                now[0] = 100.0
            yield {"x": 2}

        return iterator()

    result = search(expand=expand, verifier=verifier, deadline=100.0)
    assert result.status == "limited" and result.reason == "deadline" and result.plan is None
    if who == "initial":
        assert calls == [] and result.evaluated == 0


def test_trace_is_bounded_and_contains_no_draft_payload():
    result = search({"x": 0, "secret": "private draft content"},
                    verifier=lambda p: {"never": False},
                    expand=lambda p, _: itertools.repeat(p),
                    limits=SearchLimits(max_generated=10, max_trace=2))
    assert len(result.trace) == 2 and result.trace_dropped > 0
    assert "private draft content" not in json.dumps(result.trace)
    assert search(limits=SearchLimits(max_trace=0)).trace == []


@pytest.mark.parametrize("bad", [math.nan, math.inf, (1, 2), {1: 2}, {"x": [math.nan]}])
def test_invalid_initial_json_is_configuration_error_but_bad_successors_are_skipped(bad):
    with pytest.raises(ValueError):
        bounded_search(bad, expand=lambda p, c: [], verifier=lambda p: {"done": False})
    result = search(expand=lambda p, _: [bad, {"x": 2}])
    assert result.accepted and result.invalid == 1 and result.generated == 3


def test_oversized_cyclic_and_deep_successors_are_bounded_and_skipped():
    cyclic = []
    cyclic.append(cyclic)
    deep = []
    for _ in range(70):
        deep = [deep]
    result = search(expand=lambda p, _: [{"x": "z" * 1000}, cyclic, deep, {"x": 2}],
                    limits=SearchLimits(max_json_bytes=128))
    assert result.accepted and result.invalid == 3


@pytest.mark.parametrize("name,value", [
    ("max_expansions", -1), ("max_generated", 0), ("max_depth", -1),
    ("max_frontier", 0), ("max_json_bytes", 0), ("max_trace", -1),
    ("max_generated", True), ("max_expansions", 1.5),
])
def test_bad_limits_are_configuration_errors(name, value):
    with pytest.raises(ValueError):
        SearchLimits(**{name: value})


@pytest.mark.parametrize("deadline", [True, "tomorrow", math.nan, math.inf, 10 ** 400])
def test_invalid_deadline_is_configuration_error(deadline):
    with pytest.raises(ValueError):
        search(deadline=deadline)
