"""Offline integration: repair budgets and guards must preserve unfinished work."""
from copy import deepcopy
import builtins
import importlib.util
import json
import os
from pathlib import Path
import tarfile

import pytest

from resimind.repair import Edit, RepairProposal, apply_repair, plan_sha256
from experiments.chinatravel_repair.binding import BindingReport
from experiments.chinatravel_repair import repair as loop


UPSTREAM = Path(os.environ.get("CHINATRAVEL_UPSTREAM", "/tmp/resimind-chinatravel-upstream"))
QUERY = {"uid": "offline", "nature_language":
         "[当前位置深圳,目标位置南京,旅行人数1,旅行天数1] 请安排一天旅行。"}


@pytest.fixture
def native_checker(monkeypatch):
    if not UPSTREAM.is_dir() or importlib.util.find_spec("jsonschema") is None:
        pytest.skip("requires pinned ChinaTravel experiment runtime")
    monkeypatch.syspath_prepend(str(UPSTREAM))
    from experiments.chinatravel_repair.checker import LocalContract
    return LocalContract


def tiny_plan():
    return {"start_city": "深圳", "target_city": "南京", "people_number": 1,
            "itinerary": [{"day": 1, "activities": []}]}


def tiny_translation(**updates):
    return {"start_city": "深圳", "target_city": "南京", "people_number": 1,
            "days": 1, "hard_logic_py": ["result=True"], **updates}


def no_proposal(*args, **kwargs):
    pytest.fail("guarded or complete plan must never request repair proposals")


def test_empty_translation_is_unfinished_and_never_proposed(native_checker, monkeypatch):
    monkeypatch.setattr(loop, "propose_repairs", no_proposal)
    plan = tiny_plan()
    result = loop.repair_plan(plan, tiny_translation(hard_logic_py=[]), UPSTREAM,
                              tools=object(), public_query=QUERY)
    assert result["status"] == "unfinished" and result["plan"] is None
    assert result["stop_reason"] == "translation_needs_regeneration"
    assert result["draft"] == plan and result["transactions"] == []


def test_public_header_disagreement_never_gets_repaired_to_wrong_task(native_checker, monkeypatch):
    monkeypatch.setattr(loop, "propose_repairs", no_proposal)
    plan = tiny_plan()
    # Model and candidate agree with each other but contradict the public task.
    plan["people_number"] = 2
    result = loop.repair_plan(plan, tiny_translation(people_number=2), UPSTREAM,
                              tools=object(), public_query=QUERY)
    assert result["status"] == "unfinished" and result["plan"] is None
    assert "translation_disagrees_with_public_header" in result["diagnostics"]["translation_errors"]
    assert result["draft"] == plan and result["transactions"] == []


def test_plan_metadata_mismatch_cannot_be_silently_overwritten(native_checker, monkeypatch):
    monkeypatch.setattr(loop, "propose_repairs", no_proposal)
    plan = tiny_plan()
    plan["people_number"] = 2
    result = loop.repair_plan(plan, tiny_translation(), UPSTREAM, tools=object(), public_query=QUERY)
    assert result["status"] == "unfinished" and result["stop_reason"] == "task_envelope_mismatch"
    assert result["draft"]["people_number"] == 2 and result["transactions"] == []


def test_checker_owns_a_detached_constraint_snapshot(native_checker):
    translation = tiny_translation(hard_logic_py=["result=False"])
    checker = native_checker(translation, UPSTREAM, tools=object(), public_query=QUERY)
    before = checker(tiny_plan())
    translation["hard_logic_py"][0] = "result=True"
    translation["people_number"] = 99
    after = checker(tiny_plan())
    assert before["self_constraint/0"] is False
    assert after == before


@pytest.mark.parametrize("reflection", [["bad"], {}, "bad"])
def test_malformed_reflection_is_unknown_instead_of_crashing(native_checker, reflection):
    checker = native_checker(tiny_translation(reflect_info=reflection), UPSTREAM,
                             tools=object(), public_query=QUERY)
    assert checker(tiny_plan())["translation_valid"] is None


def test_runtime_dsl_failure_stays_unknown(native_checker):
    checker = native_checker(tiny_translation(hard_logic_py=["result=(1/0==1)"]),
                             UPSTREAM, tools=object(), public_query=QUERY)
    assert checker(tiny_plan())["self_constraint/0"] is None


@pytest.mark.parametrize("code", ["result=1", "result='yes'"])
def test_truthy_non_boolean_dsl_result_cannot_pass(native_checker, code):
    checker = native_checker(tiny_translation(hard_logic_py=[code]), UPSTREAM,
                             tools=object(), public_query=QUERY)
    assert checker(tiny_plan())["self_constraint/0"] is None


