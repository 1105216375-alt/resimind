"""Finite schedule proposals checked against the complete local contract.

No benchmark labels, scoring or model client are imported here. Restaurant
choices may change; attractions, intercity IDs, task metadata and user-named
entities remain obligations. This is a development adapter, not a complete
natural-language travel solver.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from time import monotonic

from resimind.repair import plan_sha256
from resimind.search import SearchLimits, bounded_search
from experiments.chinatravel_repair.binding import OfficialTools, _minute, _native
from experiments.chinatravel_repair.checker import LocalContract
from .preflight import preflight_translation
from .schedule import construct_schedule


def activities(plan):
    return [a for day in plan.get("itinerary", []) for a in day.get("activities", [])]


def normalize_seed(plan, public_text, *, optional_visit_digests=()):
    """Propose an explicit reordering; omit only optional zero-room hotel visits.

    A daytime visit is removable only with an application-owned exact allowlist,
    if the same hotel has a positive overnight booking elsewhere and is not
    named by the user. Lack of a name alone does not establish optionality.
    It is recorded
    as an omitted model suggestion, never as fulfilled luggage storage.
    """
    draft, changes = deepcopy(plan), []
    occurrences = Counter(plan_sha256(a) for a in activities(draft))
    overnight = {a.get("position") for a in activities(draft)
                 if a.get("type") == "accommodation" and a.get("end_time") == "24:00"
                 and type(a.get("rooms")) is int and a["rooms"] > 0}
    for d, day in enumerate(draft["itinerary"]):
        kept = []
        for i, activity in enumerate(day["activities"]):
            name = activity.get("position", "")
            if (activity.get("type") == "accommodation" and activity.get("rooms") == 0
                    and type(activity.get("rooms")) is int and name in overnight
                    and name not in public_text and activity.get("end_time") != "24:00"
                    and plan_sha256(activity) in optional_visit_digests
                    and occurrences[plan_sha256(activity)] == 1):
                changes.append({"kind": "omit_optional_zero_room_visit",
                                "path": f"/itinerary/{d}/activities/{i}", "activity": activity})
            else:
                kept.append(activity)
        # Sorting is a proposal, not a claimed correction. Full time, location,
        # booking, task and self-DSL checks must still pass afterward.
        ordered = sorted(kept, key=lambda a: _minute(a.get("start_time"))
                         if _minute(a.get("start_time")) is not None else 1441)
        if kept != ordered:
            changes.append({"kind": "order_by_proposed_start_time", "day": d})
        day["activities"] = ordered
    return draft, changes


def _all(values):
    values = list(values)
    return False if False in values else None if not values or None in values else True


def _identity(activity):
    kind = activity.get("type")
    if kind in ("train", "airplane"):
        return kind, activity.get("TrainID" if kind == "train" else "FlightID")
    return kind, activity.get("position")


class WholePlanVerifier:
    """Stable obligation groups; rerun every detailed check after topology edits."""

    def __init__(self, initial, local, public_text):
        self.local, self.assessments = local, {}
        self.fixed = Counter(_identity(a) for a in activities(initial)
                             if a.get("type") in ("attraction", "train", "airplane"))
        self.named = {_identity(a) for a in activities(initial)
                      if a.get("position") and a["position"] in public_text}
        self.hotels = {a.get("position") for a in activities(initial)
                       if a.get("type") == "accommodation"}

    def __call__(self, plan):
        details = self.local(plan)
        checks = {key: value for key, value in details.items()
                  if not key.startswith(("binding/", "coverage/"))}
        for group in ("binding", "coverage"):
            checks[group] = _all(v for k, v in details.items() if k.startswith(group + "/"))
        acts = activities(plan)
        checks["retained_attractions_and_intercity"] = self.fixed == Counter(
            _identity(a) for a in acts if a.get("type") in ("attraction", "train", "airplane"))
        checks["retained_user_named_entities"] = self.named <= {_identity(a) for a in acts}
        checks["retained_hotel_choices"] = self.hotels == {
            a.get("position") for a in acts if a.get("type") == "accommodation"}
        days = plan.get("itinerary", [])
        checks["explicit_overnights"] = bool(days) and all(
            day.get("activities") and day["activities"][-1].get("type") == "accommodation"
            and day["activities"][-1].get("end_time") == "24:00" for day in days[:-1])
        checks["first_intercity_empty_route"] = bool(acts) and acts[0].get("type") in ("train", "airplane") and acts[0].get("transports") == []
        self.assessments[plan_sha256(plan)] = {"details": deepcopy(details),
                                              "diagnostics": deepcopy(self.local.last_diagnostics)}
        return checks


def restaurant_alternatives(plan, translation, public_text, tools, *, limit=4):
    """Offer nearby open restaurants for a meal that misses its opening window.

    The finite catalog is evidence, not an instruction. No attractions or
    user-named restaurants are removed. The constructor will bind each proposed
    replacement and recalculate the incoming and outgoing routes.
    """
    city = translation["target_city"]
    for d, day in enumerate(plan["itinerary"]):
        for i, activity in enumerate(day["activities"]):
            if activity.get("type") not in ("lunch", "dinner"):
                continue
            name = activity.get("position", "")
            if name in public_text:
                continue
            current = tools.lookup("restaurant", city, name)
            if len(current) != 1:
                continue
            row = current[0]
            meal_start, meal_end = (660, 840) if activity["type"] == "lunch" else (1020, 1200)
            start, end = _minute(activity.get("start_time")), _minute(activity.get("end_time"))
            opens, closes = _minute(row.get("opentime")), _minute(row.get("endtime"))
            if None in (start, end, opens, closes):
                continue
            if opens <= start < end <= closes and meal_start <= start < end <= meal_end:
                continue
            duration = max(30, end - start)
            rows = _native(tools.tables["restaurant"].select(city, "name", lambda _: True).to_dict("records"))
            eligible = []
            for alternative in rows:
                lo, hi = _minute(alternative.get("opentime")), _minute(alternative.get("endtime"))
                if (alternative["name"] != name and lo is not None and hi is not None
                        and max(lo, meal_start) + duration <= min(hi, meal_end)):
                    eligible.append(alternative)
            eligible.sort(key=lambda r: ((r["lat"] - row["lat"]) ** 2 +
                                         (r["lon"] - row["lon"]) ** 2, r["price"], r["name"]))
            for alternative in eligible[:limit]:
                changed = deepcopy(plan)
                changed["itinerary"][d]["activities"][i]["position"] = alternative["name"]
                yield changed, {"kind": "restaurant_reselection", "day": d, "activity": i,
                                "before": name, "after": alternative["name"], "catalog_row": alternative}


def search_plan(plan, translation, upstream: Path, *, public_query, tools=None,
                limits=SearchLimits(max_expansions=16, max_generated=96, max_depth=4,
                                    max_frontier=32), seconds=90, optional_visit_paths=()):
    """Search seen candidate alternatives without weakening the original DSL."""
    if type(seconds) not in (int, float) or not 0 < seconds <= 120:
        raise ValueError("seconds must be between zero and 120")
    preflight = preflight_translation(translation)
    if preflight["needs_retranslation"]:
        return {"status": "deferred", "reason": "needs_retranslation", "plan": None,
                "best_draft": deepcopy(plan), "preflight": preflight,
                "generated": 0, "evaluated": 0, "expansions": 0, "model_api_calls": 0}
    tools = tools or OfficialTools()
    public_text = public_query["nature_language"]
    optional_visits = set()
    occurrences = Counter(plan_sha256(a) for a in activities(plan))
    for path in optional_visit_paths:
        if (type(path) not in (list, tuple) or len(path) != 2
                or any(type(n) is not int or n < 0 for n in path)):
            raise ValueError("optional visits must be explicit [day_index, activity_index] pairs")
        digest = plan_sha256(plan["itinerary"][path[0]]["activities"][path[1]])
        if occurrences[digest] != 1:
            raise ValueError("ambiguous duplicate optional visit; preserve it until explicitly disambiguated")
        optional_visits.add(digest)
    local = LocalContract(translation, upstream, tools=tools, public_query=public_query)
    verifier = WholePlanVerifier(plan, local, public_text)
    proposals, evidence = [], {}
    start = monotonic()

    def expand(draft, checks):
        normalized, changes = normalize_seed(draft, public_text, optional_visit_digests=optional_visits)
        if changes:
            proposals.append({"input_sha256": plan_sha256(draft), "changes": changes})
            yield normalized
        for policy in ("fastest", "preserve", "cheapest"):
            constructed = construct_schedule(normalized, translation, tools=tools,
                                             policy=policy, preserve_durations=True)
            evidence.update(constructed.evidence)
            proposals.append({"input_sha256": plan_sha256(normalized), "policy": policy,
                              "reasons": list(constructed.reasons), "stats": constructed.stats})
            if constructed.plan is not None:
                yield constructed.plan
        for candidate, change in restaurant_alternatives(normalized, translation, public_text, tools):
            proposals.append({"input_sha256": plan_sha256(normalized), "changes": [change]})
            yield candidate

    result = bounded_search(plan, expand=expand, verifier=verifier, limits=limits,
                            deadline=start + seconds)
    output = asdict(result)
    final = result.plan if result.accepted else result.best_draft
    assessment = verifier.assessments.get(plan_sha256(final), {"details": {}, "diagnostics": {}})
    output.update(preflight=preflight, proposals=proposals, evidence=evidence,
                  detailed_checks=assessment["details"], diagnostics=assessment["diagnostics"],
                  seconds=monotonic() - start, model_api_calls=0,
                  optional_visit_paths=list(optional_visit_paths),
                  scope="self_translated_contract_and_sandbox_only")
    return output
