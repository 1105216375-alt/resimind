"""Exercise live-run audit and budget behavior without any network requests."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace as NS

import pytest

from benchmarks.live_eval_support import RecordedCompletion, RequestFailure, write_json


SETTINGS = dict(model="fake-model", max_output_tokens=128, max_physical_calls=4,
                max_prompt_bytes=4096, timeout_seconds=10, temperature=0.0, thinking="disabled")


def response(text="null", *, finish="stop", usage=True):
    return NS(model="returned-version", id="response-1",
              usage=NS(prompt_tokens=11, completion_tokens=7, total_tokens=18) if usage else None,
              choices=[NS(finish_reason=finish, message=NS(content=text, tool_calls=None,
                                                        function_call=None, refusal=None))])


class Client:
    def __init__(self, answers=None):
        self.answers = list(answers) if answers is not None else None
        self.requests = []
        self.options = []
        self.lock = threading.Lock()
        self.chat = NS(completions=NS(create=self.create))

    def with_options(self, **options):
        self.options.append(options)
        return self

    def create(self, **request):
        with self.lock:
            self.requests.append(request)
            answer = self.answers.pop(0) if self.answers is not None else response()
        if isinstance(answer, Exception):
            raise answer
        return answer


def recorder(folder, client=None, **settings):
    return RecordedCompletion(folder, dict(SETTINGS, **settings), client, "return protocol JSON")


def test_bind_records_model_tokens_and_returns_public_safe_filtered_summary(tmp_path):
    client = Client([response("PRIVATE_RESPONSE"), response()])
    audit = recorder(tmp_path, client)
    arm = audit.bind("case-1-arm-a")
    assert arm("PRIVATE_PROMPT") == "PRIVATE_RESPONSE"
    assert arm.calls == 1 and arm.request_ids == ["case-1-arm-a-001"]
    audit("another-arm", "another prompt")
    summary = audit.compact_summary(arm.request_ids)
    assert summary["physical_calls"] == 1
    assert summary["model_returned"] == ["returned-version"]
    assert summary["usage"] == dict(input_tokens=11, output_tokens=7, total_tokens=18)
    assert "PRIVATE" not in json.dumps(summary)
    assert audit.record(arm.request_ids[0])["response_text"] == "PRIVATE_RESPONSE"
    assert audit.record(arm.request_ids[0])["finish_reason"] == "stop"
    assert client.options == [dict(timeout=10, max_retries=0)]
    assert client.requests[0]["temperature"] == 0
    assert client.requests[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_successful_replay_is_free_and_changed_prompt_or_settings_fail(tmp_path):
    client = Client()
    audit = recorder(tmp_path, client)
    audit("one", "prompt")
    offline = recorder(tmp_path)
    assert offline("one", "prompt") == "null"
    assert len(client.requests) == 1
    with pytest.raises(RequestFailure, match="differs"):
        offline("one", "changed")
    with pytest.raises(RequestFailure, match="differs"):
        recorder(tmp_path, max_output_tokens=64)("one", "prompt")
    with pytest.raises(RequestFailure, match="unrecorded"):
        offline("missing", "prompt")
    assert not offline.path("missing").exists()


def test_transport_exception_is_sanitized_and_cannot_be_retried(tmp_path):
    client = Client([ValueError("PRIVATE TOKEN MUST NEVER APPEAR")])
    audit = recorder(tmp_path, client)
    with pytest.raises(RequestFailure):
        audit("one", "prompt")
    with pytest.raises(RequestFailure, match="no retry"):
        audit("one", "prompt")
    assert len(client.requests) == 1
    assert "PRIVATE TOKEN" not in audit.path("one").read_text()
    assert audit.compact_summary()["usage"]["output_tokens"] is None
    assert audit.compact_summary()["failed_requests"] == 1


def test_crashed_in_flight_request_consumes_budgets_and_never_reissues(tmp_path):
    client = Client()
    audit = recorder(tmp_path, client, max_physical_calls=1)
    audit("one", "prompt")
    old = audit.record("one")
    old.update(status="in_flight", response_text=None, usage=None)
    write_json(audit.path("one"), old)
    with pytest.raises(RequestFailure, match="no retry"):
        audit("one", "prompt")
    with pytest.raises(RequestFailure, match="call ceiling"):
        audit("two", "prompt")
    assert len(client.requests) == 1
    assert audit.compact_summary()["in_flight_requests"] == 1


@pytest.mark.parametrize("mutate,error", [
    (lambda r: setattr(r.choices[0], "finish_reason", "length"), "Incomplete"),
    (lambda r: setattr(r.choices[0].message, "tool_calls", ["call"]), "tool call"),
    (lambda r: setattr(r.choices[0].message, "refusal", "refused"), "refusal"),
    (lambda r: setattr(r.choices[0].message, "content", ""), "No completion"),
    (lambda r: setattr(r, "choices", []), "exactly one"),
])
def test_unusable_response_retains_usage_and_fails_closed(tmp_path, mutate, error):
    answer = response("partial content")
    mutate(answer)
    audit = recorder(tmp_path, Client([answer]))
    with pytest.raises(RequestFailure, match=error):
        audit("one", "prompt")
    record = audit.record("one")
    assert record["status"] == "failed" and record["usage"]["output_tokens"] == 7
    assert audit.compact_summary()["physical_calls"] == 1


def test_missing_or_invalid_usage_is_unknown_not_zero(tmp_path):
    answer = response(usage=False)
    audit = recorder(tmp_path, Client([answer]))
    audit("one", "prompt")
    assert audit.compact_summary()["usage"] == dict(input_tokens=None, output_tokens=None, total_tokens=None)


def test_output_reservation_counts_full_limit_after_short_and_failed_requests(tmp_path):
    client = Client([response(), ValueError("network")])
    settings = dict(max_physical_calls=99, max_total_output_tokens=256)
    audit = recorder(tmp_path, client, **settings)
    audit("one", "prompt")
    with pytest.raises(RequestFailure):
        audit("two", "prompt")
    resumed = recorder(tmp_path, client, **settings)
    with pytest.raises(RequestFailure, match="output token ceiling"):
        resumed("three", "prompt")
    assert len(client.requests) == 2
    assert resumed.compact_summary()["reserved_output_tokens"] == 256
    assert resumed.record("three")["api_attempted"] is False


def test_prompt_size_is_utf8_bytes_and_blocks_before_api(tmp_path):
    client = Client()
    audit = RecordedCompletion(tmp_path, dict(SETTINGS, max_prompt_bytes=5), client, "a")
    with pytest.raises(RequestFailure, match="Prompt byte ceiling"):
        audit("one", "数学")
    assert not client.requests
    assert audit.record("one")["prompt_bytes"] == 7
    assert audit.compact_summary()["usage"]["total_tokens"] == 0


def test_concurrent_threads_and_recorders_share_physical_call_reservations(tmp_path):
    client = Client()
    a = recorder(tmp_path, client, max_physical_calls=3)
    b = recorder(tmp_path, client, max_physical_calls=3)

    def run(index):
        try:
            return (a if index % 2 else b)(f"request-{index}", "prompt")
        except RequestFailure:
            return "blocked"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(run, range(20)))
    assert outcomes.count("null") == 3
    assert len(client.requests) == 3
    assert a.compact_summary()["physical_calls"] == 3
    assert a.compact_summary()["blocked_requests"] == 17


def test_same_in_flight_id_does_not_duplicate_request(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    client = Client()
    original = client.create

    def delayed(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)

    client.chat.completions.create = delayed
    audit = recorder(tmp_path, client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(audit, "same", "prompt")
        assert entered.wait(5)
        try:
            with pytest.raises(RequestFailure, match="no retry"):
                audit("same", "prompt")
        finally:
            release.set()
        assert first.result() == "null"
    assert len(client.requests) == 1


@pytest.mark.parametrize("identifier", ["../escape", "/absolute", ".", "", "bad/id", "x" * 161])
def test_request_id_cannot_escape_local_directory(tmp_path, identifier):
    with pytest.raises(ValueError, match="identifier"):
        recorder(tmp_path).path(identifier)
