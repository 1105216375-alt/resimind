"""Standard-library aggregation for the frozen, paired planning pilot.

This module never queries a model or the production verifier. It reports scored
rows, including failures and unknown usage, without selecting successful cases.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any

ARMS = ("direct", "self_review", "resimind")
ARM_LABELS = {"direct": "直接生成", "self_review": "模型自检", "resimind": "ResiMind"}
EXPECTED_LABELS = {"feasible": "可行", "infeasible": "已知不可行", "missing_evidence": "证据不足"}
OUTCOME_LABELS = {
    "valid_plan": "有效方案",
    "invalid_plan": "无效方案",
    "explicit_abstention": "明确放弃",
    "verifier_withheld": "未提交",
    "technical_failure": "技术失败",
}
COST_FIELDS = ("logical_calls", "input_tokens", "output_tokens", "model_seconds", "elapsed_seconds", "list_price_usd")


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def _wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.959963984540054
    proportion = successes / total
    divisor = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / divisor
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / divisor
    return [max(0.0, center - radius), min(1.0, center + radius)]


def _success(arm: dict[str, Any]) -> bool:
    return bool(arm["delivered"] and arm["semantic_valid"]
                and arm["outcome"] == "valid_plan" and not arm.get("contract_errors"))


def _cost(values: list[int | float | None]) -> dict[str, Any]:
    known = [value for value in values if value is not None]
    if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in known):
        raise ValueError("Usage and cost values must be finite nonnegative numbers or null")
    complete = len(known) == len(values)
    return {"total": sum(known) if complete else None,
            "mean": sum(known) / len(values) if complete and values else None,
            "known_count": len(known), "missing_count": len(values) - len(known),
            "known_total": sum(known)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return JSON-serializable descriptive metrics; unknown totals stay null.

    Success requires a delivered, semantically valid, contract-valid plan. A
    controlled withhold requires explicit abstention or verifier withholding;
    technical failures are counted in the denominator but never as withholding.
    """
    identities = [row["case_id"] for row in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate case_id would invalidate paired counts")
    for row in rows:
        if row["expected_outcome"] not in EXPECTED_LABELS:
            raise ValueError("Unknown expected_outcome")
        for name in ARMS:
            if name not in row["arms"]:
                raise ValueError(f"Missing arm: {name}")
            if row["arms"][name]["outcome"] not in OUTCOME_LABELS:
                raise ValueError("Unknown outcome")
    counts = Counter(row["expected_outcome"] for row in rows)
    count_summary = {"total": len(rows), **{name: counts[name] for name in EXPECTED_LABELS},
                     "negative": counts["infeasible"] + counts["missing_evidence"]}
    feasible = [row for row in rows if row["expected_outcome"] == "feasible"]
    negative = [row for row in rows if row["expected_outcome"] != "feasible"]
    arm_summaries = {}
    for name in ARMS:
        arms = [row["arms"][name] for row in rows]
        success_count = sum(_success(row["arms"][name]) for row in feasible)
        delivered = sum(bool(arm["delivered"]) for arm in arms)
        invalid = sum(bool(arm["delivered"]) and not _success(arm) for arm in arms)
        semantic_invalid = sum(bool(arm["delivered"]) and not arm["semantic_valid"] for arm in arms)
        contract_invalid = sum(bool(arm["delivered"]) and bool(arm.get("contract_errors")) for arm in arms)
        controlled = sum(not row["arms"][name]["delivered"] and row["arms"][name]["outcome"]
                         in ("explicit_abstention", "verifier_withheld") for row in negative)
        outcomes = Counter(arm["outcome"] for arm in arms)
        by_expected = {}
        for expected in EXPECTED_LABELS:
            subset = [row["arms"][name] for row in rows if row["expected_outcome"] == expected]
            subgroup = Counter(arm["outcome"] for arm in subset)
            by_expected[expected] = {"cases": len(subset),
                                     "outcomes": {label: subgroup[label] for label in OUTCOME_LABELS}}
        arm_summaries[name] = {
            "feasible_success": {**_rate(success_count, len(feasible)), "wilson95": _wilson(success_count, len(feasible))},
            "delivery": _rate(delivered, len(rows)),
            "invalid_delivery": _rate(invalid, len(rows)),
            "invalid_among_delivered": _rate(invalid, delivered),
            "semantic_invalid_delivery": _rate(semantic_invalid, len(rows)),
            "contract_invalid_delivery": _rate(contract_invalid, len(rows)),
            "technical_failure": _rate(outcomes["technical_failure"], len(rows)),
            "controlled_withhold": _rate(controlled, len(negative)),
            "outcomes": {label: outcomes[label] for label in OUTCOME_LABELS},
            "by_expected_outcome": by_expected,
            "costs": {field: _cost([arm.get(field) for arm in arms]) for field in COST_FIELDS},
        }
    pairs = [(_success(row["arms"]["resimind"]), _success(row["arms"]["self_review"])) for row in feasible]
    wins = sum(resimind and not review for resimind, review in pairs)
    losses = sum(review and not resimind for resimind, review in pairs)
    discordant = wins + losses
    p_value = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(wins, losses) + 1)) / (2 ** discordant)) if discordant else 1.0
    paired = {"feasible_cases": len(feasible), "wins": wins, "losses": losses,
              "ties": len(feasible) - wins - losses,
              "both_success": sum(a and b for a, b in pairs),
              "both_failure": sum(not a and not b for a, b in pairs),
              "discordant_pairs": discordant, "exact_mcnemar_p": p_value}
    return {"schema_version": 1, "case_counts": count_summary, "arms": arm_summaries,
            "paired": {"resimind_vs_self_review": paired}}


