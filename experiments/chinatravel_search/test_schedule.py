from copy import deepcopy

import pytest

from .schedule import construct_schedule


META = {"people_number": 5, "start_city": "上海", "target_city": "南京"}


def clock(value):
    return f"{value // 60:02d}:{value % 60:02d}"


class Tools:
    def __init__(self):
        self.rows = {
            ("attraction", "Park"): [{"name": "Park", "price": 10, "opentime": "09:00", "endtime": "18:00", "recommendmintime": 1.5}],
            ("attraction", "Museum"): [{"name": "Museum", "price": 20, "opentime": "10:00", "endtime": "17:00", "recommendmintime": 1}],
            ("restaurant", "Lunch"): [{"name": "Lunch", "price": 30, "opentime": "10:00", "endtime": "22:00"}],
            ("restaurant", "Dinner"): [{"name": "Dinner", "price": 40, "opentime": "10:00", "endtime": "22:00"}],
            ("accommodation", "Hotel"): [{"name": "Hotel", "price": 90, "numbed": 2}],
        }
        self.trips = {
            "G1": {"TrainID": "G1", "From": "Origin", "To": "Station", "BeginTime": "07:00", "EndTime": "08:00", "Cost": 100},
            "G2": {"TrainID": "G2", "From": "Station", "To": "Origin", "BeginTime": "21:00", "EndTime": "23:00", "Cost": 100},
        }
        self.calls = []
        self.delays = {"walk": 60, "metro": 30, "taxi": 15}

    def lookup(self, kind, city, name):
        self.calls.append(("lookup", kind, city, name))
        return deepcopy(self.rows.get((kind, name), []))

    def intercity(self, start, end, kind, identifier):
        self.calls.append(("intercity", start, end, kind, identifier))
        return deepcopy([self.trips[identifier]]) if identifier in self.trips else []

    def poi(self, city, name):
        self.calls.append(("poi", city, name))
        return name in {"Station", "Park", "Museum", "Hotel", "Lunch", "Dinner"}

    def route(self, city, start, end, start_time, mode):
        self.calls.append(("route", city, start, end, start_time, mode))
        hour, minute = map(int, start_time.split(":"))
        begin = hour * 60 + minute
        def leg(source, target, kind, st, ed, cost):
            return {"start": source, "end": target, "mode": kind, "start_time": clock(st),
                    "end_time": clock(ed), "cost": cost, "distance": 2.5}
        if mode == "metro":
            return [leg(start, "MetroA", "walk", begin, begin + 5, 0),
                    leg("MetroA", "MetroB", "metro", begin + 5, begin + self.delays[mode] - 5, 3),
                    leg("MetroB", end, "walk", begin + self.delays[mode] - 5, begin + self.delays[mode], 0)]
        return [leg(start, end, mode, begin, begin + self.delays[mode], 12 if mode == "taxi" else 0)]


def trip(identifier):
    return {"type": "train", "TrainID": identifier, "start_time": "00:00", "end_time": "00:01", "transports": []}


def activity(kind="attraction", name="Park", **kw):
    return {"type": kind, "position": name, "start_time": "10:00", "end_time": "11:00", "transports": [], **kw}


def plan(*days):
    return {**META, "itinerary": [{"day": index + 1, "activities": list(items)} for index, items in enumerate(days)]}


def one_day(*items):
    return plan([trip("G1"), *items, trip("G2")])


def construct(original, **kw):
    return construct_schedule(original, {**META, "days": len(original["itinerary"])}, tools=kw.pop("tools", Tools()), **kw)


def test_exact_data_routes_and_windows_without_mutating_input():
    original = one_day(activity(), activity("lunch", "Lunch"), activity("attraction", "Museum"))
    snapshot = deepcopy(original)
    result = construct(original)
    assert result.plan is not None, result.reasons
    assert original == snapshot
    acts = result.plan["itinerary"][0]["activities"]
    assert [item.get("position") for item in acts] == [None, "Park", "Lunch", "Museum", None]
    assert (acts[0]["TrainID"], acts[0]["start_time"], acts[0]["end_time"], acts[0]["tickets"]) == ("G1", "07:00", "08:00", 5)
    assert (acts[1]["start_time"], acts[1]["end_time"], acts[1]["price"], acts[1]["cost"]) == ("09:00", "10:30", 10, 50)
    assert (acts[2]["start_time"], acts[2]["end_time"]) == ("11:00", "12:00")
    taxi = acts[1]["transports"][0]
    assert (taxi["start"], taxi["end"], taxi["cars"], taxi["cost"]) == ("Station", "Park", 2, 24)
    assert acts[-1]["TrainID"] == "G2" and acts[-1]["start_time"] == "21:00"
    assert result.evidence and result.stats["route_calls"] == 12


def test_cheapest_prefers_real_walk_and_preserve_prefers_existing_mode():
    draft = one_day(activity(transports=[{"mode": "metro"}]))
    cheapest = construct(draft, policy="cheapest")
    preserved = construct(draft, policy="preserve")
    assert cheapest.plan["itinerary"][0]["activities"][1]["transports"][0]["mode"] == "walk"
    legs = preserved.plan["itinerary"][0]["activities"][1]["transports"]
    assert [leg["mode"] for leg in legs] == ["walk", "metro", "walk"]
    assert legs[1]["tickets"] == 5 and legs[1]["cost"] == 15


