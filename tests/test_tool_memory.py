"""Verified learning survives tool-process restarts only when explicitly configured."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from resimind.tool_api import ToolSession


FIRST = {"expression": "(a+b)**3", "variables": ["a", "b"]}
SECOND = {"expression": "(u+v)**3", "variables": ["u", "v"]}
QUERY = {"expression": "(2*x+3*y)**3", "variables": ["x", "y"]}


def request(directory, name, arguments):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    return subprocess.run([sys.executable, "-m", "resimind", "api", "--memory-dir", str(directory)],
        input=json.dumps({"tool": name, "arguments": arguments}) + "\n",
        capture_output=True, text=True, env=env, timeout=30)


def snapshot(directory):
    return {name: (directory / name).read_bytes() for name in ("rules.json", "learning.json")}


def test_learning_and_recall_across_three_processes(tmp_path):
    first = request(tmp_path, "learn_polynomial", FIRST)
    second = request(tmp_path, "learn_polynomial", SECOND)
    assert first.returncode == second.returncode == 0
    assert not json.loads(first.stdout)["learning"]["new_rule_ids"]
    assert json.loads(second.stdout)["learning"]["new_rule_ids"]
    before = snapshot(tmp_path)
    recalled = request(tmp_path, "recall_polynomial", QUERY)
    assert recalled.returncode == 0, recalled.stderr
    result = json.loads(recalled.stdout)
    assert result["status"] == "verified" and result["route"] == "recall"
    assert result["search_actions"] == 1 and result["model_calls"] == 0
    assert result["memory_scope"] == "directory"
    assert snapshot(tmp_path) == before


def test_rejects_a_second_process_and_releases_on_close(tmp_path):
    with ToolSession(tmp_path):
        refused = request(tmp_path, "list_rules", {})
        assert refused.returncode == 2
        assert "cannot be locked" in refused.stderr
        assert not refused.stdout
    assert request(tmp_path, "list_rules", {}).returncode == 0


def test_closed_session_cannot_write_after_releasing_its_lock(tmp_path):
    previous = ToolSession(tmp_path)
    previous.close()
    with ToolSession(tmp_path) as current:
        current.call("learn_polynomial", FIRST)
        before = snapshot(tmp_path)
        refused = previous.call("learn_polynomial", SECOND)
        assert refused["status"] == "error" and "closed" in refused["reason"]
        assert snapshot(tmp_path) == before


def test_revalidates_history_and_never_resets_a_bad_snapshot(tmp_path):
    with ToolSession(tmp_path) as session:
        session.call("learn_polynomial", FIRST)
    learning = tmp_path / "learning.json"
    data = json.loads(learning.read_text())
    data["examples"][0]["after"] = "0"
    learning.write_text(json.dumps(data))
    before = snapshot(tmp_path)
    refused = request(tmp_path, "list_rules", {})
    assert refused.returncode == 2
    assert "unverified learning history" in refused.stderr
    assert snapshot(tmp_path) == before
    # A failed load must release its lock too.
    with pytest.raises(ValueError, match="unverified learning history"):
        ToolSession(tmp_path)


def test_partial_snapshot_is_not_an_empty_store(tmp_path):
    (tmp_path / "rules.json").write_text("{}")
    with pytest.raises(OSError):
        ToolSession(tmp_path)
    assert not (tmp_path / "learning.json").exists()


def test_malformed_snapshot_reports_a_startup_error(tmp_path):
    with ToolSession(tmp_path) as session:
        session.call("learn_polynomial", FIRST)
    learning = tmp_path / "learning.json"
    data = json.loads(learning.read_text())
    data["observations"] = [{}]
    learning.write_text(json.dumps(data))
    before = snapshot(tmp_path)
    refused = request(tmp_path, "list_rules", {})
    assert refused.returncode == 2
    assert "invalid memory snapshot" in refused.stderr
    assert "Traceback" not in refused.stderr and not refused.stdout
    assert snapshot(tmp_path) == before


def test_frozen_snapshot_remains_read_only(tmp_path):
    with ToolSession() as source:
        source.call("learn_polynomial", FIRST)
        source.call("learn_polynomial", SECOND)
        source.agent.freeze().save(tmp_path)
    before = snapshot(tmp_path)
    with ToolSession(tmp_path) as session:
        assert session.call("recall_polynomial", QUERY)["status"] == "verified"
        result = session.call("learn_polynomial", FIRST)
        assert result["status"] == "error" and "frozen" in result["reason"]
    assert snapshot(tmp_path) == before


def test_unsuccessful_learning_writes_no_snapshot(tmp_path):
    with ToolSession(tmp_path) as session:
        result = session.call("learn_polynomial", {"expression": "sin(x)", "variables": ["x"]})
        assert result["status"] == "unresolved"
    assert not (tmp_path / "learning.json").exists()
    assert not (tmp_path / "rules.json").exists()


def test_save_failure_is_not_reported_as_durable_success(tmp_path, monkeypatch):
    from resimind.tool_memory import ToolMemory
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(ToolMemory, "save", fail)
    with ToolSession(tmp_path) as session:
        result = session.call("learn_polynomial", FIRST)
        assert result["status"] == "error" and "disk full" in result["reason"]
        assert result["memory_scope"] == "directory"
        assert session.call("list_rules", {})["status"] == "error"
