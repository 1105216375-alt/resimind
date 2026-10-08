"""Fixed, blinded planning inputs for the first paired evaluation.

Only ``case.problem`` belongs in model prompts. Descriptions, expected outcomes,
and independently generated witnesses are evaluation metadata, never hints.
The catalogue is explicitly frozen here rather than inheriting future demo edits.
All names, routes, measurements, and constraints are fictional.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from resimind.domains.planning import Place, TravelOption, TripProblem


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    description: str
    problem: TripProblem
    expected_outcome: str


def _snapshot() -> TripProblem:
    places = (
        Place("gallery", "Paper Lantern Gallery", ("art",), 600, 960, 60, 2500, True),
        Place("workshop", "Clay Room Workshop", ("art",), 660, 1020, 90, 4000, True),
        Place("noodles", "Little Bowl Noodles", ("meal",), 660, 840, 45, 3000, True),
        Place("cafe", "Quiet Corner Cafe", ("meal", "coffee"), 600, 1020, 40, 4500, True),
        Place("garden", "Moon Gate Garden", ("nature",), 540, 1020, 60, 0, False),
        Place("library", "Maple Reading Room", ("reading",), 570, 1020, 45, 0, True),
    )
    links = (
        ("station", "gallery", 20, 10, 300, 2),
        ("station", "workshop", 35, 15, 300, 4),
        ("station", "noodles", 25, 12, 300, 3),
        ("station", "cafe", 15, 8, 300, 2),
        ("station", "garden", 10, 8, 300, 2),
        ("station", "library", 12, 8, 300, 2),
        ("gallery", "noodles", 10, 6, 200, 2),
        ("gallery", "library", 8, 6, 200, 2),
        ("noodles", "library", 8, 6, 200, 2),
        ("workshop", "cafe", 10, 7, 200, 2),
        ("workshop", "library", 15, 8, 200, 2),
        ("cafe", "library", 10, 6, 200, 2),
        ("garden", "cafe", 12, 8, 200, 2),
    )
    routes = tuple(
        TravelOption(f"{origin}:{destination}:{mode}", origin, destination,
                     mode, duration, fare, walking)
        for left, right, walk, transit, fare, access in links
        for origin, destination in ((left, right), (right, left))
        for mode, duration, fare, walking in (
            ("walk", walk, 0, walk), ("transit", transit, fare, access))
    )
    return TripProblem(
        "trip-01", "Plan a pleasant day using the supplied places and explicit requirements.",
        "station", 540, 1080, 30000, 45, 3, ("art", "meal"), False, places, routes)


def benchmark_cases() -> tuple[BenchmarkCase, ...]:
    """Return all twelve cases in their preregistered order, without sampling."""
    base = _snapshot()
    renames = dict(station="harbor", gallery="museum", workshop="studio",
                   noodles="tearoom", cafe="atrium", garden="promenade", library="archive")
    shifted = replace(
        base, origin="harbor", rain=True, required_categories=("art", "meal", "reading", "coffee"),
        places=tuple(replace(p, id=renames[p.id], name=renames[p.id].title(),
                             opens_minute=p.opens_minute + 30, closes_minute=p.closes_minute - 30,
                             cost_cents=p.cost_cents + 200) for p in base.places),
        travel=tuple(replace(r, id=f"{renames[r.origin]}:{renames[r.destination]}:{r.mode}",
                             origin=renames[r.origin], destination=renames[r.destination])
                     for r in base.travel))
    specifications = (
        (base, "Original catalogue with art, meal and three distinct stops.", "feasible"),
        (replace(base, rain=True), "Rain makes outdoor visits inadmissible.", "feasible"),
        (replace(base, required_categories=("art", "meal", "coffee")),
         "Coffee is an explicit additional hard requirement.", "feasible"),
        (replace(base, budget_cents=5800), "Tight budget still permits a mixed-mode itinerary.", "feasible"),
        (replace(base, max_walking_minutes=10), "Tight walking allowance includes transit access.", "feasible"),
        (replace(base, day_end_minute=770), "Return is required by 12:50.", "feasible"),
        (replace(base, min_stops=4, required_categories=("art", "meal", "reading", "coffee")),
         "Four distinct stops and four required categories.", "feasible"),
        (shifted, "Renamed catalogue, shifted opening windows, altered costs and indoor requirements.", "feasible"),
        (replace(base, budget_cents=2400), "All art venues alone cost more than the complete budget.", "infeasible"),
        (replace(base, day_end_minute=659), "No art visit can finish before the day ends.", "infeasible"),
        (replace(base, travel=tuple(replace(r, minutes=None, walking_minutes=None)
                                    if r.destination == base.origin else r for r in base.travel)),
         "Every return route lacks travel duration and walking measurements.", "missing_evidence"),
        (replace(base, travel=tuple(replace(r, cost_cents=None)
                                    if r.destination == base.origin else r for r in base.travel)),
         "Every return route lacks its fare; even an unmeasured walk fare must not be invented.", "missing_evidence"),
    )
    return tuple(BenchmarkCase(f"case-{index:02}", description,
                               replace(problem, trip_id=f"trip-{index:02}"), expected)
                 for index, (problem, description, expected) in enumerate(specifications, 1))
