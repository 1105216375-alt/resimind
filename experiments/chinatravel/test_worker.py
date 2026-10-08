"""Offline worker checks: real upstream agents, fake parent broker, no API."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.chinatravel import upstream_worker as module


UPSTREAM = Path(os.environ.get("CHINATRAVEL_UPSTREAM", "/tmp/resimind-chinatravel-upstream"))
QUERY = {"uid": "offline", "nature_language": "一个人从上海去杭州玩一天。"}
SETTINGS = {"model": "deepseek-chat", "max_output_tokens": 128,
            "search_seconds": 1, "max_proposals": 2}
FIRST = ["先生成计划。", "plan('生成完整计划')", '{"people_number":1}']
TRANSLATION = [
    json.dumps({"days": 1, "people_number": 1, "start_city": "上海",
                "target_city": "杭州", "hard_logic": ["一个人"]}, ensure_ascii=False),
    '["result = people_count(plan) == 1"]',
]


class FakeBroker:
    def __init__(self, answers=()):
        self.answers = iter(answers)
        self.requests = []
        self.result = None

    def send(self, packet):
        packet = deepcopy(packet)  # Match the real IPC pickle boundary.
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
    dependencies = ("numpy", "pandas", "geopy", "jsonschema", "tqdm", "sklearn", "fuzzywuzzy")
    if not UPSTREAM.is_dir() or any(importlib.util.find_spec(name) is None for name in dependencies):
        pytest.skip("requires the pinned local ChinaTravel checkout and experiment venv")
    return str(UPSTREAM)


def execute(tmp_path, upstream, arm, answers=(), query=None, settings=None):
    query_path = tmp_path / "public.json"
    query_path.write_text(json.dumps(QUERY if query is None else query), encoding="utf-8")
    output = tmp_path / "output"
    broker = FakeBroker(answers)
    module.worker(broker, upstream, str(query_path), str(output),
                  SETTINGS if settings is None else settings, "fixed-sandbox", arm)
    assert broker.result is not None
    assert json.loads((output / "result.json").read_text()) == broker.result
    return broker, output


def test_official_react_keeps_native_request_parameters(tmp_path, upstream):
    broker, _ = execute(tmp_path, upstream, "official_react", FIRST)
    assert broker.result["status"] == "completed"
    assert broker.result["plan"] == {"people_number": 1}
    assert len(broker.requests) == 3
    for request in broker.requests:
        assert request["request_args"]["temperature"] == 0
        assert request["request_args"]["reasoning_effort"] == "none"
        assert request["request_args"]["max_tokens"] == 128
    assert broker.requests[0]["request_args"]["stop"] == ["\n"]
    assert broker.requests[1]["request_args"]["stop"] == ["\n"]
    assert "stop" not in broker.requests[2]["request_args"]
    assert broker.requests[2]["request_args"]["response_format"] == {"type": "json_object"}
    assert broker.result["details"]["worker_usage"]["prompt_tokens"] == 21


def test_nesy_budget_stop_cannot_be_swallowed_or_delivered(tmp_path, upstream):
    broker, output = execute(tmp_path, upstream, "official_nesy", [{"status": "budget_exhausted"}])
    assert broker.result["status"] == "budget_exhausted"
    assert broker.result["plan"] is None
    assert len(broker.requests) == 1
    assert not (output / "candidate_01.json").exists()


def test_nesy_numpy_result_is_identical_on_disk_and_ipc(tmp_path, upstream, monkeypatch):
    import numpy as np

    monkeypatch.syspath_prepend(upstream)
    from chinatravel.agent import load_model

    large = 2**60 + 19  # Detect accidental integer-to-float precision loss.
    native_plan = {
        "people_number": np.int64(2),
        "itinerary": [{"day": np.int64(1), "activities": [
            {"tickets": np.int64(2), "cost": np.float64(12.5)},
        ]}],
        "nested": {"counts": [np.int64(large), np.int64(-(2**63))]},
    }
    agent = SimpleNamespace(run=lambda *args, **kwargs: (True, native_plan))
    monkeypatch.setattr(load_model, "create_agent_runtime", lambda *args, **kwargs:
                        SimpleNamespace(agent=agent))
    broker, output = execute(tmp_path, upstream, "official_nesy")
    assert broker.result["status"] == "completed" and broker.requests == []
    # Parent-side persistence must succeed without a custom NumPy encoder.
    packet = json.loads(json.dumps(broker.result, ensure_ascii=False, allow_nan=False))
    assert packet == json.loads((output / "result.json").read_text())
    plan = packet["plan"]
    assert type(plan["people_number"]) is int and plan["people_number"] == 2
    assert type(plan["itinerary"][0]["day"]) is int
    assert plan["itinerary"][0]["activities"] == [{"tickets": 2, "cost": 12.5}]
    assert plan["nested"]["counts"] == [large, -(2**63)]
    assert all(type(value) is int for value in plan["nested"]["counts"])
    assert type(native_plan["people_number"]) is np.int64  # Input wasn't mutated.


def test_resimind_continues_same_state_and_counts_translation(tmp_path, upstream):
    later = ["根据反馈修正。", "plan('修正完整计划')", '{"people_number":2}']
    broker, output = execute(tmp_path, upstream, "resimind_react", FIRST + TRANSLATION + later)
    assert broker.result["status"] == "completed"
    assert broker.result["details"]["resimind_status"] == "budget_exhausted"
    assert broker.result["plan"] is None
    assert len(broker.requests) == 8
    assert broker.result["details"]["worker_usage"]["prompt_tokens"] == 56
    state = json.loads((output / "react_state.json").read_text())
    assert state["cur_step"] == 2 and state["max_steps"] == 50
    assert "验收反馈" in broker.requests[-1]["messages"][0]["content"]
    assert "schema_failed" in broker.requests[-1]["messages"][0]["content"]
    trace = json.loads((output / "resimind_trace.json").read_text())
    assert trace["accepted_plan"] is None
    assert trace["run_result"]["state"]["facts"] == []
    assert (output / "candidate_01.json").exists() and (output / "candidate_02.json").exists()


def test_mid_continuation_budget_stop_never_returns_previous_candidate(tmp_path, upstream):
    broker, output = execute(tmp_path, upstream, "resimind_react",
                             FIRST + TRANSLATION + [{"status": "budget_exhausted"}])
    assert broker.result["status"] == "budget_exhausted" and broker.result["plan"] is None
    assert (output / "candidate_01.json").exists()
    assert not (output / "candidate_02.json").exists()
    assert broker.result["details"]["worker_usage"]["requests"] == 6
    assert broker.result["details"]["worker_usage"]["prompt_tokens"] == 35


def test_self_translation_receives_only_public_query(tmp_path, upstream, monkeypatch):
    # Importing official translation requires the checkout's cwd for its public
    # example plans. No evaluation query/gold file is used here.
    monkeypatch.syspath_prepend(upstream)
    monkeypatch.chdir(upstream)
    from chinatravel.agent.nesy_agent import nl2sl_hybrid

    received = []
    private = "PRIVATE_ORACLE_SENTINEL_DO_NOT_READ"
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps({"hard_logic_py": [private]}))
    path_open = Path.open

    def guarded_open(path, *args, **kwargs):
        assert Path(path) != gold_path, "worker attempted gold access"
        return path_open(path, *args, **kwargs)

    def translate(query, llm, lang):
        received.append(deepcopy(query))
        assert query == QUERY and lang == "zh"
        assert llm._llm is None  # No provider SDK was initialized.
        return {**query, "days": 1, "people_number": 1, "start_city": "上海",
                "target_city": "杭州", "hard_logic_py": []}

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(nl2sl_hybrid, "nl2sl_reflect", translate)
    broker, output = execute(tmp_path, upstream, "resimind_react", FIRST,
                             settings={**SETTINGS, "max_proposals": 1})
    assert received == [QUERY]
    assert broker.result["plan"] is None
    gate = json.loads((output / "gate_01.json").read_text())
    assert gate["deferred"] and "translation_constraints_empty" in gate["reasons"]
    assert private not in json.dumps(broker.requests)
    assert private not in (output / "self_translation.json").read_text()


@pytest.mark.parametrize("field", ["hard_logic", "hard_logic_py", "hard_logic_nl", "unknown"])
def test_oracle_or_unknown_input_fields_stop_before_model(tmp_path, upstream, field):
    broker, _ = execute(tmp_path, upstream, "official_react", query={**QUERY, field: ["private"]})
    assert broker.result["status"] == "technical_failure"
    assert broker.result["plan"] is None and broker.requests == []


@pytest.mark.parametrize("reply", [
    {"status": "unrecognized", "error": "PRIVATE_PROVIDER_DETAIL"},
    {"status": "complete", "message": {"role": "assistant", "content": "x"}},
    {"status": "complete", "message": {"role": "assistant", "content": "x"},
     "usage": {"prompt_tokens": -1, "completion_tokens": 0}},
])
def test_unknown_or_malformed_broker_replies_fail_closed(tmp_path, upstream, reply):
    broker, output = execute(tmp_path, upstream, "official_react", [reply])
    assert broker.result["status"] == "technical_failure" and broker.result["plan"] is None
    assert len(broker.requests) == 1
    assert broker.result["details"]["exception_class"] == "WorkerStopped"
    for path in output.rglob("*"):
        if path.is_file():
            assert "PRIVATE_PROVIDER_DETAIL" not in path.read_text()


@pytest.mark.parametrize("settings", [
    {**SETTINGS, "max_output_tokens": 0},
    {**SETTINGS, "max_output_tokens": True},
    {**SETTINGS, "model": ""},
])
def test_malformed_model_parameters_stop_before_request(tmp_path, upstream, settings):
    broker, _ = execute(tmp_path, upstream, "official_react", settings=settings)
    assert broker.result["status"] == "technical_failure" and broker.result["plan"] is None
    assert broker.requests == []


def test_unknown_arm_and_invalid_search_time_stop_before_request(tmp_path, upstream):
    for index, (arm, settings) in enumerate([
        ("unknown", SETTINGS),
        ("official_nesy", {**SETTINGS, "search_seconds": -1}),
        ("official_nesy", {**SETTINGS, "search_seconds": float("nan")}),
    ]):
        directory = tmp_path / str(index)
        directory.mkdir()
        broker, _ = execute(directory, upstream, arm, settings=settings)
        assert broker.result["status"] == "technical_failure" and broker.result["plan"] is None
        assert broker.requests == []


def test_tool_log_handles_native_types_and_dataframes(tmp_path, upstream):
    answers = ["查字段。", "attractions_keys('杭州')", "查景点。",
               "attractions_select('杭州', 'name', lambda x: x == '西湖')", *FIRST]
    broker, output = execute(tmp_path, upstream, "official_react", answers)
    assert broker.result["status"] == "completed"
    tools = [json.loads(line) for line in (output / "tools.jsonl").read_text().splitlines()]
    assert len(tools) == 2
    result = json.loads((output / "tool_results" / (tools[0]["result_sha256"] + ".json")).read_text())
    assert result["success"] and tools[0]["result_omitted"] is False


def test_large_repeated_tool_results_are_deduplicated_and_call_limit_is_exact(tmp_path):
    class NativeResult:
        serializations = 0

        def to_dict(self):
            self.serializations += 1
            return {"success": True, "data": "大" * 100_000}

    value = NativeResult()
    trace = module._ToolTrace(tmp_path, max_records=3)
    for _ in range(7):
        assert trace.call(lambda env, command: value, None, "repeat()") is value
    trace.close()
    rows = [json.loads(line) for line in (tmp_path / "tools.jsonl").read_text().splitlines()]
    blobs = list((tmp_path / "tool_results").iterdir())
    assert len(rows) == 3 and len(blobs) == 1
    assert len({row["result_sha256"] for row in rows}) == 1
    assert value.serializations == 3  # No new result handling after truncation.
    summary = json.loads((tmp_path / "tool_trace_summary.json").read_text())
    assert summary["total_calls"] == 7 and summary["recorded_calls"] == 3
    assert summary["omitted_calls"] == 4 and summary["omitted_results"] == 0
    assert summary["truncated"] and summary["finalized"]
    assert summary["stored_result_count"] == 1
    assert summary["stored_result_bytes"] == blobs[0].stat().st_size
    assert summary["trace_bytes"] == (tmp_path / "tools.jsonl").stat().st_size
    assert summary["limits"] == {"trace_bytes": 8 * 1024 * 1024, "records": 3,
                                  "single_result_bytes": 1024 * 1024,
                                  "total_blob_bytes": 16 * 1024 * 1024}


def test_tool_trace_byte_limit_does_not_store_or_inspect_later_results(tmp_path):
    trace = module._ToolTrace(tmp_path, max_trace_bytes=300)
    first, second = {"data": "small"}, {"data": "never stored"}
    assert trace.call(lambda env, command: first, None, "first()") is first
    assert trace.call(lambda env, command: second, None, "x" * 1000) is second
    assert trace.call(lambda env, command: second, None, "later()") is second
    trace.close()
    summary = json.loads((tmp_path / "tool_trace_summary.json").read_text())
    assert summary["total_calls"] == 3 and summary["recorded_calls"] == 1
    assert summary["omitted_calls"] == 2 and summary["truncated"]
    assert summary["trace_bytes"] <= 300
    assert len(list((tmp_path / "tool_results").iterdir())) == 1


def test_oversized_tool_result_has_only_digest_and_omission_metadata(tmp_path):
    value = {"data": "x" * (1024 * 1024 + 1)}
    trace = module._ToolTrace(tmp_path)
    assert trace.call(lambda env, command: value, None, "large()") is value
    trace.close()
    row = json.loads((tmp_path / "tools.jsonl").read_text())
    assert row["result_omitted"] and row["omission_reason"] == "single_result_limit"
    assert row["result_bytes"] > 1024 * 1024 and len(row["result_sha256"]) == 64
    assert "data" not in row and not (tmp_path / "tool_results").exists()
    summary = json.loads((tmp_path / "tool_trace_summary.json").read_text())
    assert summary["recorded_calls"] == summary["omitted_results"] == 1
    assert summary["omitted_calls"] == 0 and not summary["truncated"]


def test_total_blob_limit_preserves_existing_deduplicated_results(tmp_path):
    trace = module._ToolTrace(tmp_path, max_blob_bytes=25)
    for value in ({"x": "a" * 10}, {"x": "b" * 10}, {"x": "a" * 10}):
        assert trace.call(lambda env, command: value, None, "blob()") is value
    trace.close()
    rows = [json.loads(line) for line in (tmp_path / "tools.jsonl").read_text().splitlines()]
    assert [row["result_omitted"] for row in rows] == [False, True, False]
    assert rows[1]["omission_reason"] == "total_blob_limit"
    blobs = list((tmp_path / "tool_results").iterdir())
    summary = json.loads((tmp_path / "tool_trace_summary.json").read_text())
    assert len(blobs) == summary["stored_result_count"] == 1
    assert sum(path.stat().st_size for path in blobs) == summary["stored_result_bytes"] <= 25
    assert summary["total_calls"] == summary["recorded_calls"] == 3
    assert summary["omitted_results"] == 1 and not summary["truncated"]


def test_audit_write_failure_cannot_change_native_tool_result(tmp_path, monkeypatch):
    trace = module._ToolTrace(tmp_path)
    value = {"native": "unchanged"}

    def fail_write(*args, **kwargs):
        raise OSError(27, "PRIVATE_AUDIT_FAILURE")

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    assert trace.call(lambda env, command: value, None, "works()") is value
    assert trace.call(lambda env, command: value, None, "works_again()") is value
    trace.close()
    summary = json.loads((tmp_path / "tool_trace_summary.json").read_text())
    assert summary["total_calls"] == summary["omitted_calls"] == 2
    assert summary["recorded_calls"] == 0 and summary["audit_errors"] == 1
    assert summary["truncated"]
    assert "PRIVATE_AUDIT_FAILURE" not in (tmp_path / "tool_trace_summary.json").read_text()


def test_worker_reports_integer_errno_without_exception_body(tmp_path, upstream, monkeypatch):
    monkeypatch.syspath_prepend(upstream)
    from chinatravel.agent import load_model

    def fail_runtime(*args, **kwargs):
        raise OSError(27, "PRIVATE_RUNTIME_FAILURE")

    monkeypatch.setattr(load_model, "create_agent_runtime", fail_runtime)
    broker, output = execute(tmp_path, upstream, "official_react")
    assert broker.result["status"] == "technical_failure" and broker.result["plan"] is None
    assert broker.result["details"]["errno"] == 27
    assert "PRIVATE_RUNTIME_FAILURE" not in (output / "result.json").read_text()


def _cli_args():
    return ["--port", "12345", "--auth-key", "abcd", "--upstream-root", "/runtime",
            "--public-query-path", "/public.json", "--output-dir", "/output",
            "--settings-json", json.dumps(SETTINGS), "--sandbox-digest", "fixed",
            "--arm", "official_react"]


def test_cli_applies_resource_limits_before_authenticated_ipc(monkeypatch):
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def client(address, authkey):
        calls.append(("ipc", address, authkey))
        return Connection()

    monkeypatch.setattr(module, "_apply_resource_limits", lambda settings: calls.append(("limits", settings)))
    monkeypatch.setattr(module, "Client", client)
    monkeypatch.setattr(module, "worker", lambda *args: calls.append(("worker", args)))
    module.main(_cli_args())
    assert [call[0] for call in calls] == ["limits", "ipc", "worker"]
    assert calls[1][1:] == (("127.0.0.1", 12345), b"\xab\xcd")


def test_cli_resource_limit_failure_never_connects(monkeypatch, capsys):
    def unavailable(_):
        raise OSError("PRIVATE_PLATFORM_DETAIL")

    monkeypatch.setattr(module, "_apply_resource_limits", unavailable)
    monkeypatch.setattr(module, "Client", lambda *args, **kwargs: pytest.fail("must fail before IPC"))
    with pytest.raises(SystemExit) as caught:
        module.main(_cli_args())
    assert caught.value.code == 2
    stderr = capsys.readouterr().err
    assert "resource_limits_unavailable:OSError" in stderr
    assert "PRIVATE_PLATFORM_DETAIL" not in stderr


def test_resource_limits_use_equal_soft_and_hard_without_memory_claim(monkeypatch):
    import resource

    calls = []
    monkeypatch.setattr(resource, "setrlimit", lambda name, limits: calls.append((name, limits)))
    module._apply_resource_limits({})
    assert calls == [(resource.RLIMIT_CPU, (420, 420)),
                     (resource.RLIMIT_FSIZE, (67_108_864, 67_108_864)),
                     (resource.RLIMIT_CORE, (0, 0))]
