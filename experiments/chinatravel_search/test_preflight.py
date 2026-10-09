from copy import deepcopy

import pytest

from experiments.chinatravel_search.preflight import preflight_translation


TRANSPORT = """innercity_transport_set = set()
for activity in allactivities(plan):
  innercity_transport_set.add(innercity_transport_type(activity_transports(activity)))
result=(innercity_transport_set<={'taxi'})"""

ROOMS = """result=True
for activity in allactivities(plan):
  if activity_type(activity)=='accommodation' and room_count(activity)!=1: result=False
  if activity_type(activity)=='accommodation' and room_type(activity)!=1: result=False"""


def inspect(*codes, **kwargs):
    return preflight_translation({"hard_logic_py": list(codes)}, **kwargs)


def test_real_self_translation_shape_requests_retranslation_without_rewriting():
    source = {"people_number": 2, "hard_logic_py": [ROOMS, TRANSPORT],
              "hard_logic": ["rooms==1", "room_type==1", "transport_type<={'taxi'}"]}
    before = deepcopy(source)
    result = preflight_translation(source)
    assert source == before
    assert result["status"] == "needs_retranslation"
    assert result["needs_retranslation"] is True
    assert len(result["issues"]) == 1
    issue = result["issues"][0]
    assert issue["constraint_index"] == 1
    assert issue["action"] == "needs_retranslation"
    assert issue["source"] == TRANSPORT
    assert issue["evidence"]["required_initial_value"] == "empty"
    assert issue["evidence"]["allowed_modes"] == ["taxi"]
    assert result["unknown_constraint_indices"] == []


@pytest.mark.parametrize("collector,activity", [
    ("modes", "leg"), ("集合", "事件"), ("x", "y"),
])
def test_collectors_and_loop_variables_are_identified_by_data_flow(collector, activity):
    code = TRANSPORT.replace("innercity_transport_set", collector)
    # Only variable identifiers change; function names retain their semantics.
    code = code.replace("for activity", "for " + activity).replace("(activity)", "(" + activity + ")")
    issue = inspect(code)["issues"][0]
    assert issue["evidence"]["collector_variable"] == collector
    assert issue["evidence"]["activity_variable"] == activity


@pytest.mark.parametrize("allowed", ["{'taxi', 'empty'}", "{'empty'}"])
def test_explicit_empty_allowance_is_recognized_without_a_contradiction(allowed):
    result = inspect(TRANSPORT.replace("{'taxi'}", allowed))
    assert result["recognized_constraint_indices"] == [0]
    assert result["needs_retranslation"] is False


def test_domain_assumption_is_explicit_and_can_be_disabled():
    result = inspect(TRANSPORT, first_activity_has_empty_transports=False)
    assert result["recognized_constraint_indices"] == [0]
    assert result["issues"] == []
    with pytest.raises(TypeError):
        inspect(TRANSPORT, first_activity_has_empty_transports=1)


@pytest.mark.parametrize("code", [
    # A filtered collection need not contain the first activity's empty route.
    TRANSPORT.replace("  innercity_transport_set.add", "  if activity_transports(activity):\n    innercity_transport_set.add"),
    TRANSPORT.replace("allactivities(plan)", "dayactivities(plan, 1)"),
    TRANSPORT.replace("activity_transports(activity)", "activity_transports(other)"),
    TRANSPORT.replace("  innercity_transport_set.add", "  break\n  innercity_transport_set.add"),
    TRANSPORT.replace("result=", "innercity_transport_set.clear()\nresult="),
    TRANSPORT + "\nresult=True",
    TRANSPORT.replace("result=", "innercity_transport_set={'taxi'}\nresult="),
    TRANSPORT.replace("innercity_transport_set = set()", "innercity_transport_set = set()\nalias=innercity_transport_set"),
    TRANSPORT.replace("result=", "innercity_transport_set.discard('empty')\nresult="),
    TRANSPORT.replace("innercity_transport_set<=", "other<="),
    TRANSPORT.replace("innercity_transport_set.add", "other.add"),
    TRANSPORT.replace("set()", "set(['taxi'])"),
    TRANSPORT.replace("{'taxi'}", "allowed"),
    TRANSPORT.replace("{'taxi'}", "{'taxi', dynamic_value}"),
    TRANSPORT.replace("<=", "=="),
    TRANSPORT.replace("innercity_transport_set", "activity"),
    TRANSPORT.replace("innercity_transport_set", "set"),
    TRANSPORT.replace("for activity", "for plan").replace("(activity)", "(plan)"),
    "set=lambda: {'taxi'}\n" + TRANSPORT,
    "result={innercity_transport_type(activity_transports(a)) for a in allactivities(plan)}<={'taxi'}",
])
def test_unknown_or_modified_data_flow_is_never_a_false_contradiction(code):
    result = inspect(code)
    assert result["issues"] == []
    assert result["needs_retranslation"] is False
    assert result["unknown_constraint_indices"] == [0]


