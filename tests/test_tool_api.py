import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from resimind.domains.algebra import verify_identity
from resimind.tool_api import MAX_REQUEST, ToolSession, run_json_lines, tool_catalog


@pytest.mark.parametrize("lhs,rhs,status", [
    ("(x+1)**2", "x**2+2*x+1", "verified"),
    ("(x+1)**2", "x**2+1", "rejected"),
    ("1/x", "x", "unresolved"),
    ("__import__('os').system('echo bad')", "0", "unresolved"),
    ("x**100000000", "0", "unresolved"),
])
def test_verification_statuses(lhs, rhs, status):
    result = ToolSession().call("verify_identity", {"lhs": lhs, "rhs": rhs, "variables": ["x"]})
    assert result["status"] == status
    assert result["model_calls"] == 0
    if status == "rejected":
        assert "coefficient" in result["reason"]


@pytest.mark.parametrize("name,args", [
    ("missing", {}), ([], {}), ("list_rules", {"path": "/tmp/anything"}),
    ("expand_polynomial", {"expression": True, "variables": ["x"]}),
    ("expand_polynomial", {"expression": "x", "variables": "x"}),
    ("expand_polynomial", {"expression": "x", "variables": ["x", "x"]}),
    ("expand_polynomial", {"expression": "x", "variables": ["class"]}),
    ("expand_polynomial", {"expression": "x" * 8193, "variables": ["x"]}),
    ("learn_polynomial", {"expression": "x", "variables": [1]}),
])
def test_invalid_arguments_do_not_mutate_memory(name, args):
    session = ToolSession()
    before = session.agent.fingerprint()
    assert session.call(name, args)["status"] == "error"
    assert before == session.agent.fingerprint()


def test_sparse_output_preserves_large_exact_coefficients():
    result = ToolSession().call("expand_polynomial", {"expression": "(100000000000000000*x+1)**2", "variables": ["x"]})
    assert result["status"] == "verified"
    terms = result["certificate"]["terms"]
    assert terms[-1] == {"powers": [2], "numerator": "10000000000000000000000000000000000", "denominator": "1"}
    json.dumps(result, allow_nan=False)


def train(session):
    first = session.call("learn_polynomial", {"expression": "(a+b)**3", "variables": ["a", "b"]})
    second = session.call("learn_polynomial", {"expression": "(u+v)**3", "variables": ["u", "v"]})
    assert first["status"] == second["status"] == "verified"
    assert not first["learning"]["new_rule_ids"]
    assert second["learning"]["new_rule_ids"]


def test_learning_recall_and_process_isolation():
    session = ToolSession()
    args = {"expression": "(2*x+3*y)**3", "variables": ["x", "y"]}
    assert session.call("recall_polynomial", args)["status"] == "unresolved"
    train(session)
    before = session.agent.fingerprint()
    result = session.call("recall_polynomial", args)
    assert result["status"] == "verified" and result["route"] == "recall"
    assert result["search_actions"] == 1
    assert verify_identity(args["expression"], result["expression"], args["variables"]).status == "verified"
    assert before == session.agent.fingerprint()
    assert len(session.call("list_rules", {})["rules"]) == 1
    assert ToolSession().call("recall_polynomial", args)["status"] == "unresolved"


def test_expand_is_read_only_and_failed_learning_is_not_admitted():
    session = ToolSession()
    before = session.agent.fingerprint()
    assert session.call("expand_polynomial", {"expression": "(a+b)**3", "variables": ["a", "b"]})["status"] == "verified"
    assert session.call("learn_polynomial", {"expression": "sin(x)", "variables": ["x"]})["status"] == "unresolved"
    assert before == session.agent.fingerprint()


def test_json_lines_recover_after_bad_and_oversized_requests():
    good = json.dumps({"tool": "list_rules", "arguments": {}})
    rows = ['{"tool":"list_rules","tool":"learn_polynomial","arguments":{}}',
            '{"tool":"list_rules","arguments":{"n":NaN}}', "[" * 2000,
            '"' + "x" * MAX_REQUEST + '"', good]
    output = io.StringIO()
    assert run_json_lines(io.StringIO("\n".join(rows) + "\n"), output) == 2
    results = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["status"] for r in results] == ["error"] * 4 + ["ok"]


def test_cli_catalog_and_zero_dependency_json(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run([sys.executable, "-m", "resimind", "api", "--list-tools"],
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == tool_catalog()
    result = subprocess.run([sys.executable, "-m", "resimind", "api"],
        input='{"tool":"verify_identity","arguments":{"lhs":"x+x","rhs":"2*x","variables":["x"]}}\n',
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["status"] == "verified"