def test_preserve_falls_back_when_old_mode_cannot_reach_fixed_departure():
    tools = Tools()
    tools.trips["G2"]["BeginTime"] = "10:50"
    draft = one_day(activity())
    draft["itinerary"][0]["activities"][-1]["transports"] = [{"mode": "walk"}]
    result = construct(draft, tools=tools, policy="preserve")
    assert result.plan is not None, result.reasons
    assert result.plan["itinerary"][0]["activities"][-1]["transports"][0]["mode"] == "taxi"


def test_move_hotel_after_dinner_and_explicitly_return_then_start_next_day_there():
    draft = plan([trip("G1"), activity("accommodation", "Hotel", rooms=3), activity(), activity("dinner", "Dinner")],
                 [activity("breakfast", "Hotel", start_time="08:00", end_time="08:30"), activity("attraction", "Museum"), trip("G2")])
    result = construct(draft)
    assert result.plan is not None, result.reasons
    first, second = result.plan["itinerary"][0]["activities"], result.plan["itinerary"][1]["activities"]
    assert [item["type"] for item in first] == ["train", "attraction", "dinner", "accommodation"]
    assert first[-1]["end_time"] == "24:00" and first[-1]["room_type"] == 2 and first[-1]["cost"] == 270
    assert first[-1]["transports"][0]["start"] == "Dinner" and first[-1]["transports"][-1]["end"] == "Hotel"
    assert second[0]["transports"] == [] and second[0]["price"] == 0
    assert second[1]["transports"][0]["start"] == "Hotel"
    assert result.stats["hotels_moved"] == 1


def test_insert_overnight_only_from_previous_chosen_hotel_and_preserve_rooms():
    draft = plan([trip("G1"), activity("accommodation", "Hotel", rooms=1)],
                 [activity()], [activity("attraction", "Museum"), trip("G2")])
    result = construct(draft)
    assert result.plan is not None, result.reasons
    assert result.stats["overnights_inserted"] == 1
    for day in result.plan["itinerary"][:2]:
        assert day["activities"][-1]["position"] == "Hotel"
        assert day["activities"][-1]["rooms"] == 1
        assert day["activities"][-1]["end_time"] == "24:00"
    missing = plan([trip("G1"), activity()], [activity("accommodation", "Hotel"), trip("G2")])
    assert construct(missing).reasons[0]["reason"] == "overnight_requires_previously_chosen_hotel"


def test_zero_room_luggage_stop_does_not_turn_into_paid_overnight():
    result = construct(plan([trip("G1"), activity("accommodation", "Hotel", rooms=0)], [trip("G2")]))
    assert result.plan is None and result.reasons[0]["reason"] == "invalid_quantity"


def test_room_count_is_never_inferred_from_bed_count_or_people():
    result = construct(plan([trip("G1"), activity("accommodation", "Hotel")], [trip("G2")]))
    assert result.plan is None and result.reasons[0]["reason"] == "invalid_quantity"
    assert result.reasons[0]["field"] == "rooms"


@pytest.mark.parametrize("kind", ["shopping", "rest", "hotel_checkout", "transport"])
def test_unknown_activity_is_never_silently_dropped(kind):
    result = construct(one_day(activity(kind)))
    assert result.plan is None and result.reasons[0]["reason"] == "unsupported_activity_type"


def test_only_proven_route_pseudoactivity_is_removed_and_rebuilt():
    pseudo = {"type": "transport", "mode": "walk", "start": "Station", "end": "Park", "start_time": "08:00", "end_time": "08:01"}
    result = construct(one_day(pseudo, activity()), policy="preserve")
    assert result.plan is not None, result.reasons
    assert len(result.plan["itinerary"][0]["activities"]) == 3
    assert result.stats["transport_pseudoactivities_removed"] == 1
    assert result.plan["itinerary"][0]["activities"][1]["transports"][0]["end_time"] == "09:00"
    pseudo["description"] = "Required observation activity"
    assert construct(one_day(pseudo, activity())).plan is None


@pytest.mark.parametrize("rows,reason", [([], "unknown_entity"), ([{"name": "Park"}, {"name": "Park"}], "ambiguous_entity"), ([{"name": "Other"}], "entity_identifier_mismatch")])
def test_exact_entity_binding_abstains_on_missing_ambiguous_or_wrong_row(rows, reason):
    tools = Tools()
    tools.rows["attraction", "Park"] = rows
    result = construct(one_day(activity()), tools=tools)
    assert result.plan is None and result.reasons[0]["reason"] == reason


def test_recommended_minimum_is_always_respected_and_duration_shortening_explicit():
    draft = one_day(activity(start_time="10:00", end_time="13:00"))
    regular, minimum = construct(draft), construct(draft, preserve_durations=False)
    assert regular.plan["itinerary"][0]["activities"][1]["end_time"] == "12:00"
    assert minimum.plan["itinerary"][0]["activities"][1]["end_time"] == "10:30"
    assert minimum.stats["preserve_durations"] is False


