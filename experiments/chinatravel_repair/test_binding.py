from copy import deepcopy
import pytest

from experiments.chinatravel_repair.binding import binding_checks, propose_repairs
from resimind.repair import apply_repair


class Tools:
    def __init__(self):
        self.rows = {
            ("attraction", "A"): [{"name": "A", "price": 10}],
            ("restaurant", "B"): [{"name": "B", "price": 20}],
            ("accommodation", "Hotel"): [{"name": "Hotel", "price": 80, "numbed": 2}],
        }
        self.known = {"A", "B", "Hotel", "Station", "Airport"}
        self.route_calls = []
        self.trip_rows = [{"TrainID": "G1", "From": "Station", "To": "Airport", "BeginTime": "08:00", "EndTime": "09:00", "Cost": 100}]

    def lookup(self, kind, city, name):
        assert city == "南京"
        return deepcopy(self.rows.get((kind, name), []))

    def intercity(self, start_city, end_city, kind, identifier):
        return deepcopy([row for row in self.trip_rows if row.get("TrainID") == identifier])

    def poi(self, city, name):
        return name in self.known

    def route(self, city, start, end, start_time, mode):
        self.route_calls.append((city, start, end, start_time, mode))
        hour, minute = map(int, start_time.split(":"))
        minutes = hour * 60 + minute
        def clock(add):
            return f"{(minutes + add) // 60:02d}:{(minutes + add) % 60:02d}"
        def leg(source, target, kind, begin, finish, cost):
            return {"start": source, "end": target, "mode": kind,
                    "start_time": clock(begin), "end_time": clock(finish),
                    "cost": cost, "distance": 1.25}
        if mode == "metro":
            return [leg(start, "Metro A", "walk", 0, 5, 0),
                    leg("Metro A", "Metro B", "metro", 5, 15, 3),
                    leg("Metro B", end, "walk", 15, 20, 0)]
        return [leg(start, end, mode, 0, 20, 11 if mode == "taxi" else 0)]


META = {"people_number": 2, "start_city": "上海", "target_city": "南京"}


def activity(kind="attraction", name="A", **kw):
    return {"type": kind, "position": name, "start_time": "10:00", "end_time": "11:00",
            "price": 10, "cost": 20, "tickets": 2, "transports": [], **kw}


def plan(*activities):
    return {**META, "itinerary": [{"day": 1, "activities": list(activities)}]}


def apply_kind(original, report, kind, tools):
    index = report.proposal_kinds.index(kind)
    result = apply_repair(original, report.proposals[index], evidence_ids=report.evidence,
                          verifier=lambda value: binding_checks(value, META, tools=tools), max_edits=128)
    assert result.status in {"accepted", "repaired"}, result.reason
    return result.plan


def test_exact_price_and_multiplication_bind_without_mutation():
    tools = Tools()
    original = plan(activity(price=1, cost=1))
    snapshot = deepcopy(original)
    report = propose_repairs(original, META, tools=tools)
    fixed = apply_kind(original, report, "activity_data", tools)
    assert original == snapshot
    item = fixed["itinerary"][0]["activities"][0]
    assert (item["price"], item["tickets"], item["cost"]) == (10, 2, 20)
    assert item["position"] == "A"
    assert {v["source"] for v in report.evidence.values()} == {"official_sandbox_tool", "caller_self_translated_contract"}


@pytest.mark.parametrize("rows,reason", [([], "unknown_entity"), ([{"price": 10}, {"price": 99}], "ambiguous_entity")])
def test_unknown_or_duplicate_entity_abstains(rows, reason):
    tools = Tools()
    tools.rows["attraction", "A"] = rows
    report = propose_repairs(plan(activity(price=999)), META, tools=tools)
    assert not report.proposals
    assert any(item["reason"] == reason for item in report.unresolved)


def test_fuzzy_names_do_not_bind():
    tools = Tools()
    report = propose_repairs(plan(activity(name="A附近", price=999)), META, tools=tools)
    assert not report.proposals
    assert any(item["reason"] == "unknown_entity" for item in report.unresolved)


def test_metadata_disagreement_prevents_arbitrary_rewrite():
    original = plan(activity(price=0))
    report = propose_repairs(original, {**META, "people_number": 3}, tools=Tools())
    assert not report.proposals
    assert report.unresolved[0]["reason"] == "translation_metadata_mismatch_or_invalid"


