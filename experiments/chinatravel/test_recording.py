"""No provider traffic: validate hard budgets and failed-request accounting."""
from types import SimpleNamespace as NS
import json
import threading
import time
from . import recording
from .recording import Recorder


SETTINGS = dict(model="fixture", max_output_tokens=8, max_calls=2,
                max_request_prompt_bytes=1000, max_total_prompt_bytes=2000,
                max_total_output_tokens=16, request_timeout_seconds=10)


def setup(tmp_path, create, **overrides):
    client = NS(chat=NS(completions=NS(create=create)))
    return Recorder(tmp_path / "requests", SETTINGS | overrides, client, time.monotonic() + 10)


def response(**kwargs):
    return NS(model="fixture", usage=NS(model_dump=lambda **_: dict(prompt_tokens=3, completion_tokens=8)),
              choices=[NS(finish_reason="stop", message=NS(content="{}"))])


REQUEST = dict(messages=[dict(role="user", content="query")], request_args={})


def test_budget_no_dispatch(tmp_path):
    recorder = setup(tmp_path, response)
    assert recorder(REQUEST)["status"] == "complete"
    assert recorder(REQUEST)["status"] == "complete"
    assert recorder(REQUEST)["status"] == "budget_exhausted"
    assert recorder.calls == 2
    assert not json.loads((recorder.folder / "003.json").read_text())["api_attempted"]


def test_output_reservation(tmp_path):
    recorder = setup(tmp_path, response, max_total_output_tokens=9)
    assert recorder(REQUEST)["status"] == "complete"
    assert recorder(REQUEST)["code"] == "output_reservation_limit"
    assert recorder.calls == 1


def test_prompt_and_configuration(tmp_path):
    recorder = setup(tmp_path, response, max_request_prompt_bytes=2)
    assert recorder(REQUEST)["code"] == "request_prompt_limit"
    assert recorder.calls == 0
    other = setup(tmp_path / "other", response)
    assert other(REQUEST | {"request_args": {"model": "different"}})["status"] == "technical_failure"
    assert other.calls == 0


def test_failed_request_is_recorded_and_never_retried(tmp_path):
    def fail(**kwargs):
        before = json.loads((tmp_path / "requests/001.json").read_text())
        assert before["status"] == "inflight"
        raise RuntimeError("private error body MUST NOT be archived")
    recorder = setup(tmp_path, fail)
    assert recorder(REQUEST)["status"] == "technical_failure"
    assert recorder(REQUEST)["status"] == "budget_exhausted"
    assert recorder.calls == 1
    assert "private error" not in (recorder.folder / "001.json").read_text()


def test_real_total_timeout_discards_late_result_without_retry(tmp_path):
    dispatched, release, finished = (threading.Event() for _ in range(3))
    attempts = []

    def slow(**kwargs):
        attempts.append(kwargs)
        dispatched.set()
        assert release.wait(timeout=2)
        finished.set()
        return response()

    recorder = setup(tmp_path, slow, request_timeout_seconds=0.03)
    started = time.monotonic()
    try:
        result = recorder(REQUEST)
        elapsed = time.monotonic() - started
        assert dispatched.is_set()
        assert result["status"] == "technical_failure"
        assert elapsed < 0.5  # Total deadline, not a blocking SDK call timeout.
        assert recorder.totals()["api_attempts"] == 1
        assert recorder.totals()["unknown_usage_requests"] == 1
        archived = (recorder.folder / "001.json").read_bytes()
    finally:
        release.set()
        assert finished.wait(timeout=1)
    assert len(attempts) == 1
    assert (recorder.folder / "001.json").read_bytes() == archived
    assert recorder(REQUEST)["code"] == "prior_failure"
    assert len(attempts) == recorder.calls == 1


def test_total_deadline_rechecks_elapsed_when_queue_wakeup_is_late(tmp_path, monkeypatch):
    original_queue = recording.queue.Queue

    class LateWakeupQueue(original_queue):
        def get(self, *args, **kwargs):
            # The SDK result can already be queued while this thread is
            # descheduled past its deadline. Queue.get alone accepts that item.
            time.sleep(0.06)
            return super().get(*args, **kwargs)

    monkeypatch.setattr(recording.queue, "Queue", LateWakeupQueue)
    recorder = setup(tmp_path, response, request_timeout_seconds=0.02)
    result = recorder(REQUEST)
    assert result["status"] == "technical_failure"
    assert recorder.calls == 1
    assert "message" not in result
    assert recorder(REQUEST)["code"] == "prior_failure"


def test_incomplete_response_retains_actual_usage_and_stops(tmp_path):
    def truncated(**kwargs):
        result = response()
        result.choices[0].finish_reason = "length"
        return result

    recorder = setup(tmp_path, truncated)
    assert recorder(REQUEST)["code"] == "incomplete_response"
    assert recorder.calls == 1 and recorder.prompt_tokens == 3
    assert recorder.completion_tokens == 8 and recorder.unknown_usage_requests == 0
    assert recorder(REQUEST)["code"] == "prior_failure"
    assert recorder.calls == 1