def test_room_requirements_are_positive_decisions_not_bed_capacity():
    source = {"people_number": 2, "hard_logic_py": [ROOMS]}
    result = preflight_translation(source)
    assert result["issues"] == []
    assert result["needs_retranslation"] is False
    assert result["room_requirements"] == [
        {"constraint_index": 0, "field": "rooms", "value": 1,
         "scope": "all_accommodation_activities"},
        {"constraint_index": 0, "field": "room_type", "value": 1,
         "scope": "all_accommodation_activities"},
    ]


def test_room_guard_variable_and_symmetric_comparisons_are_understood():
    code = """result=True
for stay in allactivities(plan):
  if 2 != room_type(stay) and 'accommodation' == activity_type(stay): result=False
  if activity_type(stay)=='accommodation' and 3 != room_count(stay): result=False"""
    result = inspect(code)
    assert [(x["field"], x["value"]) for x in result["room_requirements"]] == [
        ("room_type", 2), ("rooms", 3)]


@pytest.mark.parametrize("code", [
    ROOMS.replace("!=1", "!=0"),
    ROOMS.replace("!=1", "!=-1"),
    ROOMS.replace("!=1", "!=True"),
    ROOMS.replace("!=1", "!=1.0"),
    ROOMS.replace("!=1", "!=room_preference"),
    ROOMS.replace(" and ", " or "),
    ROOMS.replace("room_type(activity)", "room_type(other)"),
    ROOMS.replace("allactivities(plan)", "subset"),
    ROOMS.replace("result=True", "result=False"),
    ROOMS + "\nresult=True",
    ROOMS.replace("result=False", "result=True"),
    ROOMS.replace("'accommodation'", "'attraction'"),
])
def test_unknown_room_programs_do_not_create_requirements_or_capacity_rules(code):
    result = inspect(code)
    assert result["room_requirements"] == []
    assert result["issues"] == []
    assert result["unknown_constraint_indices"] == [0]


def test_does_not_execute_untrusted_source(tmp_path):
    sentinel = tmp_path / "must-not-exist"
    code = f"__import__('pathlib').Path({str(sentinel)!r}).write_text('executed')"
    result = inspect(code, "result = (lambda: 1 / 0)()", "this is not valid Python!")
    assert not sentinel.exists()
    assert result["issues"] == []
    assert result["unknown_constraint_indices"] == [0, 1, 2]
    assert result["diagnostics"] == [{"constraint_index": 2, "reason": "unparseable_source"}]


def test_only_current_constraint_sources_are_inspected():
    source = {"hard_logic_py": [ROOMS], "hard_logic": [TRANSPORT],
              "hard_logic_py_iter_3": [TRANSPORT], "reflect_info": [{"hard_logic_py": [TRANSPORT]}]}
    assert preflight_translation(source)["issues"] == []


@pytest.mark.parametrize("translation", [None, {}, {"hard_logic_py": []},
    {"hard_logic_py": TRANSPORT}, {"hard_logic_py": ["result=True"] * 129}])
def test_invalid_or_excessive_lists_are_unassessed(translation):
    result = preflight_translation(translation)
    assert result["status"] == "unassessed"
    assert result["issues"] == []


def test_source_and_ast_limits_are_reported_as_unknown():
    result = inspect("#" + "x" * 65_536, "result=[" + ",".join(["0"] * 5_000) + "]", None)
    assert result["unknown_constraint_indices"] == [0, 1, 2]
    assert [x["reason"] for x in result["diagnostics"]] == [
        "source_limit", "ast_limit", "constraint_not_nonempty_source"]
    assert result["issues"] == []
