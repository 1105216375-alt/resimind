"""Independent, bounded feasibility oracle for the planning benchmark.

This module imports no production verifier, checker, or planning helper. It reads
the immutable problem's attributes and implements the benchmark specification.
It does not inspect model identity, agent decisions, or expected case labels.

Ground truth searches distinct-place paths and all listed route alternatives.
For a fixed path, earliest possible arrival, opening-time waiting and minimum
visits dominate any later schedule: all costs/durations are nonnegative and only
upper deadlines constrain departure/return. Consequently, exhausting these
earliest schedules is sufficient for feasibility, without enumerating minutes.
The search stops at a witness, or exhausts every admissible prefix.

Unknown measurements are excluded when finding a fully grounded witness. A
second, optimistic search supplies measurement lower bounds only to determine
whether missing data prevents a conclusion. Such a witness is explicitly NOT a
verified itinerary and is never supplied to the model or used as an agent hint.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re


@dataclass(frozen=True)
class OracleResult:
    status: str
    violations: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    total_cost_cents: int | None = None
    total_walking_minutes: int | None = None
    return_minute: int | None = None

    @property
    def valid(self) -> bool:
        return self.status == "valid"


@dataclass(frozen=True)
class GroundTruth:
    status: str
    witness: dict | None
    explanation: str
    explored_prefixes: int


def _object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate field")
        result[name] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite number")


def _integer(value, limit):
    return type(value) is int and 0 <= value <= limit


def _identifier(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value) is not None


def _decode(claim):
    if type(claim) is str:
        if len(claim) > 65536:
            raise ValueError("claim too long")
        claim = json.loads(claim, object_pairs_hook=_object, parse_constant=_nonfinite)
    if type(claim) is not dict:
        raise ValueError("require an object")
    required = {"stops", "legs", "total_cost_cents", "total_walking_minutes"}
    if set(claim) not in (required, required | {"rationale"}):
        raise ValueError("unexpected fields")
    if "rationale" in claim and (type(claim["rationale"]) is not str or
                                  not claim["rationale"].strip() or len(claim["rationale"]) > 4000):
        raise ValueError("invalid rationale")
    stops, legs = claim["stops"], claim["legs"]
    if type(stops) is not list or not 1 <= len(stops) <= 12:
        raise ValueError("invalid stops")
    if type(legs) is not list or len(legs) != len(stops) + 1:
        raise ValueError("invalid leg count")
    if not _integer(claim["total_cost_cents"], 10**8) or not _integer(claim["total_walking_minutes"], 1440):
        raise ValueError("invalid totals")
    for stop in stops:
        if (type(stop) is not dict or set(stop) != {"place_id", "start_minute", "end_minute"}
                or not _identifier(stop["place_id"])
                or not _integer(stop["start_minute"], 1440) or not _integer(stop["end_minute"], 1440)):
            raise ValueError("invalid stop")
    for leg in legs:
        if (type(leg) is not dict or set(leg) != {"route_id", "depart_minute"}
                or not _identifier(leg["route_id"]) or not _integer(leg["depart_minute"], 1440)):
            raise ValueError("invalid leg")
    return claim


def score_itinerary(problem, claim: dict | str) -> OracleResult:
    """Score a raw itinerary; declarations never substitute for recomputation.

    ``unknown`` means an otherwise non-refuted itinerary uses missing selected
    route data. A known violation takes precedence over unknown measurements.
    Partial sums can refute budget limits, but are returned as ``None`` where
    the complete sum is unknown. Arbitrary optional prose is never scored.
    """
    try:
        plan = _decode(claim)
    except (ValueError, TypeError, KeyError, RecursionError):
        return OracleResult("invalid", ("invalid_schema",))
    stops, legs = plan["stops"], plan["legs"]
    places = {p.id: p for p in problem.places}
    routes = {r.id: r for r in problem.travel}
    issues, unknowns = [], []
    visit_ids = [stop["place_id"] for stop in stops]
    if len(stops) < problem.min_stops:
        issues.append("too_few_stops")
    if len(set(visit_ids)) != len(visit_ids):
        issues.append("repeated_place")
    if any(identifier not in places for identifier in visit_ids):
        issues.append("unknown_place")
    if any(leg["route_id"] not in routes for leg in legs):
        issues.append("unknown_route")
    if "unknown_place" in issues or "unknown_route" in issues:
        return OracleResult("invalid", tuple(issues))
    coverage = set()
    known_cost = sum(places[identifier].cost_cents for identifier in visit_ids)
    known_walk = 0
    complete_cost, complete_walk = True, True
    for visit in stops:
        place = places[visit["place_id"]]
        coverage.update(place.categories)
        start, end = visit["start_minute"], visit["end_minute"]
        if end - start < place.min_visit_minutes:
            issues.append(f"short_visit:{place.id}")
        if not (place.opens_minute <= start and end <= place.closes_minute):
            issues.append(f"opening_window:{place.id}")
        if problem.rain and not place.indoor:
            issues.append(f"outdoor_in_rain:{place.id}")
    if not set(problem.required_categories).issubset(coverage):
        issues.append("category_coverage")
    path = [problem.origin, *visit_ids, problem.origin]
    return_time = None
    for index, leg in enumerate(legs):
        route = routes[leg["route_id"]]
        depart = leg["depart_minute"]
        ready = problem.day_start_minute if index == 0 else stops[index - 1]["end_minute"]
        deadline = problem.day_end_minute if index == len(stops) else stops[index]["start_minute"]
        if (route.origin, route.destination) != (path[index], path[index + 1]):
            issues.append(f"disconnected_leg:{route.id}")
        if depart < ready:
            issues.append(f"departure_before_ready:{route.id}")
        if depart > deadline:
            issues.append(f"departure_after_deadline:{route.id}")
        for field in ("minutes", "cost_cents", "walking_minutes"):
            if getattr(route, field) is None:
                unknowns.append(f"{route.id}:{field}")
        if route.minutes is not None:
            arrival = depart + route.minutes
            if arrival > deadline:
                issues.append(f"late_arrival:{route.id}")
            if index == len(stops):
                return_time = arrival
        if route.cost_cents is None:
            complete_cost = False
        else:
            known_cost += route.cost_cents
        if route.walking_minutes is None:
            complete_walk = False
        else:
            known_walk += route.walking_minutes
    if known_cost > problem.budget_cents:
        issues.append("over_budget")
    if known_walk > problem.max_walking_minutes:
        issues.append("too_much_walking")
    if complete_cost and known_cost != plan["total_cost_cents"]:
        issues.append("incorrect_cost_total")
    if complete_walk and known_walk != plan["total_walking_minutes"]:
        issues.append("incorrect_walking_total")
    status = "invalid" if issues else ("unknown" if unknowns else "valid")
    return OracleResult(status, tuple(dict.fromkeys(issues)), tuple(unknowns),
                        known_cost if complete_cost else None,
                        known_walk if complete_walk else None, return_time)


def _measurements(route, optimistic):
    values = (route.minutes, route.cost_cents, route.walking_minutes)
    if not optimistic:
        return None if None in values else values
    # Consistent minimum completion, not a claim that these measurements exist.
    duration = route.minutes
    walking = route.walking_minutes
    if duration is None:
        duration = max(1, walking or 0)
    if walking is None:
        walking = duration if route.mode == "walk" else 0
    fare = 0 if route.cost_cents is None else route.cost_cents
    return duration, fare, walking


def _search(problem, optimistic=False):
    places = {p.id: p for p in problem.places}
    outgoing = {}
    for route in problem.travel:
        measured = _measurements(route, optimistic)
        if measured is not None:
            outgoing.setdefault(route.origin, []).append((route, measured))
    explored = 0

    def walk(node, ready, stops, legs, seen, coverage, cost, walking):
        nonlocal explored
        explored += 1
        for route, (duration, fare, access) in outgoing.get(node, ()):
            next_cost, next_walk = cost + fare, walking + access
            arrival = ready + duration
            if (next_cost > problem.budget_cents or next_walk > problem.max_walking_minutes
                    or arrival > problem.day_end_minute):
                continue
            leg = {"route_id": route.id, "depart_minute": ready}
            if route.destination == problem.origin:
                if len(stops) >= problem.min_stops and set(problem.required_categories) <= coverage:
                    return {"stops": stops, "legs": legs + [leg], "total_cost_cents": next_cost,
                            "total_walking_minutes": next_walk}
                continue
            if route.destination in seen:
                continue
            place = places[route.destination]
            if problem.rain and not place.indoor:
                continue
            start = max(arrival, place.opens_minute)
            end = start + place.min_visit_minutes
            next_cost += place.cost_cents
            if end > min(place.closes_minute, problem.day_end_minute) or next_cost > problem.budget_cents:
                continue
            visit = {"place_id": place.id, "start_minute": start, "end_minute": end}
            witness = walk(place.id, end, stops + [visit], legs + [leg], seen | {place.id},
                           coverage | set(place.categories), next_cost, next_walk)
            if witness is not None:
                return witness
        return None

    return walk(problem.origin, problem.day_start_minute, [], [], set(), set(), 0, 0), explored


def establish_ground_truth(problem) -> GroundTruth:
    """Prove feasible, refute all paths, or identify indispensable unknown data.

    Limited to at most eight places for a deliberately bounded benchmark oracle;
    this is independent enumeration, not a general planning solver. A feasible
    witness is a raw itinerary. A missing-evidence witness is an optimistic
    diagnostic only; scoring it against the original input must remain unknown.
    """
    if not 1 <= len(problem.places) <= 8:
        raise ValueError("benchmark oracle supports 1–8 places")
    witness, explored = _search(problem)
    if witness is not None:
        if not score_itinerary(problem, witness).valid:
            raise AssertionError("search witness failed independent feasibility scoring")
        return GroundTruth("feasible", witness,
                           "Constructive witness using only known measurements; earliest schedules enumerate all listed choices.",
                           explored)
    has_unknown = any(None in (r.minutes, r.cost_cents, r.walking_minutes) for r in problem.travel)
    if has_unknown:
        optimistic, extra = _search(problem, optimistic=True)
        explored += extra
        if optimistic is not None:
            return GroundTruth("missing_evidence", optimistic,
                               "No fully measured itinerary exists; a lower-bound completion of unknown measurements admits this diagnostic path. It is not a verified plan.",
                               explored)
    return GroundTruth("infeasible", None,
                       "All distinct-stop paths and listed route choices were exhausted with earliest legal schedules; nonnegative bounds also exclude any unknown-measurement completion.",
                       explored)
