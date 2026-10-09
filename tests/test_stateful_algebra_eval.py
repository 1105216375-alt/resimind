"""Frozen inputs, controller outcomes and action accounting, without live calls."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from benchmarks import run_stateful_algebra_eval as study
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
            name, "algebra", "polynomial_identity",
            {"lhs": lhs, "rhs": rhs, "variables": ["u", "v"]},
            derivation=({"lhs": lhs, "rhs": rhs},), source_task_id=f"discover-{name}"))
        assert record.status == "verified"
    return store


@pytest.fixture
def prior(tmp_path):
    folder = tmp_path / "prior"
    folder.mkdir()
    data = _snapshot(library()).encode()
    (folder / "knowledge.json").write_bytes(data)
    # A fixed unit-test seed, unrelated to formal study sampling.
    manifest = {"study": "structured-residual-fresh-algebra-v3", "development": False,
                "cases": [asdict(case) for case in generate_cases(424242)]}
    manifest["manifest_sha256"] = digest(manifest)
    write_json(folder / "manifest.json", manifest)
    write_json(folder / "summary.json", {
        "manifest_sha256": manifest["manifest_sha256"],
        "library": {"snapshot_sha256": hashlib.sha256(data).hexdigest()},
        # Synthetic accounting values, not measurements from a live study.
        "discovery_totals": {"model_calls": 2, "input_tokens": 100, "output_tokens": 20},
    })
    return folder


@pytest.fixture
def frozen_environment(monkeypatch):
    monkeypatch.setattr(study, "sources", lambda: {"fixture.py": "fixed-source-hash"})
    monkeypatch.setattr(study, "snapshot_sources", lambda folder, hashes: Path(folder).mkdir(parents=True))
    monkeypatch.setattr(study.subprocess, "check_output", lambda *args, **kwargs: "fixture-commit\n")
    monkeypatch.setattr(study.secrets, "token_hex", lambda size: "de" * size)


def _replace_manifest(folder, edit):
    path = folder / "manifest.json"
    value = json.loads(path.read_text())
    edit(value)
    value["manifest_sha256"] = digest({k: v for k, v in value.items() if k != "manifest_sha256"})
    write_json(path, value)


def test_prior_library_and_complete_exclusion_inventory_precede_new_seed(
        tmp_path, prior, frozen_environment, monkeypatch):
    folder = tmp_path / "new-study"
    prior_value, prior_data = study.read_prior(prior)
    order = []
    original_read = study.read_prior
    original_generate = study.generate_cases

    def read_prior(path):
        order.append("prior_verified")
        return original_read(path)

    def snapshot(path, hashes):
        order.append("sources_snapshotted")
        Path(path).mkdir(parents=True)

    def seed(size):
        assert order == ["prior_verified", "sources_snapshotted"]
        assert (folder / "knowledge.json").read_bytes() == prior_data
        assert len(prior_value["cases"]) == 32
        order.append("seed")
        return "de" * size

    def generate(seed, per_group, *, exclude_expressions):
        assert order[-1] == "seed"
        assert exclude_expressions == [case["expression"] for case in prior_value["cases"]]
        order.append("sample")
        return original_generate(seed, per_group, exclude_expressions=exclude_expressions)

    monkeypatch.setattr(study, "read_prior", read_prior)
    monkeypatch.setattr(study, "snapshot_sources", snapshot)
    monkeypatch.setattr(study.secrets, "token_hex", seed)
    monkeypatch.setattr(study, "generate_cases", generate)
    manifest = study.freeze(folder, prior)
    assert order == ["prior_verified", "sources_snapshotted", "seed", "sample"]
    assert len(manifest["cases"]) == 32 and manifest["repeats"] == 2
    assert manifest["prior"]["exclusions"] == prior_value["exclusions"]
    monkeypatch.setattr(study, "generate_cases", original_generate)
    assert study.check_frozen(folder)["manifest_sha256"] == manifest["manifest_sha256"]


@pytest.mark.parametrize("mutation, message", [
    (lambda value: value["settings"].update(max_calls_per_task=9), "Protocol changed"),
    (lambda value: value["cases"][0].update(expression="999"), "Cases differ"),
    (lambda value: value["prior"].update(exclusions={}), "exclusion inventory changed"),
    (lambda value: value["comparisons"].reverse(), "Protocol changed"),
])
def test_configuration_cases_and_exclusions_are_reconstructed_even_with_rehashed_manifest(
        tmp_path, prior, frozen_environment, mutation, message):
    folder = tmp_path / "study"
    study.freeze(folder, prior)
    _replace_manifest(folder, mutation)
    with pytest.raises(ValueError, match=message):
        study.check_frozen(folder)


def test_frozen_library_and_implementation_changes_are_detected(tmp_path, prior, frozen_environment, monkeypatch):
    folder = tmp_path / "study"
    study.freeze(folder, prior)
    data = (folder / "knowledge.json").read_bytes()
    (folder / "knowledge.json").write_bytes(data + b"\n")
    with pytest.raises(ValueError, match="Frozen library changed"):
        study.check_frozen(folder)
    (folder / "knowledge.json").write_bytes(data)
    monkeypatch.setattr(study, "sources", lambda: {"fixture.py": "changed"})
    with pytest.raises(ValueError, match="Implementation changed"):
        study.check_frozen(folder)


def test_prior_input_requires_formal_manifest_bound_summary_and_unchanged_library(prior):
    summary_path = prior / "summary.json"
    summary = json.loads(summary_path.read_text())
    write_json(summary_path, {**summary, "manifest_sha256": "wrong"})
    with pytest.raises(ValueError, match="intact completed formal v3"):
        study.read_prior(prior)
    write_json(summary_path, summary)
    path = prior / "knowledge.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="snapshot differs"):
        study.read_prior(prior)


def test_development_uses_only_prespecified_old_cases_and_cannot_overwrite_results(
        tmp_path, prior, frozen_environment):
    folder = tmp_path / "dev"
    manifest = study.freeze(folder, prior, development=True)
    assert [case["id"] for case in manifest["cases"]] == list(study.DEVELOPMENT_IDS)
    assert manifest["development"] and manifest["repeats"] == 1
    assert manifest["settings"]["max_physical_calls"] == 128
    with pytest.raises(ValueError, match="already frozen"):
        study.freeze(folder, prior)
    write_json(folder / "summary.json", {})
    with pytest.raises(ValueError, match="Completed study exists"):
        study.run_study(folder, None)


class Script:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []

    def __call__(self, prompt):
        payload = json.loads(prompt)
        self.prompts.append(payload)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response(payload) if callable(response) else response


def state_candidate(prompt, *, action="rewrite_polynomial", after=ANSWER, rule=None, stale=False):
    claim = {"state_revision": prompt["state_revision"] + int(stale),
             "state_fingerprint": prompt["state_fingerprint"],
             "rule_id": "" if rule is None else rule["id"]}
    if action == "apply_verified_rule":
        claim["rule_fingerprint"] = rule["fingerprint"]
    else:
        claim["after"] = after
    return json.dumps({"id": "proposal", "action": action, "target": "algebra:expanded",
                       "claim": json.dumps(claim), "refs": prompt["required_reference_ids"]})


@pytest.mark.parametrize("arm", ["state_bound_growth", "executable_growth"])
def test_stale_state_rejection_is_counted_and_recovery_uses_another_ordinary_call(arm):
    script = Script([lambda prompt: state_candidate(prompt, stale=True), state_candidate])
    result = study.production_run(CASE, library(), script, 8, arm=arm)
    assert result["output"] == ANSWER
    assert result["state_binding_rejections"] == 1
    assert result["work"]["proposal_calls"] == 2
    assert result["accepted_steps"] == 1 and result["rule_action_calls"] == 0
    assert script.prompts[1]["state_revision"] == 0


def test_explicit_rule_attempts_count_failures_accepts_and_provenance():
    def selected(prompt, *, forged=False):
        rule = next(rule for rule in prompt["available_verified_identities"] if rule["id"] == "square")
        if forged:
            rule = {**rule, "fingerprint": "forged"}
        return state_candidate(prompt, action="apply_verified_rule", rule=rule)

    script = Script([lambda prompt: selected(prompt, forged=True), selected])
    result = study.production_run(CASE, library(), script, 8, arm="executable_growth")
    assert result["output"] is not None
    assert result["rule_action_calls"] == result["work"]["proposal_calls"] == 2
    assert result["rule_action_rejections"] == result["rule_action_accepts"] == 1
    assert len(result["committed_rule_uses"]) == 1
    assert result["committed_rule_uses"][0]["source_task_id"] == "discover-square"
    assert result["admissions"] == []


def test_rule_executor_and_manual_legacy_follow_the_same_single_first_match():
    case = Case("two-squares", "(x+1)**2+(x+2)**2", ("x",), "fixture-family")
    after = "x**2+2*x*1+1**2+(x+2)**2"
    store = library()
    manual = Script([json.dumps({"id": "manual", "action": "rewrite_polynomial", "target": "algebra:expanded",
                                 "claim": json.dumps({"before": case.expression, "after": after, "rule_id": "square"}),
                                 "refs": ["polynomial:input"]}), "null"])
    def execute(prompt):
        rule = next(rule for rule in prompt["available_verified_identities"] if rule["id"] == "square")
        return state_candidate(prompt, action="apply_verified_rule", rule=rule)
    executable = Script([execute, "null"])
    legacy = study.production_run(case, store, manual, 8, arm="legacy_structured_growth")
    selected = study.production_run(case, store, executable, 8, arm="executable_growth")
    assert legacy["output"] is selected["output"] is None
    assert legacy["accepted_steps"] == selected["accepted_steps"] == 1
    assert legacy["committed_rule_uses"] == selected["committed_rule_uses"]
    assert study.normalized(manual.prompts[1]["state"]["facts"][-1]["value"][1]) == study.normalized(after)
    assert study.normalized(executable.prompts[1]["current_expression"]) == study.normalized(after)
    assert selected["rule_action_calls"] == selected["rule_action_accepts"] == 1
    assert selected["work"]["proposal_calls"] == 2  # The second call explicitly abstains.


class Client:
    def __init__(self, response):
        self.response = response
        self.chat = NS(completions=NS(create=self.create))

    def with_options(self, **kwargs):
        return self

    def create(self, **kwargs):
        if isinstance(self.response, Exception):
            raise self.response
        return NS(model="test", id="test", usage=NS(prompt_tokens=7, completion_tokens=4, total_tokens=11),
                  choices=[NS(finish_reason="stop", message=NS(content=self.response))])


@pytest.mark.parametrize("arm", study.ARMS)
@pytest.mark.parametrize("response, expected", [
    ("null", "abstained"), ("{bad_json", "schema"), (ValueError("private exception"), "transport"),
])
def test_all_arm_null_schema_and_transport_failures_remain_in_results(tmp_path, arm, response, expected):
    recorder = RecordedCompletion(tmp_path, study.SETTINGS, Client(response), study.SYSTEM)
    result = study.run_case(CASE, arm, 0, recorder, library(), phase="test")
    assert result["model_calls"] == 1 and not result["success"]
    assert not result["controller_failure"] and not result["invalid_delivered"]
    assert result["schema_failure"] == (expected == "schema")
    assert result["technical_failure"] == (expected == "transport")
    if expected == "abstained":
        assert result["status"] == "abstained"
    if expected == "transport":
        assert result["usage"]["total_tokens"] is None
    assert study.totals([result])["tasks"] == 1
    assert study.totals([result])["rule_action_calls"] == 0


def test_action_rejection_duplicates_are_state_scoped_and_ignore_candidate_ids():
    def event(step, claim, decision="reject", reasons=None):
        return {"step": step, "decision": decision, "reasons": reasons or ["polynomial_identity_not_proved"],
                "candidate": {"id": f"different-{step}", "action": "rewrite_polynomial",
                              "target": "algebra:expanded", "claim": claim}}
    claim = json.dumps({"before": "(x+1)**2", "after": "999", "rule_id": ""})
    formatted = json.dumps({"before": " (x + 1) ** 2 ", "after": " (999) ", "rule_id": ""})
    result = study.add_action_metrics({"observations": [event(1, claim), event(2, formatted),
                                                         event(3, claim, decision="accept"), event(4, claim)]})
    assert result["repeat_rejections"] == 1
    assert result["rule_action_calls"] == 0
