"""Check benchmark controls and independent scoring without rerunning the study."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json

import pytest

from benchmarks import run_unified_verification_benchmark as benchmark
from benchmarks.unified_bridge_oracle import score_bridge
from resimind import Decision, Task, Verdict
from resimind.domains import algebra, bridge, customer_support as support


def test_manifest_is_repeatable_unique_and_roundtrips_to_domain_inputs():
    manifest = benchmark.make_manifest()
    assert benchmark.canonical(manifest) == benchmark.canonical(benchmark.make_manifest())
    loaded = json.loads(benchmark.canonical(manifest))
    all_ids = []
    for domain in ("mathematics", "bridge", "customer_support"):
        cases = loaded[domain]
        assert len(cases) == 50
        all_ids.extend(case["id"] for case in cases)
        assert benchmark.make_manifest(3)[domain] == manifest[domain][:3]
    assert len(set(all_ids)) == 150
    assert len({case["expression"] for case in loaded["mathematics"]}) == 50
    assert {case["expression"] for case in loaded["mathematics"]}.isdisjoint(loaded["discovery"])
    assert len(set(loaded["stress"])) == len(loaded["stress"])
    for item in loaded["bridge"]:
        problem = benchmark.bridge_problem(item["problem"])
        assert len(problem.case_ids) == 8
    for item in loaded["customer_support"]:
        assert isinstance(benchmark.return_case(item["case"]), support.ReturnCase)


@pytest.mark.parametrize("limit", [0, 51, True, 3.0, "3"])
def test_manifest_rejects_limits_outside_frozen_protocol(limit):
    with pytest.raises(ValueError):
        benchmark.make_manifest(limit)


@pytest.mark.parametrize("domain,runner", [
    ("mathematics", lambda case: benchmark.math_run(case, mutations=True)),
    ("bridge", benchmark.bridge_run),
    ("customer_support", benchmark.support_run),
])
def test_representative_case_passes_independent_score_and_gate_controls(domain, runner):
    row = runner(benchmark.make_manifest(1)[domain][0])
    assert row["correct_completion"] and row["oracle"] == "valid"
    assert row["remaining_obligations"] == 0
    assert row["model_calls"] == 0
    assert row["accepted_steps"] > 0
    assert row["tamper"]["positive_control_accepted"]
    assert row["tamper"]["mutations"] == row["tamper"]["rejected"] == 3
    assert row["tamper"]["false_accepts"] == row["tamper"]["deferred_or_other"] == 0


def test_math_solved_status_does_not_override_independent_failure(monkeypatch):
    monkeypatch.setattr(benchmark, "score_expansion", lambda *_: {"status": "invalid"})
    row = benchmark.math_run(benchmark.make_manifest(1)["mathematics"][0], mutations=True)
    assert row["reported_solved"]
    assert not row["correct_completion"]
    assert "tamper" not in row
    assert benchmark.aggregate([row])["false_solved"] == 1


def test_unknown_oracle_result_is_unscored_and_not_known_false(monkeypatch):
    monkeypatch.setattr(benchmark, "score_expansion", lambda *_: {"status": "unknown"})
    row = benchmark.math_run(benchmark.make_manifest(1)["mathematics"][0])
    assert row["reported_solved"] and not row["correct_completion"]
    summary = benchmark.aggregate([row])
    assert summary["correct_completions"] == 0
    assert summary["false_solved"] == 0
    assert summary["unscored_solved"] == 1


def test_paired_steps_compare_only_independently_completed_case_pairs():
    def row(identifier, complete, steps):
        return {"id": identifier, "correct_completion": complete,
                "reported_solved": True, "accepted_steps": steps}

    before = [row("faster", True, 7), row("same", True, 2), row("slower", True, 2),
              row("new", False, 100), row("lost", True, 200), row("neither", False, 300)]
    after = [row("faster", True, 3), row("same", True, 2), row("slower", True, 4),
             row("new", True, 1), row("lost", False, 1), row("neither", False, 1)]
    summary = benchmark.paired_summary(before, after)
    assert summary["newly_completed"] == summary["lost_completions"] == 1
    assert summary["both_completed"] == 3
    assert summary["fewer_steps"] == summary["same_steps"] == summary["more_steps"] == 1
    assert summary["mean_steps_saved_on_both_completed"] == pytest.approx(2 / 3)
    incomplete_only = benchmark.paired_summary(before[3:], after[3:])
    assert incomplete_only["both_completed"] == 0
    assert incomplete_only["mean_steps_saved_on_both_completed"] is None


def test_paired_summary_rejects_misaligned_case_ids():
    before = [{"id": "case-a"}, {"id": "case-b"}]
    for after in (before[::-1], before[:1], [{"id": "case-a"}, {"id": "case-c"}]):
        with pytest.raises(ValueError, match="same ordered cases"):
            benchmark.paired_summary(before, after)


def test_math_oracle_rejects_a_false_identity_without_production_verifier(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the independent scorer must not use the production identity verifier")

    monkeypatch.setattr(algebra, "verify_identity", forbidden)
    lhs, rhs = "(x+y)**3", "x**3+3*x*x*y+3*x*y*y+y**3"
    assert benchmark.score_expansion(lhs, rhs, ("x", "y"))["status"] == "valid"
    # The added polynomial vanishes at -1, 0 and 1; a few convenient samples
    # must not let a wrong expansion pass a degree-complete oracle.
    mutated = rhs + "+x*(x-1)*(x+1)"
    score = benchmark.score_expansion(lhs, mutated, ("x", "y"))
    assert score["status"] == "invalid"
    assert score["counterexample"]["x"] not in (-1, 0, 1)


@pytest.fixture(scope="module")
def support_result():
    case = support.demo_case()
    return support.build_agent(case).run(Task("scorer-controls", "Assess return", support.DOMAIN))


@pytest.mark.parametrize("decision", [Decision.REJECT, Decision.DEFER, Decision.ACCEPT])
def test_tamper_controls_expose_broken_verifiers(support_result, decision):
    event = next(event for event in support_result.run_result.trace
                 if event.decision is Decision.ACCEPT and event.candidate.action == support.ACTIONS[2])
    payload = {**json.loads(event.candidate.claim), "payment_executed": True}

    class ConstantVerifier:
        def verify(self, candidate, state, residual, evidence):
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence)

    result = benchmark.tamper_check(
        event, support_result.run_result.evidence, ConstantVerifier(), [payload])
    assert result["positive_control_accepted"] is (decision is Decision.ACCEPT)
    if decision is Decision.REJECT:
        assert result["rejected"] == 1
        # Rejecting everything cannot pass the benchmark's valid-input control.
        assert not result["positive_control_accepted"]
    elif decision is Decision.DEFER:
        assert result["rejected"] == 0 and result["deferred_or_other"] == 1
    else:
        assert result["false_accepts"] == 1


@pytest.fixture
def refund_resolution():
    # Hand-specified expected answer, independent of the adapter's renderer.
    return {"case_id": "demo-return-001", "order_id": "demo-order-001",
            "customer_id": "demo-customer-001", "policy_id": "fictional-returns-v1",
            "outcome": "refund_recommended", "refund_cents": 24900,
            "currency": "CNY", "excluded_shipping_cents": 1000,
            "reason": "within_return_window", "next_action": "merchant_refund_review",
            "payment_executed": False}


@pytest.mark.parametrize("delivered,eligible", [
    ("2026-10-08", True), ("2026-09-24", True), ("2026-09-23", False),
])
def test_support_oracle_respects_inclusive_window_boundary(refund_resolution, delivered, eligible):
    case = replace(support.demo_case(), delivered_on=delivered)
    expected = refund_resolution
    if not eligible:
        expected.update(outcome="human_review", refund_cents=None,
                        reason="outside_return_window", next_action="manual_policy_review")
    assert benchmark.score_support(case, expected)
    forged = {**expected, "outcome": "human_review" if eligible else "refund_recommended"}
    assert not benchmark.score_support(case, forged)


def test_support_oracle_uses_declared_window_and_remaining_payment(refund_resolution):
    case = replace(support.demo_case(), policy=support.ReturnPolicy("same-day", 0),
                   delivered_on="2026-10-08", prior_item_refund_cents=1234)
    expected = {**refund_resolution, "policy_id": "same-day", "refund_cents": 23666}
    assert benchmark.score_support(case, expected)
    assert not benchmark.score_support(case, {**expected, "refund_cents": 24900})
    assert not benchmark.score_support(case, {**expected, "refund_cents": 24666})
    yesterday = replace(case, delivered_on="2026-10-07")
    assert not benchmark.score_support(yesterday, expected)


@pytest.mark.parametrize("changes", [
    {"refund_cents": 24900.0}, {"refund_cents": "24900"}, {"refund_cents": True},
    {"refund_cents": 25900}, {"payment_executed": True}, {"payment_executed": 0},
    {"excluded_shipping_cents": 1000.0}, {"customer_id": "someone-else"},
    {"order_id": "another-order"}, {"outcome": "refund_completed"}, {"extra": "ignore me"},
])
def test_support_oracle_rejects_financial_identity_and_execution_forgery(refund_resolution, changes):
    assert benchmark.score_support(support.demo_case(), refund_resolution)
    assert not benchmark.score_support(support.demo_case(), {**refund_resolution, **changes})


def test_support_oracle_does_not_equate_false_with_zero_refund(refund_resolution):
    case = replace(support.demo_case(), prior_item_refund_cents=24900)
    expected = {**refund_resolution, "refund_cents": 0}
    assert benchmark.score_support(case, expected)
    assert not benchmark.score_support(case, {**expected, "refund_cents": False})


def test_support_oracle_requires_identity_review_without_financial_disclosure(refund_resolution):
    case = replace(support.demo_case(), request_customer_id="different-customer")
    expected = {**refund_resolution, "customer_id": "different-customer", "outcome": "human_review",
                "reason": "request_identity_mismatch", "next_action": "manual_policy_review",
                "refund_cents": None, "excluded_shipping_cents": None}
    assert benchmark.score_support(case, expected)
    assert not benchmark.score_support(case, {**expected, "excluded_shipping_cents": 1000})
    assert not benchmark.score_support(case, {**expected, "refund_cents": 24900})
    assert not benchmark.score_support(case, {**expected, "customer_id": case.customer_id})


def test_support_oracle_never_scores_missing_delivery_as_completed(refund_resolution):
    case = replace(support.demo_case(), delivered_on=None)
    assert not benchmark.score_support(case, refund_resolution)
    assert not benchmark.score_support(case, {})


@pytest.fixture(scope="module")
def bridge_answer():
    problem = bridge.demo_problem()
    result = bridge.build_agent(problem).run(Task("bridge-oracle", "Check bridge", bridge.DOMAIN))
    return problem, bridge.verified_case_results(result), bridge.verified_summary(result)


def test_bridge_oracle_positive_control_has_every_declared_load_case(bridge_answer):
    problem, cases, summary = bridge_answer
    assert set(cases) == set(problem.case_ids)
    assert len(cases) == 8
    assert score_bridge(problem, cases, summary)


@pytest.mark.parametrize("field", ["q", "pier_moment", "reactions", "v1", "v2"])
def test_bridge_oracle_rejects_tampered_case_certificate(bridge_answer, field):
    problem, cases, summary = bridge_answer
    forged = deepcopy(cases)
    row = forged[next(iter(forged))]
    if field == "pier_moment":
        row[field] += Fraction(1, 1000000)
    else:
        row[field] = (row[field][0] + Fraction(1, 1000000),) + row[field][1:]
    assert not score_bridge(problem, forged, summary)


def test_bridge_oracle_rejects_missing_or_invented_load_case(bridge_answer):
    problem, cases, summary = bridge_answer
    forged = deepcopy(cases)
    row = forged.pop(next(iter(forged)))
    assert not score_bridge(problem, forged, summary)
    forged["invented:both"] = row
    assert not score_bridge(problem, forged, summary)


@pytest.mark.parametrize("alteration", ["value", "location", "case", "case_list", "comparison", "boolean_type"])
def test_bridge_oracle_rejects_tampered_envelope_and_comparisons(bridge_answer, alteration):
    problem, cases, summary = bridge_answer
    forged = deepcopy(summary)
    peak = forged["envelope"]["positive_max"][0]
    if alteration == "value":
        peak["value"] = str(Fraction(peak["value"]) + Fraction(1, 1000000))
    elif alteration == "location":
        peak["x"] = str(Fraction(peak["x"]) + Fraction(1, 1000000))
    elif alteration == "case":
        peak["case"] = next(case for case in cases if case != peak["case"])
    elif alteration == "case_list":
        forged["envelope"]["cases"].pop()
    else:
        key = "moment_within_supplied_limit"
        original = forged["comparisons"][key]
        forged["comparisons"][key] = not original if alteration == "comparison" else int(original)
    assert not score_bridge(problem, cases, forged)


def test_bridge_oracle_distinguishes_exact_numbers_and_false_equal_types(bridge_answer):
    problem, cases, summary = bridge_answer
    forged = deepcopy(cases)
    row = forged[next(iter(forged))]
    row["q"] = tuple(float(value) for value in row["q"])
    assert not score_bridge(problem, forged, summary)


@pytest.mark.parametrize("changes", [{"load_unit": "Npm"}, {"linear_elastic": None}, {"spans": (None, 30)}])
def test_bridge_oracle_does_not_silently_score_unsupported_inputs(bridge_answer, changes):
    problem, cases, summary = bridge_answer
    with pytest.raises(ValueError):
        score_bridge(replace(problem, **changes), cases, summary)