def test_missing_tickets_uses_contract_but_existing_quantity_is_preserved():
    tools = Tools()
    item = activity(price=999)
    item.pop("tickets")
    original = plan(item)
    fixed = apply_kind(original, propose_repairs(original, META, tools=tools), "activity_data", tools)
    assert fixed["itinerary"][0]["activities"][0]["tickets"] == 2
    original = plan(activity(tickets=3, price=999))
    fixed = apply_kind(original, propose_repairs(original, META, tools=tools), "activity_data", tools)
    assert fixed["itinerary"][0]["activities"][0]["cost"] == 30


def test_room_binding_preserves_quantity_and_abstains_if_unknown():
    tools = Tools()
    original = plan(activity("accommodation", "Hotel", rooms=2, room_type=1))
    fixed = apply_kind(original, propose_repairs(original, META, tools=tools), "activity_data", tools)
    hotel = fixed["itinerary"][0]["activities"][0]
    assert (hotel["rooms"], hotel["room_type"], hotel["price"], hotel["cost"]) == (2, 2, 80, 160)
    hotel.pop("rooms")
    report = propose_repairs(plan(hotel), META, tools=tools)
    assert not report.proposals
    assert any(item["reason"] == "room_quantity_requires_intent" for item in report.unresolved)


def test_intercity_binds_only_exact_unique_id_and_does_not_change_identifier():
    tools = Tools()
    item = {"type": "train", "TrainID": "G1", "start": "Wrong", "end": "Wrong",
            "start_time": "07:30", "end_time": "10:00", "price": 1, "cost": 1, "tickets": 2, "transports": []}
    original = plan(item)
    fixed = apply_kind(original, propose_repairs(original, META, tools=tools), "intercity_data", tools)
    actual = fixed["itinerary"][0]["activities"][0]
    assert (actual["TrainID"], actual["start"], actual["end"], actual["cost"]) == ("G1", "Station", "Airport", 200)
    item["TrainID"] = "G1-like"
    assert not propose_repairs(plan(item), META, tools=tools).proposals


def transport(mode="walk", **kw):
    return {"start": "Wrong", "end": "Wrong", "mode": mode, "start_time": "11:00",
            "end_time": "11:10", "price": 0, "cost": 0, "distance": 0.1, **kw}


def test_broken_adjacent_chain_recalculated_exactly_without_activity_changes():
    tools = Tools()
    original = plan(activity(), activity("lunch", "B", start_time="12:00", end_time="13:00",
                                         price=20, cost=40, transports=[transport()]))
    report = propose_repairs(original, META, tools=tools)
    fixed = apply_kind(original, report, "transport_routes", tools)
    leg = fixed["itinerary"][0]["activities"][1]["transports"][0]
    assert (leg["start"], leg["end"], leg["start_time"], leg["end_time"], leg["distance"]) == ("A", "B", "11:00", "11:20", 1.25)
    assert "TrainID" not in leg and "FlightID" not in leg and "position" not in leg
    assert fixed["itinerary"][0]["activities"][1]["start_time"] == "12:00"
    assert fixed["itinerary"][0]["activities"][0] == original["itinerary"][0]["activities"][0]


def test_metro_route_preserves_mode_and_binds_quantity_cost():
    tools = Tools()
    original = plan(activity(), activity("lunch", "B", start_time="12:00", end_time="13:00", price=20, cost=40,
                                         transports=[transport("metro", tickets=2)]))
    fixed = apply_kind(original, propose_repairs(original, META, tools=tools), "transport_routes", tools)
    legs = fixed["itinerary"][0]["activities"][1]["transports"]
    assert [leg["mode"] for leg in legs] == ["walk", "metro", "walk"]
    assert legs[1]["tickets"] == 2 and legs[1]["price"] == 3 and legs[1]["cost"] == 6
    assert "tickets" not in legs[0]


def test_route_uses_earlier_known_departure_but_never_changes_activity_time():
    tools = Tools()
    original = plan(activity(), activity("lunch", "B", start_time="11:30", end_time="12:30", price=20, cost=40,
                                         transports=[transport(start_time="11:20")]))
    fixed = apply_kind(original, propose_repairs(original, META, tools=tools), "transport_routes", tools)
    leg = fixed["itinerary"][0]["activities"][1]["transports"][0]
    assert leg["start_time"] == "11:00" and leg["end_time"] == "11:20"
    assert fixed["itinerary"][0]["activities"][1]["start_time"] == "11:30"


