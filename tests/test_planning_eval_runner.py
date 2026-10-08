"""Protect paired evaluation fairness, request accounting and failure handling."""
from types import SimpleNamespace as NS
import json

import pytest

from benchmarks.run_planning_eval import (
    Recorder, RequestFailure, SETTINGS, SYSTEM, canonical, contract_errors,
    response_kind, run_case, self_review_prompt, usage_metrics, write_json,
)
from resimind.domains.planning import ACTION, EVIDENCE_IDS, TARGET, demo_problem


def proposal(*, corrected):
    mode = "transit" if corrected else "walk"
    payload = {
        "stops": [{"place_id": p, "start_minute": s, "end_minute": e}
                  for p, s, e in (("gallery", 600, 660), ("noodles", 690, 735), ("library", 750, 810))],
        "legs": [{"route_id": r, "depart_minute": d} for r, d in (
            (f"station:gallery:{mode}", 580), ("gallery:noodles:walk", 660),
            ("noodles:library:walk", 735), (f"library:station:{mode}", 810))],
        "total_cost_cents": 6100 if corrected else 5500,
        "total_walking_minutes": 22 if corrected else 50,
    }
    return canonical(dict(id="model-plan", action=ACTION, target=TARGET, claim=canonical(payload), refs=list(EVIDENCE_IDS)))


class FakeClient:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.requests = []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return NS(model="fake-model", usage=NS(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                  choices=[NS(finish_reason="stop", message=NS(content=answer, tool_calls=None, refusal=None))])


def case():
    return NS(case_id="blind-01", problem=demo_problem())


def test_same_initial_and_no_external_feedback_to_self_review(tmp_path):
    bad, good = proposal(corrected=False), proposal(corrected=True)
    client = FakeClient([bad, bad, bad, good])
    recorder = Recorder(tmp_path, SETTINGS, client)
    result = run_case(0, case(), recorder)
    arms = result["arms"]
    assert len(client.requests) == 4
    assert arms["direct"]["final_text"] == arms["self_review"]["final_text"] == bad
    assert arms["resimind"]["agent"]["run_result"]["status"] == "solved"
    assert all(a["request_ids"][0] == "case-01-initial" for a in arms.values())
    assert [len(arms[a]["request_ids"]) for a in ("direct", "self_review", "resimind")] == [1, 3, 2]
    assert all(r["messages"][0]["content"] == SYSTEM for r in client.requests)
    for request in client.requests[1:3]:
        body = json.loads(request["messages"][1]["content"])
        assert body["last_feedback"] is None
        assert body["self_review"]["previous_response"] == bad
        assert "walking_limit_exceeded" not in request["messages"][1]["content"]
    repaired = json.loads(client.requests[-1]["messages"][1]["content"])
    assert repaired["last_feedback"]["reasons"] == ["walking_limit_exceeded"]


def test_initial_success_still_reviews_twice_without_oracle_stopping(tmp_path):
    good = proposal(corrected=True)
    client = FakeClient([good, good, good])
    result = run_case(1, case(), Recorder(tmp_path, SETTINGS, client))
    assert len(client.requests) == 3
    assert len(result["arms"]["resimind"]["request_ids"]) == 1
    assert len(result["arms"]["self_review"]["request_ids"]) == 3
    assert result["arms"]["resimind"]["agent"]["run_result"]["status"] == "solved"


def test_null_shared_response_is_abstention_and_no_additional_requests(tmp_path):
    client = FakeClient(["null"])
    result = run_case(0, case(), Recorder(tmp_path, SETTINGS, client))
    assert len(client.requests) == 1
    assert result["arms"]["direct"]["final_text"] == "null"
    assert result["arms"]["self_review"]["final_text"] == "null"
    assert result["arms"]["resimind"]["explicit_abstention"]
    assert not result["arms"]["resimind"]["technical_error"]


@pytest.mark.parametrize("answer", ["not json", "{\"claim\":false}", "{\"claim\":\"{}\"}", ValueError("SECRET MUST NOT BE LOGGED")])
def test_bad_format_or_transport_does_not_gain_more_attempts_or_count_as_abstention(tmp_path, answer):
    client = FakeClient([answer])
    result = run_case(0, case(), Recorder(tmp_path, SETTINGS, client))
    assert len(client.requests) == 1
    assert result["arms"]["self_review"]["technical_error"]
    assert result["arms"]["resimind"]["technical_error"]
    assert not result["arms"]["resimind"]["explicit_abstention"]
    assert "SECRET MUST NOT BE LOGGED" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))