def _text(value: Any) -> str:
    if value is None:
        return "未知"
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _number(value: int | float | None, digits: int = 2) -> str:
    if value is None:
        return "未知"
    if type(value) is int:
        return f"{value:,}"
    return f"{value:,.{digits}f}"


def _fraction(metric: dict[str, Any]) -> str:
    numerator, denominator = metric["numerator"], metric["denominator"]
    return f"{numerator}/{denominator}（{metric['rate']:.1%}）" if denominator else "不适用（0 例）"


def _total_mean(metric: dict[str, Any], digits: int = 2) -> str:
    return f"{_number(metric['total'], digits)} / {_number(metric['mean'], digits)}"


def _recommendation(summary: dict[str, Any]) -> str:
    arms = summary["arms"]
    paired = summary["paired"]["resimind_vs_self_review"]
    if not summary["case_counts"]["total"]:
        return "尚无已评分案例，不能作出效果或成本判断。先完成冻结实验。"
    failures = sum(arm["technical_failure"]["numerator"] for arm in arms.values())
    if failures:
        return (f"本批次有 {failures} 个分支结果出现技术失败（共享请求可能影响多个分支）。"
                "先保留失败记录并定位执行问题，再以新版本协议复测；当前结果不能据此宣称优势。")
    if paired["wins"] > paired["losses"]:
        return ("本批可行案例中，ResiMind 的配对净胜为正，值得扩大独立案例集复测；"
                "同时核对失败类型与调用、token、时延成本，暂不据此宣称普遍领先。")
    if paired["wins"] < paired["losses"]:
        return ("本批可行案例中，ResiMind 的配对净胜为负。先分析被拒绝或未完成的具体原因，"
                "保留该批结果，再评估完成率与误交付控制之间的代价，暂不推进领先性宣传。")
    n = summary["case_counts"]["feasible"]
    if n and all(arms[name]["feasible_success"]["numerator"] == n for name in ARMS):
        return ("三组在本批可行案例上均已达到满分，出现难度天花板，当前案例无法区分完成能力。"
                "先比较误交付和实际资源消耗，再另行冻结更有区分度的独立案例；不要反复调这批案例制造优势。")
    return ("本批可行案例的配对净胜为零，尚未观察到完成率净增益。"
            "优先比较误交付控制与资源消耗，并扩大独立复测；暂不据此增加功能或宣称领先。")


