"""Offline regression coverage for task anchoring and unchanged broker budgets."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path

import pytest

from experiments.chinatravel_repair.proposals import (
    RepairingReActProposer, build_plan_prompt, compact_feedback, public_task_envelope,
)


QUERY = {"uid": "offline", "nature_language":
         "[当前位置广州,目标位置武汉,旅行人数3,旅行天数4] 交通便利，行程舒适。"}
SCHEMA = {"type": "object", "properties": {"itinerary": {"type": "array"}}}
UPSTREAM = Path(os.environ.get("CHINATRAVEL_UPSTREAM", "/tmp/resimind-chinatravel-upstream"))


def test_public_header_only_and_no_gold():
    header = public_task_envelope(QUERY)["explicit_header"]
    assert header == {"start_city": "广州", "target_city": "武汉", "people_number": 3, "days": 4}
    assert "explicit_header" not in public_task_envelope({"uid": "x", "nature_language": "去武汉"})
    with pytest.raises(ValueError, match="public_query"):
        public_task_envelope({**QUERY, "hard_logic_py": ["result=True"]})


def test_schema_only_prompt_corrects_cost_and_anchors_original_last():
    text = build_plan_prompt(QUERY, SCHEMA, notebook="tool evidence", action_query="untrusted replacement")
    assert "FL159" not in text and "南京金陵饭店" not in text
    assert "cost = price * tickets" in text and "cost = price * people_number" in text
    assert "cost = price * rooms" in text and "cost = price * cars" in text
    assert text.endswith(QUERY["nature_language"])
    assert text.index("tool evidence") < text.index("untrusted replacement") < text.rindex("ORIGINAL REQUIREMENT")


def test_both_initial_and_repair_prompts_require_explicit_hotel_overnights():
    for feedback in (None, {"decision": "reject", "previous_plan": {},
                             "reasons": ["missing_overnight"]}):
        text = build_plan_prompt(QUERY, SCHEMA, feedback)
        assert "Every nonfinal day must explicitly return by an evidenced route" in text
        assert "accommodation activity whose end_time is 24:00" in text
        assert "next day's route from that same hotel" in text
        assert "A morning check-in alone does not" in text
        assert "yesterday's restaurant directly to this morning's breakfast" in text
        assert "leave the task unfinished rather than inventing a route" in text
        assert text.endswith(QUERY["nature_language"])


def test_feedback_keeps_plan_and_reports_abridgement_without_mutation():
    previous = {"target_city": "武汉", "itinerary": []}
    feedback = {"previous_plan": previous, "decision": "reject", "reasons": ["route_failed"],
                "diagnostics": {"environment": {
                    "passed": {"passed": True, "details": []},
                    "failed": {"passed": False, "counts": {"good": 0, "bad": 1},
                               "details": ["x" * 600] * 9}}}}
    original = deepcopy(feedback)
    result = compact_feedback(feedback)
    assert feedback == original and result["previous_plan"] == previous
    checks = result["diagnostics"]["environment"]
    assert "passed" not in checks
    assert checks["failed"]["failed_counts"] == {"bad": 1}
    assert checks["failed"]["omitted_detail_count"] == 3
    assert checks["failed"]["details_abridged"] is True


@pytest.fixture
def native_agent(monkeypatch):
    if not UPSTREAM.is_dir() or importlib.util.find_spec("json_repair") is None:
        pytest.skip("requires pinned ChinaTravel experiment venv")
    monkeypatch.syspath_prepend(str(UPSTREAM))
    from chinatravel.agent.pure_neuro_agent.pure_neuro_agent import ReActAgent
    from chinatravel.agent.pure_neuro_agent.prompts import ONESHOT_REACT_INSTRUCTION
    return ReActAgent, ONESHOT_REACT_INSTRUCTION


class StubModel:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append((deepcopy(messages), kwargs))
        return next(self.answers)


class StubEnvironment:
    def reset(self):
        pass

    def __call__(self, action):
        return "No data."


def test_real_upstream_continuation_keeps_steps_and_raw_answers(tmp_path, native_agent):
    cls, prompt = native_agent
    first = {"start_city": "广州", "target_city": "武汉", "people_number": 3}
    # Even an unrelated response is recorded unchanged and left to the gate.
    wrong = {"start_city": "北京", "target_city": "南京", "people_number": 1}
    llm = StubModel(["Think", "plan('original task')", json.dumps(first),
                     "Repair", "plan('replace task')", json.dumps(wrong)])
    agent = cls(StubEnvironment(), llm, prompt, max_steps=2, debug=False)
    proposer = RepairingReActProposer(agent, QUERY, tmp_path, SCHEMA)
    assert proposer(None) == first
    feedback = {"previous_plan": first, "decision": "reject", "reasons": ["missing_itinerary"]}
    assert proposer(feedback) == wrong
    assert agent.cur_step == agent.max_steps == 2
    assert proposer(feedback) is None  # No hidden reset/new budget.
    assert len(llm.calls) == 6
    final_calls = [call for call in llm.calls if call[1].get("json_mode")]
    assert len(final_calls) == 2
    for messages, options in final_calls:
        assert messages[0]["content"].endswith(QUERY["nature_language"])
        assert "FL159" not in messages[0]["content"]
        assert options == {"json_mode": True, "one_line": False}
    recorded = json.loads((tmp_path / "candidate_02.json").read_text())
    assert recorded["plan"] == wrong and json.loads(recorded["raw_answer"]) == wrong
    assert json.loads((tmp_path / "candidate_03.json").read_text())["plan"] is None
