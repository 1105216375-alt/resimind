"""Proposal syntax is explicit; invalid notation stays invalid until corrected."""
import ast
import json

from resimind import Decision, Task
from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent
from resimind.domains.algebra import AlgebraVerifier
from resimind.domains.polynomial_learning import DOMAIN, PolynomialProblem
from resimind.knowledge import KnowledgeLibrary


def run(complete, *, calls):
    library = KnowledgeLibrary({DOMAIN: AlgebraVerifier()})
    stats = AdaptiveStats()
    result = build_adaptive_learning_agent(
        PolynomialProblem("(q+3)*(q+4)", ("q",)), library,
        complete=complete, stats=stats, max_local_work=0,
        max_model_calls=calls, max_steps=calls + 1,
    ).run(Task("public-syntax-feedback", "Expand", DOMAIN))
    return result, stats, library


def test_initial_prompt_specifies_python_power_and_explicit_multiplication():
    prompts = []

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        return '{"after":"q**2+7*q+12"}'

    result, stats, _ = run(complete, calls=1)
    instruction = prompts[0]["instruction"]
    assert "Python-style powers '**'" in instruction and "never '^'" in instruction
    assert "explicit multiplication '*'" in instruction
    assert "2*x" in instruction and "x*y" in instruction
    assert result.result.run_result.status == "solved"
    assert stats.model_calls == 1


def test_caret_is_rejected_unchanged_then_feedback_guides_a_real_correction():
    prompts = []
    invalid = "q^2+7*q+12"

    def complete(raw):
        prompt = json.loads(raw)
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps({"after": invalid})
        assert prompt["state_revision"] == 0
        # The retained, bounded rejection diagnostic must include an executable
        # format correction, not merely say that the expression is unsupported.
        reasons = " ".join(reason for item in prompt["recent_diagnostics"] for reason in item["reasons"])
        assert "rewrite_after_unsupported" in reasons
        assert "'**'" in reasons and "never '^'" in reasons
        assert "explicit multiplication '*'" in reasons
        return '{"after":"q**2+7*q+12"}'

    result, stats, library = run(complete, calls=2)
    outcome = result.result.run_result
    first = outcome.trace[0]
    assert first.decision is Decision.REJECT and first.after.facts == ()
    assert json.loads(first.candidate.claim)["after"] == invalid
    assert outcome.status == "solved" and stats.model_calls == len(prompts) == 2
    assert len(outcome.state.facts) == 1
    assert ast.dump(ast.parse(outcome.state.facts[0].value[1], mode="eval")) == ast.dump(
        ast.parse("q**2+7*q+12", mode="eval"))
    assert len(result.admissions) == len(library.lookup(DOMAIN)) == 1


def test_implicit_multiplication_is_not_silently_inserted_or_committed():
    invalid = "q**2+7q+12"
    replies = []

    def complete(raw):
        replies.append(raw)
        return json.dumps({"after": invalid})

    result, stats, library = run(complete, calls=1)
    outcome = result.result.run_result
    assert outcome.status != "solved" and outcome.state.facts == ()
    assert len(replies) == stats.model_calls == stats.schema_failures == 1
    assert "model_response_schema_or_syntax" in stats.events[0]["reasons"]
    assert result.admissions == () and library.lookup(DOMAIN) == ()
