"""Tests of statistical denominators, paired inference and honest reporting."""
from copy import deepcopy

import pytest

from benchmarks.report_planning_eval import render_report, summarize


def arm(outcome="valid_plan", *, cost=0.01):
    delivered = outcome in ("valid_plan", "invalid_plan")
    return dict(outcome=outcome, delivered=delivered, semantic_valid=outcome == "valid_plan",
                oracle={"status": "valid" if outcome == "valid_plan" else "invalid", "violations": [], "missing": []},
                contract_errors=[], logical_calls=1, input_tokens=10, output_tokens=20,
                model_seconds=2.0, elapsed_seconds=2.5, list_price_usd=cost, request_ids=["shared-first"])


def row(index, expected="feasible", outcomes=("valid_plan", "valid_plan", "valid_plan")):
    return dict(case_id=f"case-{index}", description=f"案例 {index}", expected_outcome=expected,
                arms={name: arm(outcome) for name, outcome in zip(("direct", "self_review", "resimind"), outcomes)})


def test_paired_counts_and_exact_two_sided_mcnemar():
    rows = [row(i, outcomes=("invalid_plan", "invalid_plan", "valid_plan")) for i in range(6)]
    rows.extend([row(6), row(7, outcomes=("explicit_abstention",) * 3)])
    result = summarize(rows)
    paired = result["paired"]["resimind_vs_self_review"]
    assert paired == dict(feasible_cases=8, wins=6, losses=0, ties=2, both_success=1, both_failure=1,
                          discordant_pairs=6, exact_mcnemar_p=0.03125)
    metric = result["arms"]["resimind"]["feasible_success"]
    assert metric["numerator"] == 7 and metric["denominator"] == 8
    assert metric["wilson95"] == pytest.approx([0.5291118177871464, 0.9775825085499433])


def test_negative_withhold_excludes_technical_failure_and_preserves_categories():
    rows = [row(0, "infeasible", ("explicit_abstention", "technical_failure", "verifier_withheld")),
            row(1, "missing_evidence", ("invalid_plan", "explicit_abstention", "technical_failure"))]
    result = summarize(rows)
    for name in ("direct", "self_review", "resimind"):
        assert result["arms"][name]["controlled_withhold"] == dict(numerator=1, denominator=2, rate=0.5)
    assert result["arms"]["resimind"]["technical_failure"]["numerator"] == 1
    assert result["case_counts"] == dict(total=2, feasible=0, infeasible=1, missing_evidence=1, negative=2)
    assert result["arms"]["resimind"]["by_expected_outcome"]["infeasible"]["outcomes"]["verifier_withheld"] == 1
    assert result["arms"]["resimind"]["feasible_success"]["wilson95"] is None


def test_contract_error_counts_as_invalid_delivery_even_if_semantic_plan_is_valid():
    case = row(0)
    case["arms"]["direct"].update(outcome="invalid_plan", contract_errors=["evidence_references"])
    result = summarize([case])["arms"]["direct"]
    assert result["feasible_success"]["numerator"] == 0
    assert result["invalid_delivery"]["numerator"] == 1
    assert result["contract_invalid_delivery"]["numerator"] == 1
    assert result["semantic_invalid_delivery"]["numerator"] == 0


def test_missing_usage_never_becomes_zero_or_complete_cost():
    rows = [row(0), row(1)]
    rows[1]["arms"]["resimind"].update(input_tokens=None, list_price_usd=None)
    result = summarize(rows)["arms"]["resimind"]["costs"]
    assert result["input_tokens"] == dict(total=None, mean=None, known_count=1, missing_count=1, known_total=10)
    assert result["list_price_usd"]["total"] is None
    assert result["logical_calls"]["total"] == 2
    assert result["output_tokens"]["mean"] == 20


def test_report_separates_shared_logical_cost_and_physical_requests():
    rows = [row(i) for i in range(8)]
    rows.extend(row(i, "infeasible" if i < 10 else "missing_evidence", ("explicit_abstention",) * 3) for i in range(8, 12))
    summary = summarize(rows)
    report = render_report(summary, rows, dict(model="test-model", physical_calls=12,
                           physical_usage=dict(input_tokens=120, output_tokens=240), physical_list_price_usd=0.12,
                           baseline_commit="abcdef", manifest_sha256="012345", timestamp="2026-10-09T00:00:00Z"))
    assert "难度天花板" in report
    assert "直接生成是 1 次调用" in report and "均以 3 次调用为上限" in report
    assert "不等于相同实际调用次数、token 或成本" in report
    assert "重复计算共享请求" in report
    assert "实际物理请求：12 次" in report
    assert "USD 0.120000" in report
    assert "[case-0](runs/case-01.json)" in report
    assert "[case-11](runs/case-12.json)" in report
    assert "明确放弃" in report and "未提交" in report
    assert "不代表模型证明了问题不可行" in report
    assert "0 次无效交付不等于零风险保证" in report
    assert "不能据此证明统计优势" in report
    assert "p=1" in report


def test_technical_failure_is_visible_and_metadata_unknown_not_fabricated():
    rows = [row(0, outcomes=("technical_failure", "invalid_plan", "verifier_withheld"))]
    rows[0]["arms"]["direct"]["technical_error"] = "timeout | no response\nnext"
    report = render_report(summarize(rows), rows, {})
    assert "技术失败" in report and "timeout \\| no response next" in report
    assert "实际物理请求：未知 次" in report
    assert "标价估算 USD 未知" in report
    assert "无效交付" in report
    assert "先保留失败记录" in report


def test_empty_result_is_not_a_perfect_run():
    result = summarize([])
    assert result["arms"]["resimind"]["feasible_success"]["rate"] is None
    assert result["arms"]["resimind"]["costs"]["input_tokens"]["mean"] is None
    assert result["paired"]["resimind_vs_self_review"]["exact_mcnemar_p"] == 1.0
    assert "尚无已评分案例" in render_report(result, [], {})


def test_aggregation_does_not_mutate_evidence_and_validates_pairing():
    rows = [row(0)]
    original = deepcopy(rows)
    summarize(rows)
    assert rows == original
    with pytest.raises(ValueError, match="Duplicate"):
        summarize(rows + rows)
    del rows[0]["arms"]["resimind"]
    with pytest.raises(ValueError, match="Missing arm"):
        summarize(rows)


def test_nonfinite_cost_does_not_leak_nan_into_json():
    rows = [row(0)]
    rows[0]["arms"]["resimind"]["list_price_usd"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        summarize(rows)
