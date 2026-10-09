"""Audit boundaries for the adaptive/symbolic comparison; no live model calls."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from benchmarks import run_adaptive_algebra_eval as study
from benchmarks.algebra_task_generator import generate_cases
from benchmarks.knowledge_growth import Case, _snapshot
from benchmarks.live_eval_support import RecordedCompletion, digest, write_json
from resimind.domains.algebra import AlgebraVerifier
from resimind.knowledge import KnowledgeCandidate, KnowledgeLibrary


CASE = Case("fixture", "(x+1)**2", ("x",), "fixture-family")
ANSWER = "x*x+2*x+1"


def library():
    store = KnowledgeLibrary({"algebra": AlgebraVerifier()})
    for name, lhs, rhs in (
        ("square", "(u+v)**2", "u**2+2*u*v+v**2"),
        ("cube", "(u+v)**3", "u**3+3*u**2*v+3*u*v**2+v**3"),
        ("difference", "(u+v)*(u-v)", "u**2-v**2"),
    ):
        record = store.admit(KnowledgeCandidate(
            name, "algebra", "polynomial_identity", {"lhs": lhs, "rhs": rhs, "variables": ["u", "v"]},
            derivation=({"lhs": lhs, "rhs": rhs},), source_task_id=f"discover-{name}"))
        assert record.status == "verified"
    return store


def save_manifest(folder, value):
    value["manifest_sha256"] = digest({k: v for k, v in value.items() if k != "manifest_sha256"})
    write_json(folder / "manifest.json", value)
    return value


@pytest.fixture
def priors(tmp_path):
    v3, v4 = tmp_path / "v3", tmp_path / "v4"
    v3.mkdir()
    v4.mkdir()
    data = _snapshot(library()).encode()
    sha = hashlib.sha256(data).hexdigest()
    (v3 / "knowledge.json").write_bytes(data)
    # Fixed fixture seeds never enter a real study's sampling path.
    first = save_manifest(v3, {"study": "structured-residual-fresh-algebra-v3", "development": False,
                              "cases": [asdict(c) for c in generate_cases(424242)]})
    write_json(v3 / "summary.json", {"manifest_sha256": first["manifest_sha256"],
               "library": {"snapshot_sha256": sha},
               # Synthetic accounting values, not measurements from a live study.
               "discovery_totals": {"model_calls": 2, "input_tokens": 100, "output_tokens": 20}})
    prior, _ = study.read_v3(v3)
    second = save_manifest(v4, {"study": "state-bound-rule-execution-algebra-v4", "development": False,
        "prior": prior, "cases": [asdict(c) for c in generate_cases(
            515151, exclude_expressions=[c["expression"] for c in first["cases"]])]})
    (v4 / "knowledge.json").write_bytes(data)
    write_json(v4 / "summary.json", {"manifest_sha256": second["manifest_sha256"],
                                    "library": {"snapshot_sha256": sha}})
    return v3, v4


@pytest.fixture
def frozen_environment(monkeypatch):
    monkeypatch.setattr(study, "sources", lambda: {"fixture.py": "fixed-source-hash"})
    monkeypatch.setattr(study, "snapshot_sources", lambda folder, hashes: Path(folder).mkdir(parents=True))
    monkeypatch.setattr(study.subprocess, "check_output", lambda *a, **kw: "fixture-commit\n")
    monkeypatch.setattr(study.secrets, "token_hex", lambda size: "de" * size)


def edit_manifest(folder, edit):
    value = json.loads((folder / "manifest.json").read_text())
    edit(value)
    return save_manifest(folder, value)


def test_both_prior_inventories_and_shared_library_are_fixed_before_sampling(
        tmp_path, priors, frozen_environment, monkeypatch):
    folder = tmp_path / "fresh"
    value, data = study.read_priors(*priors)
    order = []
    original_read, original_generate = study.read_priors, study.generate_cases

    def read(*args):
        result = original_read(*args)
        order.append("priors-verified")
        return result

    def snapshot(path, hashes):
        order.append("source-snapshot")
        Path(path).mkdir(parents=True)

    def seed(size):
        assert order == ["priors-verified", "source-snapshot"]
        assert (folder / "knowledge.json").read_bytes() == data
        assert value["exclusions"]["unique_ast_count"] == 64
        order.append("seed")
        return "de" * size

    def generate(seed, per_group, *, exclude_expressions):
        assert order[-1] == "seed"
        assert exclude_expressions == [c["expression"] for name in ("v3", "v4") for c in value[name]["cases"]]
        order.append("sample")
        return original_generate(seed, per_group, exclude_expressions=exclude_expressions)

    monkeypatch.setattr(study, "read_priors", read)
    monkeypatch.setattr(study, "snapshot_sources", snapshot)
    monkeypatch.setattr(study.secrets, "token_hex", seed)
    monkeypatch.setattr(study, "generate_cases", generate)
    result = study.freeze(folder, *priors)
    assert order == ["priors-verified", "source-snapshot", "seed", "sample"]
    assert len(result["cases"]) == 32 and result["repeats"] == 2
    assert result["prior"]["historical_acquisition"]["model_calls"] == 2
    monkeypatch.setattr(study, "generate_cases", original_generate)
    assert study.check_frozen(folder)["manifest_sha256"] == result["manifest_sha256"]


@pytest.mark.parametrize("mutation,message", [
    (lambda v: v["settings"].update(max_action_steps=65), "Protocol changed"),
    (lambda v: v["settings"].update(max_calls_per_task=9), "Protocol changed"),
    (lambda v: v["comparisons"].reverse(), "Protocol changed"),
    (lambda v: v["cases"][0].update(expression="999"), "Cases differ"),
    (lambda v: v["prior"].update(exclusions={}), "exclusion inventory changed"),
])
def test_frozen_protocol_and_sampling_reconstruct_after_rehashed_tamper(
        tmp_path, priors, frozen_environment, mutation, message):
    folder = tmp_path / "fresh"
    study.freeze(folder, *priors)
    edit_manifest(folder, mutation)
    with pytest.raises(ValueError, match=message):
        study.check_frozen(folder)


def test_library_bytes_and_current_source_are_both_verified(tmp_path, priors, frozen_environment, monkeypatch):
    folder = tmp_path / "fresh"
    study.freeze(folder, *priors)
    path = folder / "knowledge.json"
    data = path.read_bytes()
    path.write_bytes(data + b"\n")
    with pytest.raises(ValueError, match="Frozen library changed"):
        study.check_frozen(folder)
    path.write_bytes(data)
    monkeypatch.setattr(study, "sources", lambda: {"fixture.py": "changed"})
    with pytest.raises(ValueError, match="Implementation changed"):
        study.check_frozen(folder)


@pytest.mark.parametrize("mutation,message", [
    (lambda v: v.update(development=True), "intact completed formal v4"),
    (lambda v: v["cases"].pop(), "complete 32-case"),
    (lambda v: v["cases"].__setitem__(0, v["cases"][1]), "complete 32-case"),
    (lambda v: v["prior"].update(study_manifest_sha256="wrong"), "does not bind"),
    (lambda v: v["prior"]["cases"][0].update(expression="999"), "does not bind"),
])
def test_prior_v4_must_be_formal_complete_and_bound_to_v3(priors, mutation, message):
    v3, v4 = priors
    value = edit_manifest(v4, mutation)
    summary = json.loads((v4 / "summary.json").read_text())
    summary["manifest_sha256"] = value["manifest_sha256"]
    write_json(v4 / "summary.json", summary)
    with pytest.raises(ValueError, match=message):
        study.read_priors(v3, v4)


def test_both_prior_studies_must_share_exact_library_bytes(priors):
    v3, v4 = priors
    path = v4 / "knowledge.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="same audited library"):
        study.read_priors(v3, v4)


def test_repeated_prior_expression_ast_is_rejected_even_with_32_distinct_ids(priors):
    v3, v4 = priors
    old = json.loads((v3 / "manifest.json").read_text())
    value = edit_manifest(v4, lambda v: v["cases"][0].update(expression=old["cases"][0]["expression"]))
    summary = json.loads((v4 / "summary.json").read_text())
    summary["manifest_sha256"] = value["manifest_sha256"]
    write_json(v4 / "summary.json", summary)
    with pytest.raises(ValueError, match="64 distinct"):
        study.read_priors(v3, v4)


def test_development_uses_only_the_four_prespecified_old_v4_cases(tmp_path, priors, frozen_environment):
    folder = tmp_path / "dev"
    value = study.freeze(folder, *priors, development=True)
    assert [c["id"] for c in value["cases"]] == list(study.DEVELOPMENT_IDS)
    assert value["development"] and value["repeats"] == 1
    assert value["settings"]["max_physical_calls"] == 96
    assert value["settings"]["max_action_steps"] == 64
    with pytest.raises(ValueError, match="already frozen"):
        study.freeze(folder, *priors)
    write_json(folder / "summary.json", {})
    with pytest.raises(ValueError, match="Completed study exists"):
        study.run_study(folder, None)


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.chat = NS(completions=NS(create=self.create))

    def with_options(self, **kwargs):
        assert kwargs["max_retries"] == 0
        return self

    def create(self, **kwargs):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        response = self.response(kwargs) if callable(self.response) else self.response
        return NS(model="fixture", id="fixture", usage=NS(prompt_tokens=7, completion_tokens=4, total_tokens=11),
                  choices=[NS(finish_reason="stop", message=NS(content=response))])


@pytest.mark.parametrize("arm", ["strong_verify_retry", "executable_growth"])
@pytest.mark.parametrize("response,kind", [
    ("null", "abstention"), ("{bad_json", "schema"), (ValueError("private exception"), "transport"),
])
def test_old_reference_arms_preserve_their_failure_stopping_behavior(tmp_path, arm, response, kind):
    client = Client(response)
    recorder = RecordedCompletion(tmp_path, study.SETTINGS, client, study.SYSTEM)
    row = study.run_case(CASE, arm, 0, recorder, library(), phase="test")
    assert row["model_calls"] == client.calls == 1
    assert not row["success"] and not row["invalid_delivered"]
    assert row["schema_failure"] == (kind == "schema")
    assert row["technical_failure"] == (kind == "transport")
    assert row["validated_actions"] == 0
    assert study.totals([row])["tasks"] == 1


def test_symbolic_control_invokes_no_model_and_keeps_verified_action_costs(tmp_path):
    client = Client(AssertionError("Symbolic control must never call the API"))
    recorder = RecordedCompletion(tmp_path, study.SETTINGS, client, study.SYSTEM)
    row = study.run_case(CASE, "symbolic_growth", 0, recorder, library(), phase="test")
    assert row["success"] and row["oracle"]["status"] == "valid"
    assert row["model_calls"] == row["api_attempts"] == client.calls == 0
    assert row["request_ids"] == recorder.records() == []
    assert row["usage"]["total_tokens"] == 0
    assert 0 < row["validated_actions"] <= row["controller_steps"] <= 64
    assert row["work"]["identity_checks"] > 0
    assert row["strategy_audit"]["events"]
    assert row["admissions"] == []
    raw = json.loads(next((tmp_path / "cases").glob("*.json")).read_text())
    assert raw["strategy_audit"] == row["strategy_audit"]
    assert "events" not in study.compact(row)["strategy_audit"]
    assert study.totals([row])["validated_actions"] == row["validated_actions"]


@pytest.mark.parametrize("response,technical", [
    ("null", False), ("{bad_json", False), (ValueError("private exception"), True),
])
def test_adaptive_recovery_retains_failed_requests_and_can_independently_succeed(tmp_path, response, technical):
    client = Client(response)
    recorder = RecordedCompletion(tmp_path, study.SETTINGS, client, study.SYSTEM)
    row = study.run_case(CASE, "adaptive_growth", 0, recorder, library(), phase="test")
    assert row["success"] and not row["invalid_delivered"]
    assert 0 < row["model_calls"] == client.calls <= 8
    assert row["strategy_audit"]["model_calls"] == row["model_calls"]
    assert row["technical_failure"] == technical
    assert row["schema_failure"] == (response == "{bad_json")
    assert row["controller_steps"] <= 64
    assert row["strategy_audit"]["strategy_switches"] > 0
    assert len(recorder.records()) == row["model_calls"]
    assert row["admissions"] == []
    if technical:
        assert row["usage"]["total_tokens"] is None
        assert study.totals([row])["technical_failures"] == 1


def test_small_action_budget_stops_symbolic_work_without_calling_a_model(tmp_path):
    client = Client(AssertionError("No symbolic model requests"))
    settings = dict(study.SETTINGS, max_action_steps=1)
    recorder = RecordedCompletion(tmp_path, settings, client, study.SYSTEM)
    case = Case("bounded-fixture", "(x+1)*(x+2)*(x+3)", ("x",), "fixture-family")
    row = study.run_case(case, "symbolic_growth", 0, recorder, library(), phase="test")
    assert row["controller_steps"] == 1
    assert row["model_calls"] == client.calls == 0
    assert not row["success"] and row["oracle"]["status"] == "missing"
    assert row["strategy_audit"]["action_budget_exhausted"]
    assert study.totals([row])["action_budget_exhausted_episodes"] == 1


def test_blocked_model_request_remains_a_budget_failure_after_verified_recovery(tmp_path):
    client = Client("null")
    settings = dict(study.SETTINGS, max_prompt_bytes=1)
    recorder = RecordedCompletion(tmp_path, settings, client, study.SYSTEM)
    row = study.run_case(CASE, "adaptive_growth", 0, recorder, library(), phase="test")
    assert row["success"] and row["budget_failure"] and row["technical_failure"]
    assert row["model_calls"] > 0 and client.calls == row["api_attempts"] == 0
    assert all(r["status"] == "blocked" for r in recorder.records())
    assert study.totals([row])["budget_failures"] == 1


def test_independent_oracle_receives_only_delivered_output_after_controller_finishes(tmp_path, monkeypatch):
    client = Client("null")
    recorder = RecordedCompletion(tmp_path, study.SETTINGS, client, study.SYSTEM)
    called = []
    original = study.score_expansion

    def score(expression, output, variables):
        assert client.calls > 0
        called.append((expression, output, variables))
        return original(expression, output, variables)

    monkeypatch.setattr(study, "score_expansion", score)
    row = study.run_case(CASE, "adaptive_growth", 0, recorder, library(), phase="test")
    assert called == [(CASE.expression, row["output"], CASE.variables)]
    assert all("oracle" not in r["user_prompt"] for r in recorder.records())


def test_actual_strategy_counters_must_agree_with_recorded_model_invocations(tmp_path, monkeypatch):
    recorder = RecordedCompletion(tmp_path, study.SETTINGS, Client("null"), study.SYSTEM)
    monkeypatch.setattr(study, "adaptive_run", lambda *a, **kw: {"strategy_audit": {"model_calls": 1}})
    with pytest.raises(RuntimeError, match="counter disagrees"):
        study.run_case(CASE, "adaptive_growth", 0, recorder, library(), phase="test")


def test_full_development_summary_retains_all_arms_and_separate_historical_costs(
        tmp_path, priors, frozen_environment):
    folder = tmp_path / "dev"
    manifest = study.freeze(folder, *priors, development=True)
    study.run_study(folder, Client("null"))
    result = json.loads((folder / "summary.json").read_text())
    assert result["manifest_sha256"] == manifest["manifest_sha256"]
    assert result["statistics"] is None
    assert result["evaluation_kind"] == "seen_case_development"
    assert len(result["transfer"]) == 16
    assert result["case_clusters"] == 4 and result["repeats"] == 1
    assert {r["arm"] for r in result["transfer"]} == set(study.ARMS)
    assert all(result["totals"][arm]["tasks"] == 4 for arm in study.ARMS)
    assert result["totals"]["symbolic_growth"]["model_calls"] == 0
    assert result["historical_acquisition"] == {"model_calls": 2, "input_tokens": 100, "output_tokens": 20}
    assert result["library"]["verified_rules"] == 3
    assert result["library"]["transfer_admission_calls"] == 0
    assert all("events" not in row["strategy_audit"] for row in result["transfer"])
    assert all("observations" not in row for row in result["transfer"])
    assert len(list((folder / "cases").glob("*.json"))) == 16