@pytest.mark.parametrize("legs,reason", [([], "missing_route_mode_requires_intent"), ([transport("taxi")], "taxi_car_quantity_requires_intent")])
def test_unspecified_mode_or_car_quantity_abstains(legs, reason):
    tools = Tools()
    original = plan(activity(), activity("lunch", "B", start_time="12:00", end_time="13:00", price=20, cost=40, transports=legs))
    report = propose_repairs(original, META, tools=tools)
    assert not report.proposals
    assert any(item["reason"] == reason for item in report.unresolved)


def test_insufficient_travel_window_abstains():
    tools = Tools()
    original = plan(activity(), activity("lunch", "B", start_time="11:10", end_time="12:00", price=20, cost=40,
                                         transports=[transport()]))
    report = propose_repairs(original, META, tools=tools)
    assert not report.proposals
    assert any(item["reason"] == "route_does_not_fit_activity_window" for item in report.unresolved)


def test_tool_failure_is_explicit_and_non_mutating():
    class Broken(Tools):
        def lookup(self, *args):
            raise RuntimeError("tool failed")
    original = plan(activity(price=1))
    snapshot = deepcopy(original)
    report = propose_repairs(original, META, tools=Broken())
    assert not report.proposals and original == snapshot
    assert any(item["reason"] == "entity_tool_exception" for item in report.unresolved)


def test_binding_keys_stay_fixed_across_price_and_route_proposals():
    tools = Tools()
    original = plan(activity(price=1), activity("lunch", "B", start_time="12:00", end_time="13:00", price=20, cost=40,
                                               transports=[transport()]))
    first = binding_checks(original, META, tools=tools)
    report = propose_repairs(original, META, tools=tools)
    fixed = apply_kind(original, report, "activity_data", tools)
    assert first.keys() == binding_checks(fixed, META, tools=tools).keys()
    fixed = apply_kind(fixed, propose_repairs(fixed, META, tools=tools), "transport_routes", tools)
    assert first.keys() == binding_checks(fixed, META, tools=tools).keys()


def test_patch_limit_is_enforced():
    assert not propose_repairs(plan(activity(price=1)), META, tools=Tools(), max_patches=0).proposals
    with pytest.raises(ValueError):
        propose_repairs(plan(activity()), META, tools=Tools(), max_patches=1000)


@pytest.mark.parametrize("previous", [
    activity("dinner", "B", start_time="18:00", end_time="19:00", price=20, cost=40),
    activity("accommodation", "Hotel", start_time="08:40", end_time="09:00",
             price=80, cost=80, rooms=1, room_type=2),
])
def test_cross_day_origin_without_explicit_overnight_abstains(previous):
    tools = Tools()
    morning = activity("breakfast", "B", start_time="08:00", end_time="08:30",
                       price=20, cost=40,
                       transports=[transport(start="Hotel", end="B", start_time="07:30")])
    original = {**META, "itinerary": [
        {"day": 1, "activities": [deepcopy(previous)]},
        {"day": 2, "activities": [morning]},
    ]}
    snapshot = deepcopy(original)
    report = propose_repairs(original, META, tools=tools)
    checks = binding_checks(original, META, tools=tools)
    assert original == snapshot
    assert not report.proposals and not tools.route_calls
    assert checks["binding:route:/itinerary/1/activities/0"] is None
    assert any(item["path"] == "/itinerary/1/activities/0/transports"
               and item["reason"] == "overnight_position_unverified"
               for item in report.unresolved)
    assert not all(value is True for value in checks.values())


def test_cross_day_route_can_bind_from_explicit_overnight_hotel():
    tools = Tools()
    hotel = activity("accommodation", "Hotel", start_time="21:00", end_time="24:00",
                     price=80, cost=80, rooms=1, room_type=2)
    morning = activity("breakfast", "B", start_time="08:00", end_time="08:30",
                       price=20, cost=40, transports=[transport(start_time="07:30")])
    original = {**META, "itinerary": [
        {"day": 1, "activities": [hotel]},
        {"day": 2, "activities": [morning]},
    ]}
    report = propose_repairs(original, META, tools=tools)
    fixed = apply_kind(original, report, "transport_routes", tools)
    leg = fixed["itinerary"][1]["activities"][0]["transports"][0]
    assert (leg["start"], leg["end"], leg["start_time"], leg["end_time"]) == (
        "Hotel", "B", "07:30", "07:50")
    assert fixed["itinerary"][0]["activities"][0] == hotel
    assert fixed["itinerary"][1]["activities"][0]["start_time"] == "08:00"
    assert binding_checks(fixed, META, tools=tools)["binding:route:/itinerary/1/activities/0"] is True
    assert not any(item["reason"] == "overnight_position_unverified" for item in report.unresolved)