def test_cached_response_is_not_rebilled_and_changed_prompt_fails(tmp_path):
    client = FakeClient(["null"])
    recorder = Recorder(tmp_path, SETTINGS, client)
    assert recorder("one", "prompt") == recorder("one", "prompt") == "null"
    assert len(client.requests) == 1
    with pytest.raises(RuntimeError, match="differs"):
        recorder("one", "changed")


def test_unknown_or_failed_request_is_not_reissued(tmp_path):
    client = FakeClient([ValueError("transport")])
    recorder = Recorder(tmp_path, SETTINGS, client)
    with pytest.raises(RequestFailure):
        recorder("one", "prompt")
    record = recorder.record("one")
    record["status"] = "in_flight"
    write_json(recorder.path("one"), record)
    with pytest.raises(RequestFailure, match="no retry"):
        recorder("one", "prompt")
    assert len(client.requests) == 1


def test_budget_and_prompt_caps_block_network(tmp_path):
    client = FakeClient(["null"])
    recorder = Recorder(tmp_path, dict(SETTINGS, max_physical_calls=1), client)
    recorder("one", "prompt")
    with pytest.raises(RequestFailure, match="ceiling"):
        recorder("two", "prompt")
    assert len(client.requests) == 1
    assert recorder.record("two")["api_attempted"] is False
    small = Recorder(tmp_path / "small", dict(SETTINGS, max_prompt_bytes=2), client)
    with pytest.raises(RequestFailure, match="byte ceiling"):
        small("three", "prompt")
    assert len(client.requests) == 1
    assert small.record("three")["status"] == "blocked"


def test_resume_preserves_archived_model_seconds(tmp_path):
    bad, good = proposal(corrected=False), proposal(corrected=True)
    client = FakeClient([bad, bad, bad, good])
    recorder = Recorder(tmp_path, SETTINGS, client)
    run_case(0, case(), recorder)
    for path in (tmp_path / "requests").glob("*.json"):
        record = json.loads(path.read_text())
        record["elapsed_seconds"] = 2.0
        write_json(path, record)
    resumed = run_case(0, case(), recorder, persist=False)
    assert len(client.requests) == 4
    assert resumed["arms"]["self_review"]["elapsed_seconds"] >= 6
    assert resumed["arms"]["resimind"]["elapsed_seconds"] >= 4


def test_blocked_request_replays_without_network(tmp_path, monkeypatch):
    from benchmarks import run_planning_eval as runner
    settings = dict(SETTINGS, max_prompt_bytes=2)
    client = FakeClient([])
    recorder = Recorder(tmp_path, settings, client)
    result = run_case(0, case(), recorder)
    assert all(a["technical_error"] for a in result["arms"].values())
    assert not client.requests
    monkeypatch.setattr(runner, "load_frozen", lambda _: ({"settings": settings}, [case()]))
    runner.replay(tmp_path)


def test_missing_usage_is_unknown_not_zero():
    assert usage_metrics([{"usage": None}])["list_price_usd"] is None
    result = usage_metrics([{"usage": {"input_tokens": 100, "output_tokens": 50}}])
    assert result["input_tokens"] == 100 and result["output_tokens"] == 50
    assert result["list_price_usd"] == pytest.approx(0.00009)


def test_duplicate_keys_and_invalid_references_do_not_sneak_through():
    assert response_kind('{"claim":"first","claim":"second"}') == "malformed"
    candidate = json.loads(proposal(corrected=True))
    assert not contract_errors(candidate)
    candidate["refs"] = [EVIDENCE_IDS[0], EVIDENCE_IDS[0]]
    assert "evidence_references" in contract_errors(candidate)
