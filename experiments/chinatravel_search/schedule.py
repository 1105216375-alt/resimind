"""Bounded, tool-grounded schedule proposals, without gold or model access.

This constructor preserves selected activities and their order (moving each
non-final day's hotel to the end). It changes times, routes and exact data, not
the user's contract. A returned proposal still requires the full verifier.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math

from experiments.chinatravel_repair.binding import OfficialTools


@dataclass(frozen=True)
class ConstructionResult:
    plan: dict | None
    evidence: dict
    reasons: tuple[dict, ...]
    stats: dict


class _Unresolved(Exception):
    def __init__(self, reason, **details):
        self.details = {"reason": reason, **details}
        super().__init__(reason)


def _minute(value):
    if (type(value) is not str or len(value) != 5 or value[2] != ":"
            or any(character not in "0123456789" for character in value[:2] + value[3:])):
        return None
    try:
        hour, minute = map(int, value.split(":"))
    except ValueError:
        return None
    if not 0 <= hour <= 24 or not 0 <= minute < 60 or (hour == 24 and minute):
        return None
    return hour * 60 + minute


def _clock(value):
    return f"{value // 60:02d}:{value % 60:02d}"


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _positive(value):
    return type(value) is int and value > 0


def _native(value):
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(item) for item in value]
    if hasattr(value, "item"):
        return _native(value.item())
    return value


class _BoundedTools:
    """Per-construction cache; no eviction makes accounting deterministic."""

    def __init__(self, tools, evidence, stats, route_limit, tool_limit, cache_limit):
        self.tools, self.evidence, self.stats = tools, evidence, stats
        self.route_limit, self.tool_limit, self.cache_limit = route_limit, tool_limit, cache_limit
        self.cache = {}

    def record(self, operation, arguments, result, source="official_sandbox_tool"):
        payload = {"source": source, "operation": operation, "arguments": arguments, "result": result}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(raw.encode()) > 262144:
            raise _Unresolved("tool_result_too_large", operation=operation)
        key = "schedule_" + hashlib.sha256(raw.encode()).hexdigest()
        self.evidence[key] = payload
        return key

    def call(self, operation, *args):
        key = (operation, *args)
        if key in self.cache:
            self.stats["cache_hits"] += 1
            return deepcopy(self.cache[key])
        if len(self.cache) >= self.cache_limit:
            raise _Unresolved("cache_budget_exhausted")
        if self.stats["tool_calls"] >= self.tool_limit:
            raise _Unresolved("tool_budget_exhausted")
        if operation == "route" and self.stats["route_calls"] >= self.route_limit:
            raise _Unresolved("route_budget_exhausted")
        self.stats["tool_calls"] += 1
        if operation == "route":
            self.stats["route_calls"] += 1
        try:
            result = _native(getattr(self.tools, operation)(*args))
            self.record(operation, list(args), result)
        except _Unresolved:
            raise
        except Exception as exc:
            self.record(operation, list(args), {"exception_class": type(exc).__name__})
            raise _Unresolved("tool_exception", operation=operation, exception_class=type(exc).__name__) from exc
        self.cache[key] = deepcopy(result)
        self.stats["cache_entries"] = len(self.cache)
        return deepcopy(result)


_ACTIVITY_TYPES = {"train", "airplane", "attraction", "accommodation", "breakfast", "lunch", "dinner"}
_MODES = ("walk", "metro", "taxi")
_MEAL_WINDOWS = {"breakfast": (360, 540), "lunch": (660, 840), "dinner": (1020, 1200)}


def _original_mode(activity):
    transports = activity.get("transports")
    if type(transports) is not list:
        return None
    modes = [leg.get("mode") for leg in transports if type(leg) is dict]
    if modes in (["walk", "metro", "walk"], ["metro"]):
        return "metro"
    if modes in (["walk"], ["taxi"]):
        return modes[0]
    return None


def _pure_transport(activity):
    if activity.get("type") not in {"transport", "transportation", *_MODES}:
        return False
    allowed = {"type", "start", "end", "mode", "start_time", "end_time", "cost", "price",
               "distance", "tickets", "cars", "transports"}
    if set(activity) - allowed:
        return False
    return (all(type(activity.get(key)) is str and activity[key] for key in ("start", "end"))
            and activity.get("mode", activity["type"]) in _MODES)


def _one(rows, key, expected):
    if type(rows) is not list:
        raise _Unresolved("invalid_entity_response")
    if len(rows) != 1:
        raise _Unresolved("unknown_entity" if not rows else "ambiguous_entity", entity=expected)
    row = rows[0]
    if type(row) is not dict or row.get(key) != expected:
        raise _Unresolved("entity_identifier_mismatch", entity=expected)
    return row


def _quantity(activity, key, minimum):
    value = activity.get(key, minimum)
    if not _positive(value):
        raise _Unresolved("invalid_quantity", field=key)
    return max(value, minimum)


def _bind(activity, city, origin_city, people, outward, broker):
    kind = activity["type"]
    result = deepcopy(activity)
    if kind in ("train", "airplane"):
        key = "TrainID" if kind == "train" else "FlightID"
        identifier = activity.get(key)
        if type(identifier) is not str or not identifier:
            raise _Unresolved("missing_exact_transport_identifier")
        origin, target = (origin_city, city) if outward else (city, origin_city)
        row = _one(broker.call("intercity", origin, target, kind, identifier), key, identifier)
        begin, end = _minute(row.get("BeginTime")), _minute(row.get("EndTime"))
        if (begin is None or end is None or end <= begin or not _number(row.get("Cost"))
                or any(type(row.get(key)) is not str or not row[key] for key in ("From", "To"))):
            raise _Unresolved("invalid_or_overnight_intercity_row")
        quantity = _quantity(activity, "tickets", people)
        result.update(start=row["From"], end=row["To"], start_time=row["BeginTime"],
                      end_time=row["EndTime"], price=row["Cost"], tickets=quantity,
                      cost=round(row["Cost"] * quantity, 2))
        if "position" in result:
            raise _Unresolved("intercity_has_position")
        return result, row, False
    name = activity.get("position")
    if type(name) is not str or not name:
        raise _Unresolved("missing_exact_entity_name")
    if any(key in activity for key in ("start", "end", "FlightID", "TrainID")):
        raise _Unresolved("poi_has_transport_identity")
    table = kind if kind in ("attraction", "accommodation") else "restaurant"
    rows = broker.call("lookup", table, city, name)
    hotel_breakfast = kind == "breakfast" and rows == []
    if hotel_breakfast:
        rows = broker.call("lookup", "accommodation", city, name)
    row = _one(rows, "name", name)
    price = 0 if hotel_breakfast else row.get("price")
    if not _number(price):
        raise _Unresolved("invalid_tool_price")
    if kind == "accommodation":
        beds = row.get("numbed")
        if not _positive(beds):
            raise _Unresolved("invalid_tool_room_capacity")
        # The database stores bed count, not certified guest occupancy. Do not
        # infer one-person-per-bed or change the model's chosen room count.
        quantity = activity.get("rooms")
        if not _positive(quantity):
            raise _Unresolved("invalid_quantity", field="rooms")
        result.update(room_type=beds, rooms=quantity)
    elif kind == "attraction":
        quantity = _quantity(activity, "tickets", people)
        result["tickets"] = quantity
    else:
        quantity = people
    result.update(price=price, cost=round(price * quantity, 2))
    return result, row, hotel_breakfast


def _window(activity, row, hotel_breakfast, preserve_durations, min_meal_minutes):
    kind = activity["type"]
    if kind == "accommodation":
        opened, closed, minimum = 0, 1440, 1
    else:
        if hotel_breakfast:
            opened, closed = _MEAL_WINDOWS["breakfast"]
        else:
            opened, closed = _minute(row.get("opentime")), _minute(row.get("endtime"))
            if opened is None or closed is None:
                raise _Unresolved("invalid_tool_opening_hours")
            # Match the official same-day interpretation of overnight opening;
            # do not manufacture 25:00+ times or assume a morning opening.
            if closed <= opened:
                closed = 1440
        if kind == "attraction":
            hours = row.get("recommendmintime")
            if not _number(hours) or hours <= 0:
                raise _Unresolved("missing_tool_minimum_visit_duration")
            minimum = math.ceil(hours * 60)
        else:
            minimum = min_meal_minutes
            meal_open, meal_close = _MEAL_WINDOWS[kind]
            opened, closed = max(opened, meal_open), min(closed, meal_close)
    begin, end = _minute(activity.get("start_time")), _minute(activity.get("end_time"))
    original_duration = end - begin if begin is not None and end is not None and end > begin else 0
    duration = max(minimum, original_duration) if preserve_durations else minimum
    if closed - opened < duration:
        raise _Unresolved("activity_duration_exceeds_open_window", activity=kind)
    return opened, closed, duration


def _bound_route(raw, source, target, depart, mode, people):
    expected_modes = ["walk", "metro", "walk"] if mode == "metro" else [mode]
    if (type(raw) is not list or len(raw) != len(expected_modes)
            or any(type(leg) is not dict for leg in raw)
            or [leg.get("mode") for leg in raw] != expected_modes):
        raise _Unresolved("invalid_route_response")
    legs, previous_end, previous_position = [], depart, source
    for leg in raw:
        begin, end = _minute(leg.get("start_time")), _minute(leg.get("end_time"))
        if (begin is None or end is None or begin != previous_end or end < begin
                or leg.get("start") != previous_position
                or type(leg.get("end")) is not str or not leg["end"]
                or not _number(leg.get("cost")) or not _number(leg.get("distance"))):
            raise _Unresolved("invalid_route_leg")
        bound = {key: deepcopy(leg[key]) for key in ("start", "end", "mode", "start_time", "end_time", "distance")}
        quantity = people if leg["mode"] == "metro" else math.ceil(people / 4) if leg["mode"] == "taxi" else 1
        if leg["mode"] == "metro":
            bound["tickets"] = quantity
        if leg["mode"] == "taxi":
            bound["cars"] = quantity
        bound.update(price=leg["cost"], cost=round(leg["cost"] * quantity, 2))
        legs.append(bound)
        previous_end, previous_position = end, leg["end"]
    if previous_position != target:
        raise _Unresolved("route_endpoint_mismatch")
    return legs, previous_end


def _route(source, target, depart, deadline, activity, city, people, policy, broker):
    if source is None or source == target:
        return [], depart
    if depart >= 1440 or depart > deadline:
        raise _Unresolved("no_same_day_route_window")
    if broker.call("poi", city, source) is not True or broker.call("poi", city, target) is not True:
        raise _Unresolved("unknown_route_endpoint", source=source, target=target)
    preferred = _original_mode(activity)
    modes = ([preferred] if preferred else []) + [mode for mode in _MODES if mode != preferred]
    candidates, failed = [], []
    for mode in modes:
        try:
            raw = broker.call("route", city, source, target, _clock(depart), mode)
            legs, arrival = _bound_route(raw, source, target, depart, mode, people)
            if arrival <= deadline:
                candidates.append((legs, arrival, mode))
                if policy == "preserve" and preferred == mode:
                    return legs, arrival
            else:
                failed.append({"mode": mode, "reason": "route_after_deadline"})
        except _Unresolved as exc:
            if exc.details["reason"].endswith("budget_exhausted") or exc.details["reason"] == "tool_result_too_large":
                raise
            failed.append({"mode": mode, **exc.details})
    broker.stats["route_options_rejected"] += len(failed)
    if not candidates:
        raise _Unresolved("no_feasible_route", source=source, target=target, options=failed)
    def rank(candidate):
        legs, arrival, mode = candidate
        cost = sum(leg["cost"] for leg in legs)
        return ((cost, arrival, mode != preferred, modes.index(mode)) if policy == "cheapest"
                else (arrival, cost, mode != preferred, modes.index(mode)))
    selected = min(candidates, key=rank)
    return selected[0], selected[1]


def construct_schedule(plan, translation, *, tools=None, policy="fastest", start_minutes=480,
                       max_route_calls=256, max_tool_calls=512, max_cache_entries=512,
                       preserve_durations=True, min_meal_minutes=30):
    """Construct one bounded proposal, or abstain with explicit reasons.

    ``fastest`` / ``cheapest`` rank official feasible routes. ``preserve`` first
    tries the existing mode, falling back to fastest feasible routes. Tickets
    cover all travellers and preserve larger explicit activity quantities;
    room counts remain explicit choices because beds do not prove occupancy.
    The minimum-duration variant must be requested with preserve_durations=False;
    caller verification is required because this can change intended durations.
    """
    if policy not in {"fastest", "cheapest", "preserve"}:
        raise ValueError("unsupported schedule policy")
    if type(start_minutes) is not int or not 0 <= start_minutes < 1440:
        raise ValueError("start_minutes must be in [0, 1440)")
    for value, maximum, name in ((max_route_calls, 2048, "max_route_calls"),
                                 (max_tool_calls, 4096, "max_tool_calls"),
                                 (max_cache_entries, 4096, "max_cache_entries")):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"invalid {name}")
    if type(preserve_durations) is not bool or type(min_meal_minutes) is not int or not 1 <= min_meal_minutes <= 180:
        raise ValueError("invalid duration policy")
    evidence, reasons = {}, []
    stats = {"tool_calls": 0, "route_calls": 0, "cache_hits": 0, "cache_entries": 0,
             "route_options_rejected": 0, "transport_pseudoactivities_removed": 0,
             "overnights_inserted": 0, "hotels_moved": 0, "activities_scheduled": 0,
             "policy": policy, "preserve_durations": preserve_durations}
    path, context = "", {}
    try:
        if type(plan) is not dict or type(translation) is not dict:
            raise _Unresolved("plan_or_translation_not_object")
        if (not _positive(translation.get("people_number"))
                or any(type(translation.get(key)) is not str or not translation[key] for key in ("start_city", "target_city"))
                or any(plan.get(key) != translation.get(key) for key in ("start_city", "target_city", "people_number"))):
            raise _Unresolved("translation_metadata_mismatch_or_invalid")
        days = plan.get("itinerary")
        if (type(days) is not list or not 1 <= len(days) <= 6
                or ("days" in translation and translation["days"] != len(days))):
            raise _Unresolved("invalid_or_mismatched_day_count")
        activities_count = 0
        for day in days:
            if type(day) is not dict or type(day.get("activities")) is not list or len(day["activities"]) > 64:
                raise _Unresolved("invalid_activities")
            activities_count += len(day["activities"])
        if activities_count > 128:
            raise _Unresolved("activity_budget_exceeded")
        broker = _BoundedTools(tools if tools is not None else OfficialTools(), evidence, stats,
                               max_route_calls, max_tool_calls, max_cache_entries)
        broker.record("construction_policy", {}, {"policy": policy, "start_minutes": start_minutes,
                       "preserve_durations": preserve_durations, "min_meal_minutes": min_meal_minutes,
                       "metadata": {key: translation[key] for key in ("people_number", "start_city", "target_city")}},
                      source="caller_self_translated_contract_and_constructor_policy")
        candidate, last_hotel, previous_position = deepcopy(plan), None, None
        city, origin_city, people = translation["target_city"], translation["start_city"], translation["people_number"]
        for day_index, day in enumerate(candidate["itinerary"]):
            final_day = day_index == len(days) - 1
            cleaned, pending_mode, activity_paths = [], None, {}
            for activity_index, activity in enumerate(day["activities"]):
                path = f"/itinerary/{day_index}/activities/{activity_index}"
                context = {"activity_type": activity.get("type"), "position": activity.get("position")} if type(activity) is dict else {}
                if type(activity) is not dict:
                    raise _Unresolved("activity_not_object")
                if activity.get("type") not in _ACTIVITY_TYPES:
                    if not _pure_transport(activity):
                        raise _Unresolved("unsupported_activity_type", activity_type=activity.get("type"))
                    broker.record("replace_standalone_route", {"path": path}, activity,
                                  source="input_plan_route_reconstruction")
                    pending_mode = activity.get("mode", activity["type"])
                    stats["transport_pseudoactivities_removed"] += 1
                    continue
                activity = deepcopy(activity)
                if pending_mode and not activity.get("transports"):
                    activity["transports"] = [{"mode": pending_mode}]
                pending_mode = None
                activity_paths[id(activity)] = path
                cleaned.append(activity)
            if pending_mode:
                raise _Unresolved("trailing_standalone_transport")
            hotels = [item for item in cleaned if item["type"] == "accommodation"]
            if not final_day:
                if len(hotels) > 1:
                    raise _Unresolved("multiple_hotels_in_one_day_requires_intent")
                if hotels:
                    hotel = hotels[0]
                    stats["hotels_moved"] += int(cleaned[-1] is not hotel)
                    cleaned = [item for item in cleaned if item["type"] != "accommodation"]
                elif last_hotel is not None:
                    hotel = deepcopy(last_hotel)
                    stats["overnights_inserted"] += 1
                else:
                    raise _Unresolved("overnight_requires_previously_chosen_hotel")
                cleaned.append(hotel)
                last_hotel = deepcopy(hotel)
            if not cleaned:
                raise _Unresolved("empty_day")
            if day_index == 0 and cleaned[0]["type"] not in ("train", "airplane"):
                raise _Unresolved("missing_outbound_intercity_anchor")
            if final_day and cleaned[-1]["type"] not in ("train", "airplane"):
                raise _Unresolved("missing_return_intercity_anchor")
            if day_index == 0 and final_day and len(cleaned) < 2:
                raise _Unresolved("outbound_and_return_need_distinct_anchors")
            clock, scheduled = start_minutes, []
            for activity_index, activity in enumerate(cleaned):
                path = activity_paths.get(id(activity), f"/itinerary/{day_index}/activities/{activity_index}")
                context = {"activity_type": activity.get("type"), "position": activity.get("position")}
                outward = day_index == 0 and activity_index == 0
                returning = final_day and activity_index == len(cleaned) - 1
                kind = activity["type"]
                if kind in ("train", "airplane") and not (outward or returning):
                    raise _Unresolved("intercity_not_boundary")
                bound, row, breakfast = _bind(activity, city, origin_city, people, outward, broker)
                if outward:
                    bound["transports"] = []
                    clock, previous_position = max(start_minutes, _minute(bound["end_time"])), bound["end"]
                elif returning:
                    deadline = _minute(bound["start_time"])
                    route, arrival = _route(previous_position, bound["start"], clock, deadline,
                                            activity, city, people, policy, broker)
                    if arrival > deadline:
                        raise _Unresolved("missed_fixed_return_departure")
                    bound["transports"] = route
                    clock, previous_position = _minute(bound["end_time"]), bound["end"]
                else:
                    overnight = kind == "accommodation" and not final_day and activity_index == len(cleaned) - 1
                    if overnight:
                        opened, closed, duration = 0, 1440, 1
                    else:
                        opened, closed, duration = _window(activity, row, breakfast, preserve_durations, min_meal_minutes)
                    route, arrival = _route(previous_position, bound["position"], clock, closed - duration,
                                            activity, city, people, policy, broker)
                    begin = max(arrival, opened)
                    end = 1440 if overnight else begin + duration
                    if begin >= end or end > closed or begin > 1440 or end > 1440:
                        raise _Unresolved("activity_window_infeasible", activity_type=kind, position=bound["position"])
                    bound.update(start_time=_clock(begin), end_time=_clock(end), transports=route)
                    clock, previous_position = end, bound["position"]
                scheduled.append(bound)
                stats["activities_scheduled"] += 1
            day["activities"] = scheduled
        return ConstructionResult(candidate, evidence, (), stats)
    except _Unresolved as exc:
        reasons.append({"path": path, **context, **exc.details})
    except Exception as exc:
        reasons.append({"path": path, "reason": "construction_exception", "exception_class": type(exc).__name__})
    return ConstructionResult(None, evidence, tuple(reasons), stats)