def test_meal_window_conflict_and_missed_train_abstain_instead_of_dropping_activity():
    tools = Tools()
    tools.rows["restaurant", "Dinner"][0]["endtime"] = "17:15"
    result = construct(one_day(activity("dinner", "Dinner")), tools=tools, preserve_durations=False)
    assert result.plan is None and result.reasons[0]["path"] == "/itinerary/0/activities/1"
    assert result.reasons[0]["reason"] == "activity_duration_exceeds_open_window"
    tools = Tools()
    tools.trips["G2"]["BeginTime"] = "10:35"
    result = construct(one_day(activity()), tools=tools)
    assert result.plan is None and result.reasons[0]["reason"] == "no_feasible_route"


def test_failure_path_points_to_original_entity_even_after_moving_hotel():
    tools = Tools()
    tools.rows["restaurant", "Dinner"][0]["endtime"] = "17:15"
    draft = plan([trip("G1"), activity("accommodation", "Hotel", rooms=1), activity("dinner", "Dinner")], [trip("G2")])
    result = construct(draft, tools=tools)
    assert result.plan is None
    assert result.reasons[0]["path"] == "/itinerary/0/activities/2"
    assert result.reasons[0]["activity_type"] == "dinner" and result.reasons[0]["position"] == "Dinner"


@pytest.mark.parametrize("limit,reason", [({"max_route_calls": 0}, "route_budget_exhausted"), ({"max_tool_calls": 0}, "tool_budget_exhausted"), ({"max_cache_entries": 0}, "cache_budget_exhausted")])
def test_tool_budgets_are_hard_abstention_limits(limit, reason):
    tools = Tools()
    result = construct(one_day(activity()), tools=tools, **limit)
    assert result.plan is None and result.reasons[0]["reason"] == reason
    if "max_tool_calls" in limit or "max_cache_entries" in limit:
        assert tools.calls == []


def test_cache_avoids_duplicate_lookup_and_poi_calls():
    tools = Tools()
    result = construct(one_day(activity(), activity()), tools=tools)
    assert result.plan is not None
    assert len([call for call in tools.calls if call[:2] == ("lookup", "attraction")]) == 1
    assert result.stats["cache_hits"] > 0
    # Duplicated attraction still present: the complete checker must reject it.
    assert len(result.plan["itinerary"][0]["activities"]) == 4


def test_one_broken_route_mode_can_use_other_official_route_but_exceptions_are_recorded():
    class Broken(Tools):
        def route(self, *args):
            if args[-1] == "walk":
                raise RuntimeError("private detail must not appear")
            return super().route(*args)
    result = construct(one_day(activity()), tools=Broken())
    assert result.plan is not None
    assert result.stats["route_options_rejected"] == 2
    assert any(value["result"] == {"exception_class": "RuntimeError"} for value in result.evidence.values())
    assert "private detail" not in str(result.evidence)


def test_inconsistent_route_chain_is_not_accepted():
    class Broken(Tools):
        def route(self, *args):
            route = super().route(*args)
            route[0]["start"] = "Wrong"
            return route
    result = construct(one_day(activity()), tools=Broken())
    assert result.plan is None and result.reasons[0]["reason"] == "no_feasible_route"


def test_intercity_ids_times_and_overnight_wrap_are_not_fabricated():
    tools = Tools()
    tools.trips["G1"]["EndTime"] = "06:00"
    result = construct(one_day(activity()), tools=tools)
    assert result.plan is None and result.reasons[0]["reason"] == "invalid_or_overnight_intercity_row"
    tools = Tools()
    tools.trips["G1"]["TrainID"] = "G9"
    assert construct(one_day(activity()), tools=tools).reasons[0]["reason"] == "entity_identifier_mismatch"


def test_one_train_cannot_stand_for_both_outbound_and_return():
    result = construct(plan([trip("G1")]))
    assert result.plan is None and result.reasons[0]["reason"] == "outbound_and_return_need_distinct_anchors"


def test_non_clock_intercity_time_is_rejected_even_if_python_can_parse_number():
    tools = Tools()
    tools.trips["G1"]["BeginTime"] = " 7:00"
    result = construct(one_day(activity()), tools=tools)
    assert result.plan is None and result.reasons[0]["reason"] == "invalid_or_overnight_intercity_row"


def test_no_after_midnight_output_or_silent_entity_choice_changes():
    tools = Tools()
    tools.delays = {mode: 1500 for mode in tools.delays}
    result = construct(one_day(activity()), tools=tools)
    assert result.plan is None and result.reasons[0]["reason"] == "no_feasible_route"


@pytest.mark.parametrize("kwargs", [{"policy": "random"}, {"max_route_calls": -1}, {"max_tool_calls": True}, {"max_cache_entries": 5000}, {"start_minutes": 1440}, {"preserve_durations": "no"}, {"min_meal_minutes": 0}])
def test_invalid_configuration_fails_before_any_tool_access(kwargs):
    tools = Tools()
    with pytest.raises(ValueError):
        construct(one_day(activity()), tools=tools, **kwargs)
    assert not tools.calls
