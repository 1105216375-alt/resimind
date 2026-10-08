"""Broker-only integration: translation ordering, fail-closed repair, budgets."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path

import pytest

from experiments.chinatravel_repair import live_worker as module


UPSTREAM = Path(os.environ.get("CHINATRAVEL_UPSTREAM", "/tmp/resimind-chinatravel-upstream"))
QUERY = {"uid": "offline", "nature_language": "[当前位置上海,目标位置杭州,旅行人数1,旅行天数1] 游玩一天。"}
SETTINGS = {"model": "deepseek-chat", "max_output_tokens": 128, "max_proposals": 3}
TRANSLATION = [json.dumps({"start_city": "上海", "target_city": "杭州", "days": 1,
                           "people_number": 1, "hard_logic": ["一个人"]}, ensure_ascii=False),
               '["result = people_count(plan) == 1"]']
FIRST = ["生成计划。", "plan('当前任务')", '{"people_number":1}']


class FakeBroker:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.requests = []
        self.result = None

    def send(self, packet):
        packet = deepcopy(packet)
        if packet["type"] == "result":
            self.result = packet
        else:
            self.requests.append(packet)

    def recv(self):
        answer = next(self.answers)
        if type(answer) is dict:
            return answer
        return {"status": "complete", "message": {"role": "assistant", "content": answer},
                "usage": {"prompt_tokens": 7, "completion_tokens": 2}}


@pytest.fixture
def upstream():
    if not UPSTREAM.is_dir() or any(importlib.util.find_spec(x) is None for x in
                                   ("pandas", "geopy", "jsonschema", "sklearn")):
        pytest.skip("requires pinned local ChinaTravel checkout and experiment venv")
    return str(UPSTREAM)


def execute(tmp_path, upstream, answers, settings=None):
    query_path = tmp_path / "public.json"
    query_path.write_text(json.dumps(QUERY, ensure_ascii=False))
    output = tmp_path / "worker"
    broker = FakeBroker(answers)
    module.worker(broker, upstream, str(query_path), str(output),
                  SETTINGS if settings is None else settings, "sandbox", module.ARM)
    assert broker.result is not None
    assert broker.result == json.loads((output / "result.json").read_text())
    return broker, output


def repair_result(plan, *, accepted=False, draft=None):
    draft = deepcopy(plan if draft is None else draft)
    return {"status": "accepted" if accepted else "unfinished", "draft": draft,
            "plan": draft if accepted else None, "final_checks": {"local": accepted},
            "diagnostics": {"environment": {}}, "stop_reason": "test"}


def test_translation_precedes_generation_and_raw_is_separate(tmp_path, upstream, monkeypatch):
    seen = []

    def repair(plan, translation, root, **kwargs):
        seen.append((deepcopy(plan), deepcopy(translation), kwargs))
        return repair_result(plan, accepted=True, draft={**plan, "bound": True})

    monkeypatch.setattr(module, "repair_plan", repair)
    broker, output = execute(tmp_path, upstream, TRANSLATION + FIRST)
    assert broker.result["status"] == "completed"
    assert broker.result["plan"] == {"people_number": 1, "bound": True}
    assert len(broker.requests) == 5
    assert broker.result["details"]["worker_usage"]["prompt_tokens"] == 35
    assert "hard_logic" in broker.requests[0]["messages"][0]["content"]
    assert seen[0][1]["start_city"] == "上海"
    assert seen[0][2] == {"max_rounds": 3, "public_query": QUERY}
    assert json.loads((output / "raw_candidates/candidate_01.json").read_text())["plan"] == {"people_number": 1}
    assert (output / "repair_01.json").exists()


def test_unfinished_repairs_continue_without_reset_or_delivery(tmp_path, upstream, monkeypatch):
    monkeypatch.setattr(module, "repair_plan", lambda plan, *a, **k: repair_result(plan))
    broker, output = execute(tmp_path, upstream, TRANSLATION + FIRST * 3)
    assert broker.result["status"] == "completed" and broker.result["plan"] is None
    assert broker.result["details"]["resimind_stop_reason"] == "max_proposals"
    assert len(broker.requests) == 11
    state = json.loads((output / "react_state.json").read_text())
    assert state["cur_step"] == 3 and state["max_steps"] == 50
    assert len(list(output.glob("repair_*.json"))) == 3
    assert len(list((output / "raw_candidates").glob("candidate_*.json"))) == 3


@pytest.mark.parametrize("checks", [{}, {"local": None}, {"local": False}])
def test_status_label_cannot_override_failed_or_unknown_checks(tmp_path, upstream, monkeypatch, checks):
    def repair(plan, *a, **k):
        result = repair_result(plan, accepted=True)
        result["final_checks"] = checks
        return result
    monkeypatch.setattr(module, "repair_plan", repair)
    broker, _ = execute(tmp_path, upstream, TRANSLATION + FIRST,
                        {**SETTINGS, "max_proposals": 1})
    assert broker.result["plan"] is None


def test_translation_failure_stops_before_generation(tmp_path, upstream, monkeypatch):
    def invalid(self):
        self.translation = {"start_city": "上海", "target_city": "杭州", "people_number": 1, "days": 1}
        self.translation_reasons = ["translation_constraints_empty"]
    monkeypatch.setattr(module._LocalGate, "_translate", invalid)
    broker, output = execute(tmp_path, upstream, [])
    assert broker.requests == [] and broker.result["plan"] is None
    assert broker.result["details"]["resimind_stop_reason"] == "translation_needs_regeneration"
    assert (output / "translation_stop.json").exists()
    assert not (output / "raw_candidates").exists()


def test_translation_must_match_explicit_public_header(tmp_path, upstream, monkeypatch):
    def mismatched(self):
        self.translation = {"start_city": "北京", "target_city": "南京", "people_number": 1, "days": 1}
        self.translation_reasons = []
    monkeypatch.setattr(module._LocalGate, "_translate", mismatched)
    broker, _ = execute(tmp_path, upstream, [])
    assert broker.requests == [] and broker.result["plan"] is None
    assert "translation_public_header_mismatch:start_city" in broker.result["details"]["translation_reasons"]


def test_broker_budget_stop_never_returns_previous_draft(tmp_path, upstream, monkeypatch):
    monkeypatch.setattr(module, "repair_plan", lambda plan, *a, **k: repair_result(plan))
    broker, output = execute(tmp_path, upstream, TRANSLATION + FIRST + [{"status": "call_limit"}])
    assert broker.result["status"] == "budget_exhausted" and broker.result["plan"] is None
    assert len(broker.requests) == 6
    assert (output / "repair_01.json").exists()
    assert not (output / "raw_candidates/candidate_02.json").exists()


def test_system_exit_fails_closed_and_records_terminal(tmp_path, upstream, monkeypatch):
    def stopping(*args, **kwargs):
        raise SystemExit(9)
    monkeypatch.setattr(module, "repair_plan", stopping)
    broker, _ = execute(tmp_path, upstream, TRANSLATION + FIRST)
    assert broker.result["status"] == "technical_failure" and broker.result["plan"] is None
    assert broker.result["details"]["exception_class"] == "SystemExit"
