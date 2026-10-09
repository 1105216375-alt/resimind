"""Search adapters retain obligations and report the exact assessed draft."""
from copy import deepcopy
from pathlib import Path

import pytest

from resimind.repair import plan_sha256
from resimind.search import SearchLimits
from . import solve
from .schedule import ConstructionResult


def item(kind, name, start="10:00", end="11:00", **fields):
    identity = ({"TrainID": name} if kind == "train" else
                {"FlightID": name} if kind == "airplane" else {"position": name})
    return {"type": kind, **identity, "start_time": start, "end_time": end,
            "transports": [], **fields}


def plan(*days):
    return {"start_city": "上海", "target_city": "南京", "people_number": 1,
            "itinerary": [{"day": index + 1, "activities": deepcopy(list(activities))}
                          for index, activities in enumerate(days)]}


def single_day():
    return plan([item("train", "G1", "07:00", "08:00"),
                 item("attraction", "Park"), item("lunch", "Named Diner", "12:00", "13:00"),
                 item("airplane", "F2", "20:00", "21:00")])


def hotel_days():
    return plan([item("train", "G1", "07:00", "08:00"),
                 item("accommodation", "Hotel", "09:00", "09:30", rooms=0),
                 item("attraction", "Park"),
                 item("accommodation", "Hotel", "22:00", "24:00", rooms=1)],
                [item("train", "G2", "20:00", "21:00")])


class Local:
    def __init__(self, checks=None):
        self.checks = checks if checks is not None else {
            "schema": True, "self_constraint/0": True,
            "binding/activity0": True, "coverage/activity0": True,
        }
        self.last_diagnostics = {}
        self.calls = []

    def __call__(self, draft):
        self.calls.append(deepcopy(draft))
        self.last_diagnostics = {"call": len(self.calls), "marker": draft.get("marker", 0)}
        return deepcopy(self.checks)


@pytest.mark.parametrize("values,expected", [
    ([], None), ([True], True), ([True, True], True), ([True, None], None),
    ([False, None], False), ([True, False], False),
])
def test_aggregation_never_turns_absent_or_unknown_obligations_into_pass(values, expected):
    assert solve._all(iter(values)) is expected


@pytest.mark.parametrize("missing", ["binding", "coverage"])
def test_missing_detailed_group_is_unknown_and_not_a_vacuous_pass(missing):
    local = Local({"schema": True, ("coverage" if missing == "binding" else "binding") + "/0": True})
    checks = solve.WholePlanVerifier(single_day(), local, "Park")(single_day())
    assert checks[missing] is None


def test_topology_aggregation_keeps_stable_keys_but_reruns_every_detailed_check():
    class Dynamic(Local):
        def __call__(self, draft):
            super().__call__(draft)
            checks = {"schema": True, "self_constraint/0": True}
            for index, activity in enumerate(solve.activities(draft)):
                checks[f"binding/{index}"] = activity.get("position") != "Unbound Diner"
                checks[f"coverage/{index}"] = True
            return checks

    initial, local = single_day(), Dynamic()
    verifier = solve.WholePlanVerifier(initial, local, "Park")
    before = verifier(initial)
    changed = deepcopy(initial)
    changed["itinerary"][0]["activities"].insert(3, item("dinner", "Unbound Diner"))
    after = verifier(changed)
    assert set(before) == set(after) and before["binding"] is True and after["binding"] is False
    assert len(local.calls) == 2


@pytest.mark.parametrize("kind,index,replacement", [
    ("attraction", 1, item("attraction", "Other Park")),
    ("train", 0, item("train", "G9")),
    ("airplane", 3, item("airplane", "F9")),
])
def test_attraction_and_intercity_identities_cannot_be_replaced(kind, index, replacement):
    initial = single_day()
    candidate = deepcopy(initial)
    candidate["itinerary"][0]["activities"][index] = replacement
    checks = solve.WholePlanVerifier(initial, Local(), "")(candidate)
    assert checks["retained_attractions_and_intercity"] is False


