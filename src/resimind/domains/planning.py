"""Open-ended itineraries verified against a fictional, immutable day snapshot.

Feasibility is verified; enjoyment, accessibility, optimality, live availability,
weather forecasts and booking success are not. The proposer may supply a rationale,
but it never becomes a fact. No search, booking, or messaging is performed.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import json
import re

from ..adapters import ModelProposer
from ..agent import Agent, AgentResult, Task
from ..core import Candidate, Decision, Evidence, Fact, Residual, State, Verdict, canonical_json, content_digest

DOMAIN = "open_planning"
ACTION = "propose_itinerary"
TARGET = "trip:verified_plan"
PLAN_FACT = "trip:plan_checked"
EVIDENCE_IDS = ("trip:request", "trip:places", "trip:transport")


def _identifier(value):
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value):
        raise ValueError("require a bounded ASCII identifier")


def _integer(value, maximum=10**8):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("require a bounded nonnegative integer, not a boolean")


def _text(value, maximum=2000):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ValueError("require bounded nonempty text")


def _tags(values):
    if type(values) is not tuple or len(values) > 12:
        raise ValueError("categories must be a unique bounded tuple")
    for value in values:
        _identifier(value)
    if len(set(values)) != len(values):
        raise ValueError("categories must be unique")


@dataclass(frozen=True, slots=True)
class Place:
    id: str
    name: str
    categories: tuple[str, ...]
    opens_minute: int
    closes_minute: int
    min_visit_minutes: int
    cost_cents: int
    indoor: bool

    def __post_init__(self):
        _identifier(self.id)
        _text(self.name, 120)
        _tags(self.categories)
        for value in (self.opens_minute, self.closes_minute, self.min_visit_minutes):
            _integer(value, 1440)
        _integer(self.cost_cents)
        if not self.opens_minute < self.closes_minute or self.min_visit_minutes < 1:
            raise ValueError("invalid opening window or minimum visit")
        if type(self.indoor) is not bool:
            raise ValueError("indoor must be boolean")


@dataclass(frozen=True, slots=True)
class TravelOption:
    id: str
    origin: str
    destination: str
    mode: str
    minutes: int | None
    cost_cents: int | None
    walking_minutes: int | None

    def __post_init__(self):
        for value in (self.id, self.origin, self.destination):
            _identifier(value)
        if self.origin == self.destination or type(self.mode) is not str or self.mode not in ("walk", "transit"):
            raise ValueError("require a distinct origin/destination and walk or transit mode")
        for value, maximum in ((self.minutes, 1440), (self.walking_minutes, 1440), (self.cost_cents, 10**8)):
            if value is not None:
                _integer(value, maximum)
        if self.minutes is not None and self.minutes < 1:
            raise ValueError("travel duration must be positive when known")
        if self.minutes is not None and self.walking_minutes is not None:
            if self.walking_minutes > self.minutes or (self.mode == "walk" and self.walking_minutes != self.minutes):
                raise ValueError("walking duration is inconsistent with travel duration")
        if self.mode == "walk" and self.cost_cents not in (0, None):
            raise ValueError("walking has no fare in this adapter")


@dataclass(frozen=True, slots=True)
class TripProblem:
    trip_id: str
    brief: str
    origin: str
    day_start_minute: int
    day_end_minute: int
    budget_cents: int
    max_walking_minutes: int
    min_stops: int
    required_categories: tuple[str, ...]
    rain: bool
    places: tuple[Place, ...]
    travel: tuple[TravelOption, ...]

    def __post_init__(self):
        _identifier(self.trip_id)
        _identifier(self.origin)
        _text(self.brief)
        for value in (self.day_start_minute, self.day_end_minute, self.max_walking_minutes):
            _integer(value, 1440)
        _integer(self.budget_cents)
        _integer(self.min_stops, 12)
        _tags(self.required_categories)
        if not self.day_start_minute < self.day_end_minute or self.min_stops < 1:
            raise ValueError("invalid day window or minimum stop count")
        if type(self.rain) is not bool:
            raise ValueError("rain must be boolean")
        if type(self.places) is not tuple or not 1 <= len(self.places) <= 12 or any(type(p) is not Place for p in self.places):
            raise ValueError("require 1-12 immutable places")
        if type(self.travel) is not tuple or len(self.travel) > 256 or any(type(t) is not TravelOption for t in self.travel):
            raise ValueError("require at most 256 immutable travel options")
        ids = {p.id for p in self.places}
        if len(ids) != len(self.places) or self.origin in ids or self.min_stops > len(ids):
            raise ValueError("invalid or duplicate place identifiers")
        endpoints = ids | {self.origin}
        if len({t.id for t in self.travel}) != len(self.travel) or any(
                t.origin not in endpoints or t.destination not in endpoints for t in self.travel):
            raise ValueError("invalid or duplicate travel identifiers/endpoints")


def _scope(problem):
    return "trip-snapshot:" + content_digest(problem)


def _request(problem):
    data = json.loads(canonical_json(problem))
    del data["places"], data["travel"]
    return data


@dataclass(frozen=True, slots=True)
class TripTool:
    problem: TripProblem
    name: str = "fictional_trip_snapshot_reader"

    def collect(self, task: Task) -> tuple[Evidence, ...]:
        if task.domain != DOMAIN:
            raise ValueError(f"TripTool requires domain={DOMAIN!r}")
        return tuple(Evidence(identifier, self.problem.trip_id, metric, canonical_json(value), "json",
                              "fictional-declared-trip-snapshot", _scope(self.problem))
                     for identifier, metric, value in zip(EVIDENCE_IDS, ("request", "places", "transport"),
                                                          (_request(self.problem), self.problem.places, self.problem.travel)))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_claim_key")
        result[key] = value
    return result


def _parse(text):
    if type(text) is not str or len(text) > 65536:
        raise ValueError("claim_must_be_a_bounded_json_object")
    result = json.loads(text, object_pairs_hook=_unique,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non_json_constant")))
    if type(result) is not dict:
        raise ValueError("claim_must_be_a_json_object")
    return result


def _schema(payload):
    keys = {"stops", "legs", "total_cost_cents", "total_walking_minutes"}
    if set(payload) not in (keys, keys | {"rationale"}):
        raise ValueError("unexpected_claim_fields")
    if "rationale" in payload:
        _text(payload["rationale"], 4000)
    if type(payload["stops"]) is not list or not 1 <= len(payload["stops"]) <= 12:
        raise ValueError("invalid_stops")
    if type(payload["legs"]) is not list or len(payload["legs"]) != len(payload["stops"]) + 1:
        raise ValueError("require_one_leg_per_stop_plus_return")
    for stop in payload["stops"]:
        if type(stop) is not dict or set(stop) != {"place_id", "start_minute", "end_minute"}:
            raise ValueError("invalid_stop_fields")
        _identifier(stop["place_id"])
        _integer(stop["start_minute"], 1440)
        _integer(stop["end_minute"], 1440)
    for leg in payload["legs"]:
        if type(leg) is not dict or set(leg) != {"route_id", "depart_minute"}:
            raise ValueError("invalid_leg_fields")
        _identifier(leg["route_id"])
        _integer(leg["depart_minute"], 1440)
    _integer(payload["total_cost_cents"])
    _integer(payload["total_walking_minutes"], 1440)


def _check(problem, payload):
    """Return recomputed public data, all violations, and missing selected data."""
    _schema(payload)
    stops, legs = payload["stops"], payload["legs"]
    places, travel = {p.id: p for p in problem.places}, {r.id: r for r in problem.travel}
    errors, unknowns = [], []
    if len(stops) < problem.min_stops:
        errors.append("minimum_stops_not_met")
    if len({s["place_id"] for s in stops}) != len(stops):
        errors.append("duplicate_stops")
    if any(s["place_id"] not in places for s in stops):
        return None, tuple(errors + ["unknown_place"]), ()
    if any(l["route_id"] not in travel for l in legs):
        return None, tuple(errors + ["unknown_route"]), ()
    categories = set()
    cost, walking = 0, 0
    for stop in stops:
        place = places[stop["place_id"]]
        categories.update(place.categories)
        cost += place.cost_cents
        if stop["end_minute"] - stop["start_minute"] < place.min_visit_minutes:
            errors.append("visit_too_short:" + place.id)
        if stop["start_minute"] < place.opens_minute or stop["end_minute"] > place.closes_minute:
            errors.append("outside_opening_window:" + place.id)
        if problem.rain and not place.indoor:
            errors.append("rain_requires_indoor:" + place.id)
    if not set(problem.required_categories) <= categories:
        errors.append("required_categories_missing")
    nodes = [problem.origin] + [s["place_id"] for s in stops] + [problem.origin]
    checked_legs = []
    return_minute = None
    for index, leg in enumerate(legs):
        route = travel[leg["route_id"]]
        if (route.origin, route.destination) != (nodes[index], nodes[index + 1]):
            errors.append("route_endpoint_mismatch:" + route.id)
        earliest = problem.day_start_minute if index == 0 else stops[index - 1]["end_minute"]
        if leg["depart_minute"] < earliest:
            errors.append("departure_before_ready:" + route.id)
        latest = problem.day_end_minute if index == len(stops) else stops[index]["start_minute"]
        if leg["depart_minute"] > latest:
            errors.append("departure_after_deadline:" + route.id)
        if route.minutes is None or route.cost_cents is None or route.walking_minutes is None:
            unknowns.append("missing_travel_data:" + route.id)
        arrival = None if route.minutes is None else leg["depart_minute"] + route.minutes
        if arrival is not None and arrival > latest:
            errors.append("arrival_after_deadline:" + route.id)
        if route.cost_cents is not None:
            cost += route.cost_cents
        if route.walking_minutes is not None:
            walking += route.walking_minutes
        checked_legs.append({"route_id": route.id, "origin": route.origin, "destination": route.destination,
                             "mode": route.mode, "depart_minute": leg["depart_minute"], "arrive_minute": arrival,
                             "cost_cents": route.cost_cents, "walking_minutes": route.walking_minutes})
        if index == len(stops):
            return_minute = arrival
    if cost > problem.budget_cents:
        errors.append("budget_exceeded")
    if walking > problem.max_walking_minutes:
        errors.append("walking_limit_exceeded")
    if not unknowns:
        if payload["total_cost_cents"] != cost:
            errors.append("cost_total_mismatch")
        if payload["total_walking_minutes"] != walking:
            errors.append("walking_total_mismatch")
    summary = {"trip_id": problem.trip_id, "stops": stops, "legs": checked_legs,
               "total_cost_cents": cost, "total_walking_minutes": walking,
               "return_minute": return_minute, "currency": "CNY"}
    return summary, tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(unknowns))


def _fact(problem, summary):
    return Fact(PLAN_FACT, problem.trip_id, "verified_itinerary", canonical_json(summary), "json", EVIDENCE_IDS, _scope(problem))


def _checked_state(problem, state):
    if len(state.facts) != 1 or state.facts[0].id != PLAN_FACT:
        return None
    try:
        stored = _parse(state.facts[0].value)
        payload = {"stops": stored["stops"], "legs": [
            {"route_id": leg["route_id"], "depart_minute": leg["depart_minute"]} for leg in stored["legs"]],
            "total_cost_cents": stored["total_cost_cents"], "total_walking_minutes": stored["total_walking_minutes"]}
        checked, errors, unknowns = _check(problem, payload)
        if errors or unknowns or canonical_json(state.facts[0]) != canonical_json(_fact(problem, checked)):
            return None
        return checked
    except (ValueError, TypeError, KeyError, RecursionError):
        return None


@dataclass(frozen=True, slots=True)
class PlanningDomain:
    problem: TripProblem

    def rebuild(self, state: State) -> Residual:
        if _checked_state(self.problem, state) is not None:
            return Residual()
        return Residual(goals=(TARGET,), unknowns=("trip:transport_grounding",),
                        hard_constraints=("trip:feasibility",) + (("trip:unverified_state_facts",) if state.facts else ()))


@dataclass(frozen=True, slots=True)
class PlanningVerifier:
    problem: TripProblem

    def verify(self, candidate: Candidate, state: State, residual: Residual, evidence: tuple[Evidence, ...]) -> Verdict:
        def verdict(decision, reasons, facts=()):
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence, reasons=tuple(reasons), facts=facts)
        if candidate.action != ACTION or candidate.target != TARGET or TARGET not in residual.pending:
            return verdict(Decision.REJECT, ("unsupported_action_or_target",))
        expected = TripTool(self.problem).collect(Task("snapshot-check", "check", DOMAIN))
        if len(evidence) != len(expected) or {e.id: canonical_json(e) for e in evidence} != {e.id: canonical_json(e) for e in expected}:
            return verdict(Decision.REJECT, ("declared_snapshot_mismatch",))
        if set(candidate.refs) != set(EVIDENCE_IDS):
            return verdict(Decision.REJECT, ("missing_or_unknown_snapshot_refs",))
        if state.facts:
            return verdict(Decision.REJECT, ("unverified_state_facts",))
        try:
            checked, errors, unknowns = _check(self.problem, _parse(candidate.claim))
        except (ValueError, TypeError, KeyError, RecursionError):
            return verdict(Decision.REJECT, ("invalid_itinerary_schema",))
        if errors:
            return verdict(Decision.REJECT, errors + unknowns)
        if unknowns:
            return verdict(Decision.DEFER, unknowns)
        return verdict(Decision.ACCEPT, ("itinerary_constraints_verified",), (_fact(self.problem, checked),))


def demo_problem(scenario: str = "day-out") -> TripProblem:
    """All places, routes, costs, opening times, and weather are fictional inputs."""
    places = (
        Place("gallery", "Paper Lantern Gallery", ("art",), 600, 960, 60, 2500, True),
        Place("workshop", "Clay Room Workshop", ("art",), 660, 1020, 90, 4000, True),
        Place("noodles", "Little Bowl Noodles", ("meal",), 660, 840, 45, 3000, True),
        Place("cafe", "Quiet Corner Cafe", ("meal", "coffee"), 600, 1020, 40, 4500, True),
        Place("garden", "Moon Gate Garden", ("nature",), 540, 1020, 60, 0, False),
        Place("library", "Maple Reading Room", ("reading",), 570, 1020, 45, 0, True),
    )
    # Explicit bidirectional route options; no geometric or real-world inference.
    links = (("station", "gallery", 20, 10, 300, 2), ("station", "workshop", 35, 15, 300, 4),
             ("station", "noodles", 25, 12, 300, 3), ("station", "cafe", 15, 8, 300, 2),
             ("station", "garden", 10, 8, 300, 2), ("station", "library", 12, 8, 300, 2),
             ("gallery", "noodles", 10, 6, 200, 2), ("gallery", "library", 8, 6, 200, 2),
             ("noodles", "library", 8, 6, 200, 2), ("workshop", "cafe", 10, 7, 200, 2),
             ("workshop", "library", 15, 8, 200, 2), ("cafe", "library", 10, 6, 200, 2),
             ("garden", "cafe", 12, 8, 200, 2))
    travel = tuple(TravelOption(f"{a}:{b}:{mode}", a, b, mode, minutes, cost, walking)
                   for left, right, walk, transit, fare, access in links
                   for a, b in ((left, right), (right, left))
                   for mode, minutes, cost, walking in (("walk", walk, 0, walk), ("transit", transit, fare, access)))
    problem = TripProblem("fictional-day-out", "A relaxed day with art and a meal; coffee and somewhere quiet would be nice.",
                          "station", 540, 1080, 30000, 45, 3, ("art", "meal"), False, places, travel)
    if scenario == "day-out":
        return problem
    if scenario == "rain":
        return replace(problem, rain=True)
    if scenario == "missing-travel":
        return replace(problem, travel=tuple(replace(r, minutes=None, cost_cents=None, walking_minutes=None)
                                              if r.destination == problem.origin else r for r in travel))
    raise ValueError("scenario must be day-out, rain, or missing-travel")


def _offline_completion(problem):
    """Scripted proposals, repaired only after receiving actual verifier feedback."""
    attempt, corrected = 0, False

    def complete(prompt):
        nonlocal attempt, corrected
        data = json.loads(prompt)
        feedback = data["last_feedback"]
        if feedback and feedback["decision"] == "defer":
            return "null"
        if feedback and feedback["decision"] == "reject" and any(
                r == "walking_limit_exceeded" or r.startswith("rain_requires_indoor:") for r in feedback["reasons"]):
            corrected = True
        attempt += 1
        if problem.rain and not corrected:
            payload = {"stops": [{"place_id": p, "start_minute": s, "end_minute": e}
                                 for p, s, e in (("garden", 600, 660), ("cafe", 675, 720), ("workshop", 740, 840))],
                       "legs": [{"route_id": r, "depart_minute": d} for r, d in (
                           ("station:garden:transit", 580), ("garden:cafe:transit", 660),
                           ("cafe:workshop:transit", 720), ("workshop:station:transit", 840))],
                       "total_cost_cents": 9500, "total_walking_minutes": 10}
        else:
            mode = "transit" if corrected else "walk"
            payload = {"stops": [{"place_id": p, "start_minute": s, "end_minute": e}
                                 for p, s, e in (("gallery", 600, 660), ("noodles", 690, 735), ("library", 750, 810))],
                       "legs": [{"route_id": r, "depart_minute": d} for r, d in (
                           (f"station:gallery:{mode}", 580), ("gallery:noodles:walk", 660),
                           ("noodles:library:walk", 735), (f"library:station:{mode}", 810))],
                       "total_cost_cents": 6100 if corrected else 5500, "total_walking_minutes": 22 if corrected else 50}
        payload["rationale"] = "A pleasant creative day. This subjective sentence is intentionally not verified."
        return json.dumps({"id": f"itinerary:{attempt}", "action": ACTION, "target": TARGET,
                           "claim": json.dumps(payload), "refs": list(EVIDENCE_IDS)})
    return complete


def build_agent(problem: TripProblem, complete: Callable[[str], str] | None = None) -> Agent:
    if type(problem) is not TripProblem or (complete is not None and not callable(complete)):
        raise ValueError("require a TripProblem and an optional completion callable")

    def proposers(task, routes, evidence):
        instruction = task.instruction + (
            "\nPlan freely using this FICTIONAL snapshot: many different itineraries are valid; there is no expected "
            "route or optimality score. The brief is soft preference guidance, not a checked guarantee. "
            "Choose places, order, visit times, departures and declared walk/transit routes. Every evidence value "
            "is a JSON-encoded snapshot. Use only listed IDs. Times are integer minutes after local midnight; "
            "money is integer CNY cents. Costs cover one person, with each place cost and each route fare once. "
            "Hard rules: at least min_stops DISTINCT places; include all required_categories; each visit lasts "
            "at least min_visit_minutes within its opening window. Start from origin no earlier than "
            "day_start_minute and return there by day_end_minute. Include every travel leg, also the final return. "
            "A leg departs after the previous visit ends (or day start), arrives before the next visit begins "
            "(or day end), and uses a route whose endpoints match. Waiting is allowed. Sum every place cost and "
            "every route fare; sum route walking_minutes including transit access. Respect budget_cents and "
            "max_walking_minutes. If rain=true all VISITED PLACES must be indoor; transit and walking remain "
            "allowed in this declared rule. A null travel value is unknown: do not invent it; choose a known "
            "alternative or leave the task unresolved. Do not book or message anyone. "
            f"Use action={ACTION}, target={TARGET}, refs={list(EVIDENCE_IDS)}. "
            "claim must be a JSON object encoded as a STRING: {stops:[{place_id,start_minute,end_minute}],"
            "legs:[{route_id,depart_minute}],total_cost_cents,total_walking_minutes,rationale?}. "
            "legs has stops.length+1 elements. No other fields; optional rationale is untrusted text, never a fact. "
            "Recompute totals, repair all reject feedback and submit a whole corrected plan. If no grounded "
            "proposal is possible return null. Never copy solved or accepted flags into a candidate."
        )
        return (ModelProposer(complete if complete is not None else _offline_completion(problem),
                              allowed_actions=(ACTION,), evidence=evidence, instruction=instruction),)
    return Agent(domain_factory=lambda task: PlanningDomain(problem), verifier_factory=lambda task: PlanningVerifier(problem),
                 proposer_factory=proposers, tools=(TripTool(problem),), max_steps=8, max_no_progress=3)


def run_demo(scenario: str = "day-out") -> AgentResult:
    return build_agent(demo_problem(scenario)).run(Task(
        "open-planning-demo", "Suggest a feasible creative day out under the declared constraints.", DOMAIN))


def verified_plan(result: AgentResult) -> dict:
    """Recheck a complete plan and its snapshot before returning structured data.

    This is an integrity boundary inside trusted Python code, not a signature,
    authentication of the supplied snapshot, or a real-world travel guarantee.
    """
    error = "open planning itinerary is not verified"
    if type(result) is not AgentResult or result.task.domain != DOMAIN:
        raise ValueError(error)
    run = result.run_result
    if run.status != "solved" or not run.residual.solved:
        raise ValueError(error)
    try:
        values = {e.id: e.value for e in run.evidence}
        request = _parse(values[EVIDENCE_IDS[0]])
        request["required_categories"] = tuple(request["required_categories"])
        places = json.loads(values[EVIDENCE_IDS[1]], object_pairs_hook=_unique)
        travel = json.loads(values[EVIDENCE_IDS[2]], object_pairs_hook=_unique)
        problem = TripProblem(**request, places=tuple(Place(**dict(p, categories=tuple(p["categories"]))) for p in places),
                              travel=tuple(TravelOption(**r) for r in travel))
        expected = TripTool(problem).collect(result.task)
        if len(run.evidence) != len(expected) or {e.id: canonical_json(e) for e in run.evidence} != {e.id: canonical_json(e) for e in expected}:
            raise ValueError(error)
        checked = _checked_state(problem, run.state)
        if checked is None or not PlanningDomain(problem).rebuild(run.state).solved:
            raise ValueError(error)
        return checked
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ValueError(error) from exc