def test_party_coverage_checks_ticket_and_taxi_capacity_boundaries():
    from experiments.chinatravel_repair.checker import coverage_checks

    plan = {"itinerary": [{"activities": [
        {"type": "attraction", "tickets": 4,
         "transports": [{"mode": "metro", "tickets": 4}, {"mode": "walk"}]},
        {"type": "accommodation", "rooms": 1, "room_type": 4,
         "transports": [{"mode": "taxi", "cars": 1}]},
    ]}]}
    snapshot = deepcopy(plan)
    assert all(value is True for value in coverage_checks(plan, 4).values())
    checks = coverage_checks(plan, 5)
    assert checks["coverage/0/0/activity"] is False
    assert checks["coverage/0/0/route"] is False
    assert checks["coverage/0/1/route"] is False
    assert checks["coverage/0/1/activity"] is True
    assert plan == snapshot
    plan["itinerary"][0]["activities"][0]["tickets"] = 5
    plan["itinerary"][0]["activities"][0]["transports"][0]["tickets"] = 5
    plan["itinerary"][0]["activities"][1]["rooms"] = 2
    plan["itinerary"][0]["activities"][1]["transports"][0]["cars"] = 2
    assert all(value is True for value in coverage_checks(plan, 5).values())


@pytest.mark.parametrize("rooms,beds,expected", [(1, 1, True), (0, 1, False),
    (1, 0, False), (True, 1, False), (1, None, False)])
def test_hotel_bed_count_is_not_guest_capacity(rooms, beds, expected):
    from experiments.chinatravel_repair.checker import coverage_checks

    plan = {"itinerary": [{"activities": [{"type": "accommodation",
        "rooms": rooms, "room_type": beds, "transports": []}]}]}
    assert coverage_checks(plan, 2)["coverage/0/0/activity"] is expected


def test_party_coverage_never_treats_boolean_as_a_ticket():
    from experiments.chinatravel_repair.checker import coverage_checks

    plan = {"itinerary": [{"activities": [{"type": "train", "tickets": True,
            "transports": [{"mode": "metro", "tickets": True}]}]}]}
    assert all(value is False for value in coverage_checks(plan, 1).values())


@pytest.mark.parametrize("activity", [None, {}, {"type": []},
    {"type": "train", "tickets": 2, "transports": None},
    {"type": "train", "tickets": 2, "transports": [None, {}]},
    {"type": "train", "tickets": 2, "transports": [{"mode": ["metro"]}]},
])
def test_schema_invalid_activity_keeps_coverage_keys_without_raising(activity):
    from experiments.chinatravel_repair.checker import coverage_checks

    plan = {"itinerary": [{"activities": [activity]}]}
    checks = coverage_checks(plan, 2)
    assert set(checks) == {"coverage/0/0/activity", "coverage/0/0/route"}
    assert checks["coverage/0/0/route"] is None
    assert all(value is None or type(value) is bool for value in checks.values())


def test_schema_messages_name_missing_fields_and_bound_bad_enum_values(native_checker):
    checker = native_checker(tiny_translation(), UPSTREAM, tools=object(), public_query=QUERY)
    plan = tiny_plan()
    plan["itinerary"][0]["activities"] = [{"type": "wrong" * 500}]
    checks = checker(plan)
    assert checks["schema"] is False
    diagnostics = checker.last_diagnostics["schema"]
    assert any(row["validator"] == "required" and "start_time" in row["message"] for row in diagnostics)
    assert any(row["validator"] == "enum" and row["message_abridged"] for row in diagnostics)
    assert all(len(row["message"]) <= 500 for row in diagnostics)


def test_saved_suzhou_missing_train_endpoints_can_receive_verified_tool_patch(native_checker):
    """Replay only the public candidate/self-translation, never the gold query."""
    from experiments.chinatravel_repair.binding import OfficialTools, propose_repairs

    archive_path = Path(__file__).resolve().parents[2] / (
        "docs/evidence/chinatravel-repair-v1/guarded-live-development.tar.xz")
    if not archive_path.is_file():
        pytest.skip("requires the published guarded development archive")
    prefix = "guarded-live-development/runs/h20241029143447759844/resimind_repair/worker/"
    with tarfile.open(archive_path, "r:xz") as archive:
        # No extraction and no query/gold access; these are exact solver records.
        def read(name):
            with archive.extractfile(prefix + name) as stream:
                return json.load(stream)

        plan = read("raw_candidates/candidate_01.json")["plan"]
        translation = read("self_translation.json")
    snapshot = deepcopy(plan)
    assert any(activity["type"] == "train" and "start" not in activity
               for day in plan["itinerary"] for activity in day["activities"])
    tools = OfficialTools()
    checker = native_checker(translation, UPSTREAM, tools=tools)
    before = checker(plan)
    assert before["schema"] is False
    proposals = propose_repairs(plan, translation, tools=tools)
    index = proposals.proposal_kinds.index("intercity_data")
    result = apply_repair(plan, proposals.proposals[index], evidence_ids=proposals.evidence,
                          verifier=checker, max_edits=128)
    assert result.status == "repaired", result.reason
    assert result.after_checks["schema"] is True
    assert result.before_checks.keys() == result.after_checks.keys()
    assert any(name.startswith("binding/") for name in result.before_checks)
    assert all(result.after_checks[name] is True for name, value in before.items() if value is True)
    assert plan == snapshot