def test_attraction_multiplicity_cannot_disappear_behind_a_set():
    initial = single_day()
    initial["itinerary"][0]["activities"].insert(2, deepcopy(initial["itinerary"][0]["activities"][1]))
    candidate = deepcopy(initial)
    candidate["itinerary"][0]["activities"].pop(2)
    checks = solve.WholePlanVerifier(initial, Local(), "")(candidate)
    assert checks["retained_attractions_and_intercity"] is False


def test_user_named_restaurant_cannot_be_replaced_even_when_local_checks_pass():
    initial, candidate = single_day(), single_day()
    candidate["itinerary"][0]["activities"][2]["position"] = "Other Diner"
    checks = solve.WholePlanVerifier(initial, Local(), "I want Named Diner")(candidate)
    assert checks["retained_user_named_entities"] is False
    assert checks["binding"] is True and checks["coverage"] is True


def test_missing_room_quantity_does_not_erase_initial_hotel_choice():
    initial = hotel_days()
    initial["itinerary"][0]["activities"].pop(1)
    initial["itinerary"][0]["activities"][-1].pop("rooms")
    candidate = deepcopy(initial)
    candidate["itinerary"][0]["activities"][-1]["rooms"] = 1
    checks = solve.WholePlanVerifier(initial, Local(), "")(candidate)
    assert checks["retained_hotel_choices"] is True
    candidate["itinerary"][0]["activities"][-1]["position"] = "Other Hotel"
    assert solve.WholePlanVerifier(initial, Local(), "")(candidate)["retained_hotel_choices"] is False


def test_missing_overnight_and_first_intercity_route_cannot_pass():
    initial, candidate = hotel_days(), hotel_days()
    candidate["itinerary"][0]["activities"][-1]["end_time"] = "23:00"
    candidate["itinerary"][0]["activities"][0]["transports"] = [{"mode": "walk"}]
    checks = solve.WholePlanVerifier(initial, Local(), "")(candidate)
    assert checks["explicit_overnights"] is False
    assert checks["first_intercity_empty_route"] is False


def test_zero_room_luggage_visit_is_kept_without_application_allowlist():
    initial = hotel_days()
    snapshot = deepcopy(initial)
    normalized, changes = solve.normalize_seed(initial, "先到酒店寄存行李再参观")
    assert len(solve.activities(normalized)) == len(solve.activities(initial))
    assert not any(change["kind"] == "omit_optional_zero_room_visit" for change in changes)
    assert initial == snapshot


def test_explicit_exact_allowlist_can_remove_only_eligible_zero_room_visit():
    initial = hotel_days()
    digest = plan_sha256(initial["itinerary"][0]["activities"][1])
    normalized, changes = solve.normalize_seed(initial, "参观公园", optional_visit_digests={digest})
    assert len(solve.activities(normalized)) == len(solve.activities(initial)) - 1
    assert changes[0]["kind"] == "omit_optional_zero_room_visit"
    assert changes[0]["path"] == "/itinerary/0/activities/1"
    assert initial["itinerary"][0]["activities"][1]["rooms"] == 0


@pytest.mark.parametrize("restriction", ["named", "no_overnight", "positive_rooms", "wrong_digest"])
def test_allowlist_does_not_override_other_visit_safeguards(restriction):
    initial = hotel_days()
    public = "Hotel" if restriction == "named" else "参观公园"
    if restriction == "no_overnight":
        initial["itinerary"][0]["activities"].pop()
    if restriction == "positive_rooms":
        initial["itinerary"][0]["activities"][1]["rooms"] = 1
    digest = "0" * 64 if restriction == "wrong_digest" else plan_sha256(initial["itinerary"][0]["activities"][1])
    normalized, changes = solve.normalize_seed(initial, public, optional_visit_digests={digest})
    assert len(solve.activities(normalized)) == len(solve.activities(initial))
    assert not any(change["kind"] == "omit_optional_zero_room_visit" for change in changes)


