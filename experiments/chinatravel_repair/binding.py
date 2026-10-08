"""Conservative, oracle-free data binding for the ChinaTravel sandbox.

These are untrusted *proposals*. The caller must transactionally recheck the
whole plan and every user constraint. This module never imports gold labels or
an evaluator. It preserves activity order, choices, IDs and planned times.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from resimind.repair import Edit, RepairProposal, plan_sha256


@dataclass(frozen=True)
class BindingReport:
    proposals: tuple[RepairProposal, ...]
    evidence: dict[str, dict]
    unresolved: tuple[dict, ...]
    proposal_kinds: tuple[str, ...] = ()


def _native(value):
    if isinstance(value, dict):
        return {str(k): _native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(v) for v in value]
    if hasattr(value, "item"):
        return _native(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class OfficialTools:
    """Direct exact queries to unchanged official tool classes, loaded lazily."""

    def __init__(self, lang="zh"):
        from chinatravel.environment.tools.attractions.apis import Attractions
        from chinatravel.environment.tools.accommodations.apis import Accommodations
        from chinatravel.environment.tools.restaurants.apis import Restaurants
        from chinatravel.environment.tools.intercity_transport.apis import IntercityTransport
        from chinatravel.environment.tools.transportation.apis import Transportation
        self.tables = {"attraction": Attractions(lang=lang),
                       "accommodation": Accommodations(lang=lang),
                       "restaurant": Restaurants(lang=lang)}
        self.intercity_tool = IntercityTransport(lang=lang)
        self.transport_tool = Transportation(lang=lang)
        self._cache: dict[str, Any] = {}

    def lookup(self, kind, city, name):
        frame = self.tables[kind].select(city, "name", lambda x: x == name)
        return _native(frame.to_dict("records"))

    def intercity(self, start_city, end_city, kind, identifier):
        frame = self.intercity_tool.select(start_city, end_city, kind)
        key = "TrainID" if kind == "train" else "FlightID"
        if frame is None or not hasattr(frame, "columns") or key not in frame.columns:
            return []
        return _native(frame[frame[key] == identifier].to_dict("records"))

    def poi(self, city, name):
        # search() is exact; the tool's error is a string, coordinates a pair.
        result = self.transport_tool.poi_search.search(city, name)
        return isinstance(result, (list, tuple)) and len(result) == 2

    def route(self, city, start, end, start_time, mode):
        key = json.dumps([city, start, end, start_time, mode], ensure_ascii=False)
        if key not in self._cache:
            self._cache[key] = _native(self.transport_tool.goto(
                city, start, end, start_time, mode, verbose=False))
        return deepcopy(self._cache[key])


def _positive_int(value):
    return type(value) is int and value > 0


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _minute(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return None
    try:
        hour, minute = map(int, value.split(":"))
    except ValueError:
        return None
    return hour * 60 + minute if 0 <= hour <= 24 and 0 <= minute < 60 and (hour < 24 or minute == 0) else None


def _clock(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def _record(evidence, operation, arguments, result, *, source="official_sandbox_tool"):
    payload = {"source": source, "operation": operation,
               "arguments": arguments, "result": _native(result)}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    key = "binding_" + hashlib.sha256(raw.encode()).hexdigest()
    evidence[key] = payload
    return key


def _activities(plan):
    days = plan.get("itinerary")
    if not isinstance(days, list) or not days or len(days) > 31:
        raise ValueError("invalid_or_excessive_itinerary")
    found = []
    for d, day in enumerate(days):
        acts = day.get("activities") if isinstance(day, dict) else None
        if not isinstance(acts, list) or len(acts) > 64:
            raise ValueError("invalid_or_excessive_activities")
        for a, activity in enumerate(acts):
            if not isinstance(activity, dict):
                raise ValueError("activity_not_object")
            found.append((d, a, f"/itinerary/{d}/activities/{a}", activity))
    if len(found) > 128:
        raise ValueError("activity_limit")
    return found


def _inspect(plan, translation, tools):
    evidence: dict[str, dict] = {}
    unresolved: list[dict] = []
    groups: dict[str, list[Edit]] = {"activity_data": [], "intercity_data": [], "transport_routes": []}
    checks: dict[str, bool | None] = {}

    def issue(path, reason, **detail):
        unresolved.append({"path": path, "reason": reason, **detail})

    def edit(group, path, before, after, refs):
        if before != after:
            groups[group].append(Edit(path, deepcopy(before), deepcopy(after), tuple(refs)))

    if not isinstance(plan, dict) or not isinstance(translation, dict):
        return groups, evidence, [{"path": "", "reason": "plan_or_translation_not_object"}], {"binding:metadata": None}
    # Never repair metadata to what might be a mistranslation.
    meta = all(plan.get(k) == translation.get(k) for k in ("people_number", "start_city", "target_city"))
    meta = meta and _positive_int(translation.get("people_number")) and all(
        isinstance(translation.get(k), str) and translation[k] for k in ("start_city", "target_city"))
    checks["binding:metadata"] = bool(meta)
    if not meta:
        return groups, evidence, [{"path": "", "reason": "translation_metadata_mismatch_or_invalid"}], checks
    people, city = translation["people_number"], translation["target_city"]
    metadata_ref = _record(evidence, "metadata", {}, {k: translation[k] for k in ("people_number", "start_city", "target_city")}, source="caller_self_translated_contract")
    try:
        activities = _activities(plan)
    except ValueError as exc:
        return groups, evidence, [{"path": "", "reason": str(exc)}], {**checks, "binding:topology": None}
    checks["binding:topology"] = True
    previous = None
    for index, (d, a, path, activity) in enumerate(activities):
        kind = activity.get("type")
        money_key, route_key = f"binding:data:{path}", f"binding:route:{path}"
        checks[money_key] = None
        checks[route_key] = None
        replacement = deepcopy(activity)
        refs = [metadata_ref]
        try:
            if kind in ("train", "airplane"):
                if index not in (0, len(activities) - 1):
                    raise ValueError("intercity_not_boundary")
                id_key = "TrainID" if kind == "train" else "FlightID"
                identifier = activity.get(id_key)
                if not isinstance(identifier, str) or not identifier:
                    raise ValueError("missing_exact_transport_identifier")
                origin, destination = (translation["start_city"], city) if index == 0 else (city, translation["start_city"])
                rows = tools.intercity(origin, destination, kind, identifier)
                refs.append(_record(evidence, "intercity", {"start_city": origin, "end_city": destination,
                                                           "kind": kind, "identifier": identifier}, rows))
                if len(rows) != 1:
                    raise ValueError("unknown_entity" if not rows else "ambiguous_entity")
                row = rows[0]
                if not _number(row.get("Cost")) or any(not isinstance(row.get(k), str) or not row[k] for k in ("From", "To", "BeginTime", "EndTime")):
                    raise ValueError("invalid_intercity_row")
                tickets = activity.get("tickets", people)
                if not _positive_int(tickets):
                    raise ValueError("invalid_ticket_quantity")
                replacement.update(start=row["From"], end=row["To"], start_time=row["BeginTime"],
                                   end_time=row["EndTime"], price=row["Cost"], tickets=tickets,
                                   cost=round(row["Cost"] * tickets, 2))
                # Identifiers never change; exact ID is the prerequisite for binding.
                checks[money_key] = replacement == activity
                edit("intercity_data", path, activity, replacement, refs)
            elif kind in ("attraction", "accommodation", "breakfast", "lunch", "dinner"):
                name = activity.get("position")
                if not isinstance(name, str) or not name:
                    raise ValueError("missing_exact_entity_name")
                table = kind if kind in ("attraction", "accommodation") else "restaurant"
                rows = tools.lookup(table, city, name)
                refs.append(_record(evidence, "lookup", {"kind": table, "city": city, "name": name}, rows))
                hotel_breakfast = False
                if not rows and kind == "breakfast":
                    rows = tools.lookup("accommodation", city, name)
                    refs.append(_record(evidence, "lookup", {"kind": "accommodation", "city": city, "name": name}, rows))
                    hotel_breakfast = True
                if len(rows) != 1:
                    raise ValueError("unknown_entity" if not rows else "ambiguous_entity")
                row = rows[0]
                price = 0 if hotel_breakfast else row.get("price")
                if not _number(price):
                    raise ValueError("invalid_price_in_tool")
                replacement["price"] = price
                if kind == "accommodation":
                    if not _positive_int(row.get("numbed")):
                        raise ValueError("invalid_room_type_in_tool")
                    if not _positive_int(activity.get("rooms")):
                        raise ValueError("room_quantity_requires_intent")
                    replacement["room_type"] = row["numbed"]
                    quantity = activity["rooms"]
                elif kind == "attraction":
                    quantity = activity.get("tickets", people)
                    if not _positive_int(quantity):
                        raise ValueError("invalid_ticket_quantity")
                    replacement["tickets"] = quantity
                else:
                    quantity = people
                replacement["cost"] = round(price * quantity, 2)
                checks[money_key] = replacement == activity
                edit("activity_data", path, activity, replacement, refs)
            else:
                raise ValueError("unsupported_activity_type")
        except Exception as exc:
            issue(path, str(exc) if isinstance(exc, ValueError) else "entity_tool_exception", exception=type(exc).__name__)

        # A cross-day source requires an explicit overnight accommodation.
        # The previous evening's last POI is not evidence of a morning origin.
        # We do not invent an unrecorded return to a hotel or an overnight stay.
        try:
            existing = activity.get("transports")
            if not isinstance(existing, list) or len(existing) > 3:
                raise ValueError("unsupported_transport_shape")
            if previous is None:
                checks[route_key] = not existing
                if existing:
                    issue(path + "/transports", "first_activity_transport_not_repaired")
            else:
                pd, _, _, prev = previous
                if pd != d and (prev.get("type") != "accommodation"
                                or prev.get("end_time") != "24:00"):
                    raise ValueError("overnight_position_unverified")
                source = prev.get("end") if prev.get("type") in ("train", "airplane") else prev.get("position")
                target = activity.get("start") if kind in ("train", "airplane") else activity.get("position")
                if not isinstance(source, str) or not isinstance(target, str):
                    raise ValueError("missing_adjacent_entity")
                if source == target:
                    checks[route_key] = not existing
                    if existing:
                        issue(path + "/transports", "same_position_transport_requires_intent")
                else:
                    if not existing:
                        raise ValueError("missing_route_mode_requires_intent")
                    modes = [leg.get("mode") for leg in existing if isinstance(leg, dict)]
                    if modes == ["walk", "metro", "walk"] or modes == ["metro"]:
                        mode = "metro"
                    elif modes in (["walk"], ["taxi"]):
                        mode = modes[0]
                    else:
                        raise ValueError("ambiguous_route_mode")
                    if not tools.poi(city, source) or not tools.poi(city, target):
                        raise ValueError("unknown_route_endpoint")
                    start = _minute(existing[0].get("start_time"))
                    deadline = _minute(activity.get("start_time"))
                    previous_end = _minute(prev.get("end_time")) if pd == d else None
                    if start is None or deadline is None or (pd == d and previous_end is None):
                        raise ValueError("invalid_route_time")
                    if previous_end is not None:
                        start = max(start, previous_end)
                    if start >= 24 * 60:
                        raise ValueError("no_same_day_route_window")
                    raw = tools.route(city, source, target, _clock(start), mode)
                    # If an unnecessarily late departure does not fit, try the
                    # known preceding activity end; never move activity times.
                    if isinstance(raw, list) and raw and _minute(raw[-1].get("end_time")) is not None and _minute(raw[-1]["end_time"]) > deadline and previous_end is not None and previous_end < start:
                        start = previous_end
                        raw = tools.route(city, source, target, _clock(start), mode)
                    route_ref = _record(evidence, "route", {"city": city, "start": source, "end": target,
                                                           "start_time": _clock(start), "mode": mode}, raw)
                    if not isinstance(raw, list) or not raw or len(raw) > 3:
                        raise ValueError("invalid_route_response")
                    if raw[0].get("start") != source or raw[-1].get("end") != target:
                        raise ValueError("route_endpoint_mismatch")
                    expected_modes = ["walk", "metro", "walk"] if mode == "metro" else [mode]
                    if [leg.get("mode") for leg in raw] != expected_modes:
                        raise ValueError("route_mode_mismatch")
                    arrival = _minute(raw[-1].get("end_time"))
                    if arrival is None or arrival > deadline:
                        raise ValueError("route_does_not_fit_activity_window")
                    legs = []
                    for leg in raw:
                        if any(k not in leg for k in ("start", "end", "mode", "start_time", "end_time", "cost", "distance")) or not _number(leg["cost"]) or not _number(leg["distance"]):
                            raise ValueError("invalid_route_leg")
                        # Build only real official transport fields. Quantity is
                        # an existing plan decision, never a invented train ID.
                        bound = {k: deepcopy(leg[k]) for k in ("start", "end", "mode", "start_time", "end_time", "distance")}
                        bound["price"] = leg["cost"]
                        if leg["mode"] == "metro":
                            candidates = [v.get("tickets", people) for v in existing if v.get("mode") == "metro"]
                            quantity = candidates[0] if len(candidates) == 1 else people
                            if not _positive_int(quantity):
                                raise ValueError("invalid_metro_ticket_quantity")
                            bound["tickets"] = quantity
                        elif leg["mode"] == "taxi":
                            quantity = existing[0].get("cars")
                            if not _positive_int(quantity):
                                raise ValueError("taxi_car_quantity_requires_intent")
                            bound["cars"] = quantity
                        else:
                            quantity = 1
                        bound["cost"] = round(leg["cost"] * quantity, 2)
                        legs.append(bound)
                    # Unknown extra fields are preserved only when topology is
                    # identical, preventing accidental loss of user annotations.
                    if len(legs) == len(existing) and all(leg["mode"] == old.get("mode") for leg, old in zip(legs, existing)):
                        legs = [{**deepcopy(old), **leg} for old, leg in zip(existing, legs)]
                    checks[route_key] = legs == existing
                    edit("transport_routes", path + "/transports", existing, legs, (route_ref, metadata_ref))
        except Exception as exc:
            issue(path + "/transports", str(exc) if isinstance(exc, ValueError) else "route_tool_exception", exception=type(exc).__name__)
        previous = (d, a, path, activity)
    return groups, evidence, unresolved, checks


def propose_repairs(plan: dict, translation: dict, *, tools=None, max_patches=6) -> BindingReport:
    """Return category-level exact-data patches; this never commits a plan.

    Each proposal has the same base hash. Recompute after committing one patch.
    Up to 128 activities and 3 route legs/activity are inspected. Missing names,
    ambiguous rows, route preferences and room/car quantities remain unresolved.
    """
    if type(max_patches) is not int or not 0 <= max_patches <= 16:
        raise ValueError("max_patches must be an integer in [0, 16]")
    groups, evidence, unresolved, _ = _inspect(plan, translation, tools or OfficialTools())
    proposals, kinds = [], []
    for kind, edits in groups.items():
        if edits and len(proposals) < max_patches:
            proposals.append(RepairProposal(plan_sha256(plan), tuple(edits)))
            kinds.append(kind)
    return BindingReport(tuple(proposals), evidence, tuple(unresolved), tuple(kinds))


def binding_checks(plan: dict, translation: dict, *, tools=None) -> dict[str, bool | None]:
    """Per-activity exact-binding checks for a fixed-topology verifier.

    None means unavailable evidence or a change requiring user intent. Passing
    these checks alone does not establish world feasibility or task completion.
    """
    return _inspect(plan, translation, tools or OfficialTools())[3]