def render_report(summary: dict[str, Any], rows: list[dict[str, Any]], metadata: dict[str, Any]) -> str:
    """Render a Chinese Markdown report beside manifest.json and runs/.

    Per-case links follow the runner's stable one-based file numbering. Missing
    metadata and usage are visibly unknown, never filled with estimated zeros.
    """
    counts = summary["case_counts"]
    arms = summary["arms"]
    paired = summary["paired"]["resimind_vs_self_review"]
    lines = [
        "# ResiMind 开放式规划：配对量化试验",
        "",
        _recommendation(summary),
        "",
        f"模型：`{_text(metadata.get('model'))}`；报告时间：{_text(metadata.get('timestamp'))}。",
        f"共 {counts['total']} 例：可行 {counts['feasible']} 例、已知不可行 {counts['infeasible']} 例、证据不足 {counts['missing_evidence']} 例。",
        "[冻结清单](manifest.json) · [完整评分与用量](scored-results.json)",
        "",
        "## 怎样比较",
        "",
        "三组使用相同模型、任务、证据、候选格式，并共享每例第一次实际模型响应。直接生成是 1 次调用的参考线；"
        "模型自检与 ResiMind 均以 3 次调用为上限。自检只看自己的前答与原规则；ResiMind 使用验证器反馈。"
        "相同调用上限不等于相同实际调用次数、token 或成本；本试验不声称严格等 token 比较。",
        "",
        "成功须交付满足独立评分器语义检查和候选协议的方案。未交付保留在可行案例分母中。"
        "负例的受控不交付只包括“明确放弃”与“未提交”，技术失败不算成功拦截。"
        "“明确放弃”是模型返回 null；“未提交”是验证器未放行。两者都不代表模型证明了问题不可行；"
        "证据不足也不能当作已证明不可行。",
        "",
        "## 完成与交付",
        "",
        "| 方法 | 可行任务成功 | Wilson 95% 区间 | 全部案例交付 | 无效交付 / 全部案例 | 负例受控不交付 | 技术失败 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in ARMS:
        arm = arms[name]
        interval = arm["feasible_success"]["wilson95"]
        ci = f"{interval[0]:.1%}–{interval[1]:.1%}" if interval else "不适用"
        lines.append(f"| {ARM_LABELS[name]} | {_fraction(arm['feasible_success'])} | {ci} | "
                     f"{_fraction(arm['delivery'])} | {_fraction(arm['invalid_delivery'])} | "
                     f"{_fraction(arm['controlled_withhold'])} | {_fraction(arm['technical_failure'])} |")
    lines.extend(["", "无效交付包含语义错误或协议错误；两类可能重叠，不能相加。仅在已交付结果中计算的比例另列如下。", "",
                  "| 方法 | 无效 / 已交付 | 语义错误交付数 | 协议错误交付数 | 明确放弃数 | 未提交数 |",
                  "| --- | --- | --- | --- | --- | --- |"])
    for name in ARMS:
        arm = arms[name]
        lines.append(f"| {ARM_LABELS[name]} | {_fraction(arm['invalid_among_delivered'])} | "
                     f"{arm['semantic_invalid_delivery']['numerator']} | {arm['contract_invalid_delivery']['numerator']} | "
                     f"{arm['outcomes']['explicit_abstention']} | {arm['outcomes']['verifier_withheld']} |")
    lines.extend(["", "## 配对差异与统计边界", "",
                  f"仅比较 {paired['feasible_cases']} 个可行案例，ResiMind 对模型自检：胜 {paired['wins']}、负 {paired['losses']}、"
                  f"平 {paired['ties']}（共同成功 {paired['both_success']}、共同失败 {paired['both_failure']}）。"
                  f"精确双侧 McNemar 探索性 p 值为 {paired['exact_mcnemar_p']:.6g}；不一致配对 {paired['discordant_pairs']} 例。",
                  "",
                  "这是人为构造的微型试点，预设只有 8 个可行案例和 4 个负例，并非从真实任务总体随机抽样。"
                  "Wilson 区间与 McNemar p 值仅为描述性、探索性参考，不能据此证明统计优势或推广到其他模型、领域、真实用户。"
                  "若不一致配对为 0，p=1 仅表示没有可用于区分的配对证据。观察到 0 次无效交付不等于零风险保证。",
                  "", "## 调用、token、时间与费用", "",
                  "下表每格为“总量 / 每案例均值”，均值分母包括全部案例及失败。每个分支都计入共享首轮，"
                  "这是各方法独立运行时的逻辑用量；把三行相加会重复计算共享请求，不能作为真实账单。"
                  "用量缺失会令相应总量与均值显示未知，不以 0 补齐。",
                  "",
                  "| 方法 | 逻辑调用 | 输入 token | 输出 token | 模型请求秒 | 分支经过秒 | 标价估算 USD |",
                  "| --- | --- | --- | --- | --- | --- | --- |"])
    for name in ARMS:
        costs = arms[name]["costs"]
        cells = [_total_mean(costs[field], 6 if field == "list_price_usd" else 2) for field in COST_FIELDS]
        lines.append(f"| {ARM_LABELS[name]} | " + " | ".join(cells) + " |")
    physical = metadata.get("physical_usage") or {}
    lines.extend(["",
                  f"去重后的实际物理请求：{_number(metadata.get('physical_calls'))} 次；"
                  f"输入 token {_number(physical.get('input_tokens'))}，输出 token {_number(physical.get('output_tokens'))}；"
                  f"标价估算 USD {_number(metadata.get('physical_list_price_usd'), 6)}。",
                  "",
                  "费用为冻结协议中的标价估算，按输入全为缓存未命中计算，不是实付账单。模型请求秒为所引用请求时长之和；"
                  "分支经过秒包含共享首轮及该分支处理时间。并发分支和共享请求的时间不可相加成实验总墙钟时间。",
                  "", "## 每个案例", "",
                  "| 案例与记录 | 真值类别 | 直接生成 | 模型自检 | ResiMind |",
                  "| --- | --- | --- | --- | --- |"])
    for index, row in enumerate(rows, 1):
        results = [OUTCOME_LABELS[row["arms"][name]["outcome"]] for name in ARMS]
        lines.append(f"| [{_text(row['case_id'])}](runs/case-{index:02}.json)：{_text(row['description'])} | "
                     f"{EXPECTED_LABELS[row['expected_outcome']]} | " + " | ".join(results) + " |")
    details = []
    for row in rows:
        for name in ARMS:
            arm = row["arms"][name]
            if arm["outcome"] == "technical_failure":
                details.append(f"- {_text(row['case_id'])} / {ARM_LABELS[name]}：技术失败；"
                               f"{_text(arm.get('technical_error'))}。")
            elif arm["outcome"] == "invalid_plan":
                oracle = arm.get("oracle") or {}
                details.append(f"- {_text(row['case_id'])} / {ARM_LABELS[name]}：无效交付；"
                               f"语义检查状态 {_text(oracle.get('status'))}；"
                               f"违反项 {_text(oracle.get('violations'))}；"
                               f"缺失项 {_text(oracle.get('missing'))}；"
                               f"协议错误 {_text(arm.get('contract_errors'))}。")
    if details:
        lines.extend(["", "失败明细：", "", *details])
    else:
        lines.extend(["", "本批记录没有无效交付或技术失败；未提交与明确放弃仍按上表单独记录。"])
    lines.extend(["", "## 可追溯性", "",
                  f"- 冻结前代码提交：`{_text(metadata.get('baseline_commit'))}`。",
                  f"- 冻结清单 SHA-256：`{_text(metadata.get('manifest_sha256'))}`。",
                  "- 原始请求、响应、参数及用量位于 `requests/`；案例运行记录位于 `runs/`；完整评分在 `scored-results.json`。",
                  "- 本报告只评价提供的合成规划案例与显式规则；不评价旅行体验、真实外部数据可靠性、数学证明或桥梁安全。",
                  ""])
    return "\n".join(lines)