def setup_search(monkeypatch, local):
    monkeypatch.setattr(solve, "LocalContract", lambda *args, **kwargs: local)
    monkeypatch.setattr(solve, "preflight_translation", lambda _: {"needs_retranslation": False})
    monkeypatch.setattr(solve, "restaurant_alternatives", lambda *args, **kwargs: iter(()))
    monkeypatch.setattr(solve, "construct_schedule", lambda *args, **kwargs: ConstructionResult(None, {}, (), {}))


def run_search(initial, **kwargs):
    return solve.search_plan(initial, {}, Path("/unused-upstream"), tools=object(),
                             public_query={"nature_language": "参观公园"}, **kwargs)


def test_final_report_uses_accepted_assessment_without_an_extra_verifier_call(monkeypatch):
    class ChangingLocal(Local):
        def __call__(self, draft):
            result = super().__call__(draft)
            result["schema"] = len(self.calls) == 1
            return result

    local = ChangingLocal()
    setup_search(monkeypatch, local)
    result = run_search(single_day())
    assert result["status"] == "accepted" and result["plan"] is not None
    assert len(local.calls) == result["evaluated"] == 1
    assert result["detailed_checks"]["schema"] is True and result["diagnostics"]["call"] == 1


def test_best_draft_report_uses_its_snapshot_even_when_last_assessed_draft_differs(monkeypatch):
    class RankedLocal(Local):
        def __call__(self, draft):
            checks = super().__call__(draft)
            checks["complete"] = False
            checks["preference"] = draft.get("marker", 0) == 0
            return checks

    local = RankedLocal()
    setup_search(monkeypatch, local)
    changed = single_day()
    changed["marker"] = 1
    monkeypatch.setattr(solve, "construct_schedule", lambda *args, **kwargs: ConstructionResult(changed, {}, (), {}))
    result = run_search(single_day(), limits=SearchLimits(max_expansions=1))
    assert result["status"] == "limited" and result["plan"] is None
    assert result["best_draft"].get("marker", 0) == 0
    assert len(local.calls) == result["evaluated"] == 2
    assert result["detailed_checks"]["preference"] is True and result["diagnostics"]["marker"] == 0


def test_assessment_cache_does_not_alias_mutable_local_diagnostics():
    initial, local = single_day(), Local()
    verifier = solve.WholePlanVerifier(initial, local, "")
    verifier(initial)
    local.last_diagnostics["call"] = 999
    assert verifier.assessments[plan_sha256(initial)]["diagnostics"]["call"] == 1


def test_exact_original_path_allowlist_does_not_authorize_identical_other_day_visit(monkeypatch):
    initial = hotel_days()
    initial["itinerary"].insert(1, {"day": 2, "activities": [
        deepcopy(initial["itinerary"][0]["activities"][1]),
        deepcopy(initial["itinerary"][0]["activities"][-1]),
    ]})
    initial["itinerary"][2]["day"] = 3
    local = Local({"schema": False, "binding/0": True, "coverage/0": True})
    setup_search(monkeypatch, local)
    with pytest.raises(ValueError, match="ambiguous duplicate"):
        run_search(initial, optional_visit_paths=((0, 1),), limits=SearchLimits(max_expansions=1))
    assert local.calls == []
    digest = plan_sha256(initial["itinerary"][0]["activities"][1])
    normalized, changes = solve.normalize_seed(initial, "参观公园", optional_visit_digests={digest})
    assert len(solve.activities(normalized)) == len(solve.activities(initial))
    assert not any(change["kind"] == "omit_optional_zero_room_visit" for change in changes)


@pytest.mark.parametrize("path", ["0/1", (0,), (True, 1), (-1, 1), (0, 1, 2)])
def test_invalid_optional_path_is_rejected_before_verification(monkeypatch, path):
    local = Local()
    setup_search(monkeypatch, local)
    with pytest.raises(ValueError):
        run_search(single_day(), optional_visit_paths=(path,))
    assert local.calls == []
