"""Controller comparisons: use adversarial scripted responses, never paid APIs."""
import json
from types import SimpleNamespace as NS

import pytest

from benchmarks import run_algebra_live_eval as study
from benchmarks.knowledge_growth import Case
from benchmarks.live_eval_support import RecordedCompletion, RequestFailure
from resimind.domains.algebra import AlgebraVerifier
from resimind.knowledge import KnowledgeLibrary


CASE = Case("probe", "(x+1)**2", ("x",), "test")
ANSWER = "x*x+2*x+1"


def library():
    return KnowledgeLibrary({"algebra": AlgebraVerifier()})


def candidate(before=CASE.expression, after=ANSWER, refs=None):
    return json.dumps(dict(id="step", action="rewrite_polynomial", target="algebra:expanded",
                           claim=json.dumps(dict(before=before, after=after, rule_id="")),
                           refs=refs or ["polynomial:input"]))


class Script:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(json.loads(prompt))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_react_really_exposes_observation_then_lets_model_deliver():
    complete = Script([json.dumps(dict(action="check_identity", expression="999")),
                       json.dumps(dict(action="final", expression=ANSWER))])
    result = study.baseline(CASE, "react", complete, 8)
    assert result["output"] == ANSWER and result["work"]["identity_checks"] == 1
    assert complete.prompts[1]["history"][0]["observation"]["identity"] == "rejected"


def test_retry_rejects_wrong_identity_and_incomplete_answer_before_delivery():
    complete = Script([json.dumps(dict(action="final", expression=text))
                       for text in ("999", CASE.expression, ANSWER)])
    result = study.baseline(CASE, "verify_retry", complete, 8)
    assert result["output"] == ANSWER and result["work"]["identity_checks"] == 3
    assert complete.prompts[2]["history"][-1]["observation"]["expanded"] is False


@pytest.mark.parametrize("arm", study.ARMS)
@pytest.mark.parametrize("response,expected", [("null", "abstained"), ("{bad json", "failure")])
def test_all_arms_stop_on_first_abstention_or_schema_failure(arm, response, expected):
    complete = Script([response])
    result = (study.baseline(CASE, arm, complete, 8) if arm in study.ARMS[:2]
              else study.residual(CASE, library(), complete, 8))
    assert len(complete.prompts) == 1 and result["output"] is None
    assert result["status"] == "abstained" if expected == "abstained" else result["status"] in ("schema_failure", "error")


def test_production_residual_keeps_verified_partial_state_and_rejects_wrong_next_step():
    partial = "x*(x+1)+1*(x+1)"
    complete = Script([candidate(after=partial),
                       candidate(before=partial, after="999", refs=["polynomial:input", "polynomial:step:1"]),
                       candidate(before=partial, refs=["polynomial:input", "polynomial:step:1"])])
    result = study.residual(CASE, library(), complete, 8)
    assert result["output"] == ANSWER and result["accepted_steps"] == 2
    assert complete.prompts[2]["last_feedback"]["decision"] == "reject"
    assert len(complete.prompts[2]["state"]["facts"]) == 1


def test_checker_feedback_never_contains_independent_oracle_output():
    result = study.check_answer(CASE, "999")
    assert set(result) == {"identity", "reason", "expanded"}
    assert "counterexample" not in result and "points_checked" not in result


class Client:
    def __init__(self, text, *, usage=True):
        self.text, self.usage = text, usage
        self.chat = NS(completions=NS(create=self.create))

    def with_options(self, **kwargs):
        return self

    def create(self, **kwargs):
        if isinstance(self.text, Exception):
            raise self.text
        return NS(model="test", id="one", usage=NS(prompt_tokens=7, completion_tokens=4, total_tokens=11) if self.usage else None,
                  choices=[NS(finish_reason="stop", message=NS(content=self.text))])


def test_unexpanded_react_delivery_is_visible_as_unsuccessful_not_wrong_identity(tmp_path):
    recorder = RecordedCompletion(tmp_path, study.SETTINGS,
                                  Client(json.dumps(dict(action="final", expression=CASE.expression))), study.SYSTEM)
    result = study.run_case(CASE, "react", recorder, library(), phase="test")
    assert result["oracle"]["status"] == "incomplete"
    assert result["unsuccessful_delivered"] and not result["invalid_delivered"]
    assert study.totals([result])["unsuccessful_delivered"] == 1


def test_missing_token_usage_stays_unknown_in_case_and_arm_totals(tmp_path):
    recorder = RecordedCompletion(tmp_path, study.SETTINGS, Client("null", usage=False), study.SYSTEM)
    result = study.run_case(CASE, "verify_retry", recorder, library(), phase="test")
    assert result["usage"]["input_tokens"] is None
    assert study.totals([result])["input_tokens"] is None


@pytest.mark.parametrize("arm", study.ARMS)
def test_transport_failure_stops_each_arm_and_cannot_be_successful_abstention(tmp_path, arm):
    recorder = RecordedCompletion(tmp_path, study.SETTINGS, Client(ValueError("private")), study.SYSTEM)
    result = study.run_case(CASE, arm, recorder, library(), phase="test")
    assert result["technical_failure"] and not result["success"]
    assert result["api_attempts"] == 1 and result["status"] != "abstained"


def test_manifest_freezes_sources_and_detects_tampering(tmp_path, monkeypatch):
    study.freeze(tmp_path)
    assert study.check_frozen(tmp_path)["study"] == "same-model-algebra-v1"
    with pytest.raises(ValueError, match="already exists"):
        study.freeze(tmp_path)
    monkeypatch.setattr(study, "GOAL", "changed after freeze")
    with pytest.raises(ValueError, match="changed"):
        study.check_frozen(tmp_path)


def test_completed_run_cannot_overwrite_original_timing(tmp_path):
    study.freeze(tmp_path)
    (tmp_path / "summary.json").write_text("{}")
    with pytest.raises(ValueError, match="original timing"):
        study.run_study(tmp_path, None)
