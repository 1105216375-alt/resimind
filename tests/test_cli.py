"""CLI behavior from a directory containing only the installable package."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from resimind import Residual, RunResult, State
from resimind import cli


@pytest.fixture(scope="module")
def installed_layout(tmp_path_factory):
    # No examples, tools, repository root, or editable-install path is present.
    root = tmp_path_factory.mktemp("installed-layout")
    package = Path(cli.__file__).resolve().parent
    shutil.copytree(package, root / "resimind", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return root


def invoke(installed_layout, *args):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, "-m", "resimind", *args], cwd=installed_layout,
                          env=env, text=True, capture_output=True, timeout=30)


def test_package_only_entrypoint_and_actual_rejection_state(installed_layout):
    result = invoke(installed_layout, "demo")
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert result.stdout.startswith("ResiMind | OFFLINE deterministic fixture | NOT A LIVE LLM RUN\n")
    assert "REJECT certify_primal_dual" in result.stdout
    assert "primal_inequality_violation | remaining=2 | state_revision=1->1 (no committed change)" in result.stdout
    assert "Verified unique global minimizer: x = (1, 3/4, 5/4)" in result.stdout
    assert "Verified objective: -73/8" in result.stdout
    assert "Status: solved | remaining=0 | state_revision=3" in result.stdout


@pytest.mark.parametrize("domain", ["optimization", "bridge", "customer-support", "planning"])
def test_json_is_a_single_audit_value_with_no_other_stdout(installed_layout, domain):
    result = invoke(installed_layout, "demo", "--domain", domain, "--json")
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {"routes", "run_result", "task", "tool_events"}
    assert payload["run_result"]["status"] == "solved"
    rejected = [event for event in payload["run_result"]["trace"] if event["decision"] == "reject"]
    assert rejected and all(event["before"] == event["after"] for event in rejected)


def test_bridge_reports_committed_envelope_and_scope(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "bridge")
    assert result.returncode == 0, result.stderr
    assert "OFFLINE deterministic fixture" in result.stdout
    assert "Verified load cases: 8" in result.stdout
    assert "Governing pier moment:" in result.stdout
    assert "absolute MIDSPAN deflection:" in result.stdout
    assert "No bridge safety certification." in result.stdout
    assert "Status: solved | remaining=0" in result.stdout


def test_knowledge_growth_runs_from_installed_package_only(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "knowledge-growth")
    assert result.returncode == 0, result.stderr
    assert "NOT A LIVE LLM RUN" in result.stdout
    assert "persist -> reload/reverify -> transfer" in result.stdout
    assert "1 committed cross-task rule use(s)" in result.stdout


def test_adaptive_recovery_and_reloaded_rule_reuse_from_installed_package(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "adaptive", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["live_model"] is False
    discovery, transfer = report["discovery"], report["transfer"]
    assert discovery["status"] == transfer["status"] == "solved"
    assert discovery["strategy_audit"]["whole_model_accepts"] == 0
    assert discovery["strategy_audit"]["local_model_accepts"] == 1
    assert (discovery["strategy_audit"]["primitive_accepts"]
            + discovery["strategy_audit"]["distribute_accepts"]) > 0
    assert discovery["strategy_audit"]["strategy_switches"] >= 2
    assert len(discovery["admitted_rules"]) == 1
    assert transfer["strategy_audit"]["model_calls"] == 0
    assert transfer["strategy_audit"]["rule_accepts"] == 1
    rejected = [e for e in discovery["result"]["run_result"]["trace"] if e["decision"] == "reject"]
    assert rejected and all(e["before"] == e["after"] for e in rejected)


def test_adaptive_text_labels_fixture_without_claiming_a_live_run(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "adaptive")
    assert result.returncode == 0, result.stderr
    assert "OFFLINE scripted adaptive recovery | NOT A LIVE LLM RUN" in result.stdout
    assert "whole_model: reject" in result.stdout and "local_model: accept" in result.stdout
    assert "transfer: solved; checked rule uses=1" in result.stdout


def test_installed_growth_control_compares_the_same_task_and_reports_unfinished_arm(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "growth-control", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["live_model"] is False
    old, new = report["arms"]["primitive"], report["arms"]["bounded_growth"]
    assert old["status"] != "solved" and old["output"] is None
    assert new["status"] == "solved" and new["steps"] < old["steps"]
    assert new["strategy_audit"]["peak_expression_nodes"] < old["strategy_audit"]["peak_expression_nodes"]
    assert old["counts"]["proposal_calls"] == new["counts"]["proposal_calls"] == 0


def test_installed_lean_demo_exposes_real_goals_before_commit(installed_layout):
    from resimind.integrations.lean import LeanPolynomialBackend
    backend = LeanPolynomialBackend()
    if backend.lean_path is None or not Path(backend.lean_path).is_file():
        pytest.skip("optional Lean 4.29.0 installation not available")
    probe = backend.check("x", "x", ("x",), tactic="rfl")
    if probe.status == "unavailable":
        pytest.skip("optional pinned Lean toolchain not available")
    assert probe.status == "verified"
    result = invoke(installed_layout, "demo", "--domain", "lean", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["live_model"] is False and report["mode"] == "scripted-proposals-real-local-lean"
    discovery = report["discovery"]
    assert discovery["status"] == report["transfer"]["status"] == "solved"
    assert report["proposal_inputs"][1]["proof_feedback"]["goals"]
    assert discovery["strategy_audit"]["lean_unresolved"] == 1
    assert discovery["result"]["run_result"]["trace"][0]["after"]["facts"] == []
    assert report["transfer"]["strategy_audit"]["lean_verified"] == 1


def test_missing_lean_cli_returns_failure_with_no_symbolic_success(monkeypatch, capsys, tmp_path):
    from resimind import lean_demo
    from resimind.integrations.lean import LeanPolynomialBackend
    monkeypatch.setattr(lean_demo, "LeanPolynomialBackend", lambda: LeanPolynomialBackend(tmp_path / "missing"))
    assert cli.main(["demo", "--domain", "lean", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["discovery"]["status"] != "solved"
    assert report["discovery"]["admitted_rules"] == []
    assert report["transfer"]["status"] == "not_run"


def test_knowledge_growth_json_reports_discovery_and_transfer_separately(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "knowledge-growth", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["live_model"] is False
    assert report["discovery"]["status"] == "solved"
    assert len(report["discovery"]["admitted_rules"]) == 1
    fixed, growing = report["arms"]["fixed"], report["arms"]["growing"]
    assert fixed["status"] == growing["status"] == "solved"
    assert growing["proposal_calls"] < fixed["proposal_calls"]
    assert growing["rule_uses"][0]["id"] == report["discovery"]["admitted_rules"][0]


def test_customer_support_reports_only_checked_amount(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "customer-support")
    assert result.returncode == 0, result.stderr
    assert "REJECT" in result.stdout
    assert "Recommended item refund: CNY 249.00" in result.stdout
    assert "No refund executed" in result.stdout
    assert "Status: solved | remaining=0" in result.stdout


def test_customer_support_missing_delivery_keeps_recommendation_blocked(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "customer-support", "--scenario", "missing-delivery")
    assert result.returncode == 1 and not result.stderr
    assert "DEFER" in result.stdout and "Pending evidence or checks:" in result.stdout
    assert "Verified recommendation:" not in result.stdout
    assert "Recommended item refund:" not in result.stdout


def test_customer_support_expired_is_completed_human_review(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "customer-support", "--scenario", "expired")
    assert result.returncode == 0, result.stderr
    assert "Verified recommendation: human_review" in result.stdout
    assert "Recommended item refund:" not in result.stdout
    assert "Status: solved | remaining=0" in result.stdout


@pytest.mark.parametrize("scenario", ["day-out", "rain"])
def test_planning_prints_a_checked_plan_without_subjective_rationale(installed_layout, scenario):
    result = invoke(installed_layout, "demo", "--domain", "planning", "--scenario", scenario)
    assert result.returncode == 0, result.stderr
    assert "REJECT" in result.stdout and "ACCEPT" in result.stdout
    assert "Verified feasible itinerary" in result.stdout
    assert "A pleasant creative day" not in result.stdout
    assert "Status: solved | remaining=0" in result.stdout


def test_planning_unknown_return_keeps_output_pending(installed_layout):
    result = invoke(installed_layout, "demo", "--domain", "planning", "--scenario", "missing-travel")
    assert result.returncode == 1 and not result.stderr
    assert "DEFER" in result.stdout
    assert "trip:transport_grounding" in result.stdout
    assert "Verified feasible itinerary" not in result.stdout


@pytest.mark.parametrize("args", [("--help",), ("demo", "--help")])
def test_help_without_running_a_demo(installed_layout, args):
    result = invoke(installed_layout, *args)
    assert result.returncode == 0 and not result.stderr
    assert "usage: resimind" in result.stdout
    assert "OFFLINE" in result.stdout or "--domain" in result.stdout
    assert "Verified objective" not in result.stdout


@pytest.mark.parametrize("args", [(), ("demo", "--live"), ("demo", "--domain", "unknown"), ("unknown",),
                                  ("demo", "--scenario", "refund"),
                                  ("demo", "--domain", "bridge", "--scenario", "expired"),
                                  ("demo", "--domain", "planning", "--scenario", "refund"),
                                  ("demo", "--domain", "customer-support", "--scenario", "rain")])
def test_unsupported_inputs_fail_with_helpful_argparse_error(installed_layout, args):
    result = invoke(installed_layout, *args)
    assert result.returncode == 2
    assert not result.stdout
    assert "usage: resimind" in result.stderr and "error:" in result.stderr


@pytest.mark.parametrize("json_output", [False, True])
def test_unsolved_exit_is_nonzero_and_never_prints_candidate_as_answer(monkeypatch, capsys, json_output):
    original = cli.optimization.run_demo()
    failed = replace(original, run_result=RunResult(State(), Residual(goals=("pending",)), "stalled", "no-proposal"))
    monkeypatch.setattr(cli.optimization, "run_demo", lambda: failed)
    code = cli.main(["demo"] + (["--json"] if json_output else []))
    output = capsys.readouterr()
    assert code == 1 and not output.err
    if json_output:
        assert json.loads(output.out)["run_result"]["status"] == "stalled"
    else:
        assert "Status: stalled" in output.out
        assert "Verified unique global minimizer" not in output.out
        assert "Verified objective" not in output.out