class StubContract:
    """Small stable contract used with the real transaction implementation."""

    predicate = staticmethod(lambda plan: {"fixed": plan["value"] >= 1})

    def __init__(self, *args, **kwargs):
        self.last_diagnostics = {}

    def __call__(self, plan):
        return {"translation_valid": True,
                **{"task/" + key: True for key in ("start_city", "target_city", "people_number", "days")},
                **self.predicate(plan)}


def report(plan, *changes):
    return BindingReport(
        (RepairProposal(plan_sha256(plan), tuple(Edit(path, before, after, ("source",))
                                                for path, before, after in changes)),),
        {"source": {"origin": "deterministic offline fixture"}}, (),
    )


def test_complete_plan_is_preserved_without_even_asking_for_proposals(monkeypatch):
    monkeypatch.setattr(loop, "LocalContract", StubContract)
    monkeypatch.setattr(loop, "propose_repairs", no_proposal)
    original = {"value": 1, "extra": {"untouched": [1, 2]}}
    snapshot = deepcopy(original)
    result = loop.repair_plan(original, {}, Path("unused"), tools=object())
    assert result["status"] == "accepted" and result["transactions"] == []
    assert result["initial_sha256"] == result["draft_sha256"]
    assert original == snapshot and result["plan"] == snapshot
    result["plan"]["extra"]["untouched"].append(3)
    assert original == snapshot


def test_round_budget_keeps_verified_progress_but_withholds_unfinished_plan(monkeypatch):
    class ThreeSteps(StubContract):
        predicate = staticmethod(lambda plan: {f"step/{i}": plan["value"] >= i for i in (1, 2, 3)})

    calls = []

    def propose(plan, *args, **kwargs):
        calls.append(deepcopy(plan))
        return report(plan, ("/value", plan["value"], plan["value"] + 1))

    monkeypatch.setattr(loop, "LocalContract", ThreeSteps)
    monkeypatch.setattr(loop, "propose_repairs", propose)
    original = {"value": 0}
    result = loop.repair_plan(original, {}, Path("unused"), max_rounds=2, tools=object())
    assert result["status"] == "unfinished" and result["plan"] is None
    assert result["stop_reason"] == "repair_budget"
    assert result["draft"] == {"value": 2} and original == {"value": 0}
    assert len(calls) == len(result["transactions"]) == 2
    assert result["resolved"] == ["step/1", "step/2"] and result["regressed"] == []
    assert all(row["result"]["status"] == "repaired" for row in result["transactions"])


def test_regression_rolls_back_before_an_independent_good_patch(monkeypatch):
    class Protected(StubContract):
        predicate = staticmethod(lambda plan: {"fixed": plan["value"] == 1, "protected": plan["keep"] == 1})

    def propose(plan, *args, **kwargs):
        bad = report(plan, ("/value", 0, 1), ("/keep", 1, 0))
        good = report(plan, ("/value", 0, 1))
        return BindingReport(bad.proposals + good.proposals, bad.evidence, ())

    monkeypatch.setattr(loop, "LocalContract", Protected)
    monkeypatch.setattr(loop, "propose_repairs", propose)
    original = {"value": 0, "keep": 1}
    result = loop.repair_plan(original, {}, Path("unused"), tools=object(), max_rounds=1)
    assert result["status"] == "accepted" and result["plan"] == {"value": 1, "keep": 1}
    assert [row["result"]["status"] for row in result["transactions"]] == ["rejected", "accepted"]
    assert result["transactions"][0]["result"]["plan"] == original
    assert original == {"value": 0, "keep": 1} and result["regressed"] == []


def test_checker_and_loop_do_not_import_official_gold_scoring(native_checker, monkeypatch):
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.startswith("chinatravel.evaluation"), "solver imported official gold scorer"
        assert name != "experiments.chinatravel.official_scoring", "solver imported offline gold scorer"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    # An invalid translation must still produce an auditable unfinished result.
    result = loop.repair_plan(tiny_plan(), tiny_translation(hard_logic_py=[]), UPSTREAM,
                              tools=object(), public_query=QUERY)
    assert result["status"] == "unfinished"
