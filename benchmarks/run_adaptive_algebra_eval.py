"""Frozen v5 controller/tool study with a zero-model symbolic control.

This is a bundled policy comparison, not a matched-computation scheduling
ablation. Adaptive and symbolic arms may execute up to 64 controller steps;
the unchanged neural baselines get eight model-directed steps. The adaptive
arm may recover from model abstention, malformed output or transport failure
using verified symbolic actions. Every request and recovery remains recorded.
Symbolic success must be reported: tool-only capability is not neural gain.

Freeze code, the shared verified v3 library and every v3/v4 formal expression
before drawing a new seed. Only ``run --live`` can call a paid API. Keep the
study folder outside Git; the independent scoring oracle never feeds proposals.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

from benchmarks.algebra_task_generator import generate_cases, generator_metadata, exclusion_metadata
from benchmarks.knowledge_growth import Case, _snapshot, score_expansion
from benchmarks.live_eval_support import RecordedCompletion, canonical, digest, write_json
from benchmarks.paired_algebra_stats import analyze, ADAPTIVE_ARMS, ADAPTIVE_COMPARISONS
from benchmarks.run_algebra_live_eval import GOAL, SYSTEM, CountedAdmission
from benchmarks.run_fresh_algebra_eval import (
    ROOT, SETTINGS as NEURAL_SETTINGS, CORRECTION, context, sources, snapshot_sources, strong_retry,
)
from benchmarks.run_stateful_algebra_eval import (
    ACTION_METRICS, add_action_metrics, production_run as executable_run,
    read_prior as read_v3, totals as previous_totals,
)
from resimind import Task
from resimind.core import Decision
from resimind.domains.polynomial_learning import PolynomialProblem, WorkCounts
from resimind.knowledge import KnowledgeLibrary


ARMS, COMPARISONS = ADAPTIVE_ARMS, ADAPTIVE_COMPARISONS
DEVELOPMENT_IDS = (
    "transfer-nested-square-01", "transfer-three-factor-product-01",
    "transfer-four-factor-product-01", "transfer-mixed-products-powers-04",
)
SETTINGS = dict(NEURAL_SETTINGS, max_physical_calls=1600, max_total_output_tokens=6_553_600,
                max_action_steps=64)
STRATEGY_METRICS = (
    "whole_model_attempts", "whole_model_accepts", "local_model_attempts", "local_model_accepts",
    "model_failures", "schema_failures", "model_abstentions", "technical_failures", "failures",
    "strategy_switches", "duplicate_suppressions", "rollback_attempts", "rollback_accepts",
    "rule_attempts", "rule_accepts", "primitive_attempts", "primitive_accepts",
)


def configuration(development=False):
    settings = dict(SETTINGS)
    if development:
        settings.update(max_physical_calls=96, max_total_output_tokens=393_216)
    return dict(
        schema_version=1, study="adaptive-symbolic-algebra-v5", development=development,
        repeats=1 if development else 2, settings=settings, arms=list(ARMS), comparisons=COMPARISONS,
        common_goal=GOAL, correction=CORRECTION, system_prompt=SYSTEM,
        generator=generator_metadata(), development_ids=list(DEVELOPMENT_IDS),
        protocol={
            "sampling": ("Four prespecified, already seen v4 expressions; development only." if development else
                "32 fresh expressions, eight public families and four per family. Freeze sources, shared library and the complete 64-expression v3/v4 exclusion inventory before one seed; exclude old public tasks too. No selective resampling."),
            "arms": "Unchanged strong verifier retry with optional calculation text (provider thinking disabled); unchanged v4 executable growth; adaptive growth; identical adaptive implementation with complete=None as the zero-model symbolic control.",
            "budgets": "Three neural arms: at most eight model calls per episode, identical provider settings, 4096 output tokens and 24000 prompt bytes per request. Adaptive/symbolic arms additionally allow up to 64 total controller steps, including rejected/empty attempts; the two older baselines allow eight model-directed steps. Actual verified actions, tools, tokens and time are reported. Internal computation is not matched.",
            "treatment": "Adaptive may select verified rules, primitive rewrites, decomposition and bounded recovery automatically. Model null, malformed replies or transport errors may trigger symbolic fallback without resetting model/action budgets. These are bundled controller, tool-access and stopping changes, not isolated scheduling or pure neural reasoning.",
            "symbolic_control": "Symbolic growth never receives or invokes a model callback; request count must be zero. A strong symbolic result is evidence for the tool policy, not neural benefit. Adaptive-minus-symbolic tests the observed contribution of enabling the model within this policy.",
            "library": "All three production arms reuse exactly the audited v3 three-rule store, reverified on reload and frozen during transfer. No discovery calls or transfer learning. Historical acquisition costs are separate and identical for all three library arms.",
            "baseline": "Strong retry retains optional untrusted calculation text and full expression/checker history. The v4 executable arm is unchanged. They retain their original null/schema/technical stopping behavior, unlike adaptive recovery; disclose that asymmetry.",
            "metrics": "model_calls counts recorded completion invocations. controller_steps includes positive trace steps/attempts; validated_actions counts accepted committed steps (or the accepted final baseline answer), not attempted or rejected verification. Exact identity checks and AST work are separate. Strategy events stay local; compact strategy counters remain in results. Strategy counters are available only for adaptive/symbolic; older arms retain common action metrics. technical_failure/schema_failure mean a request/schema issue occurred and may coexist with a successfully recovered task.",
            "order": "Interleave repetitions by expression; rotate four-arm submission order using seed, case index and repeat. Four workers. No result-adaptive scheduling or selective reruns.",
            "scoring": "Independent exact degree-complete Cartesian-grid scorer; no scorer feedback to models or symbolic controller. Missing, incorrect and unsupported outputs all count as unsuccessful. No failures are dropped.",
            "statistics": ("Development only; no confirmatory inference." if development else
                "32 expression clusters with both repeats together; family-stratified 10000-draw bootstrap, seed20261009. Two primary contrasts: adaptive minus v4 executable and adaptive minus symbolic. Exact paired cluster sign-swap tests, Holm across two; support requires delta>=0.10 and adjusted p<0.05. Strong retry comparison is exploratory. No cross-study score subtraction."),
            "scope": "Bounded polynomial expansion is directly solvable by a CAS. This study does not establish mathematical SOTA, international rank, general cross-domain intelligence, new theorem discovery or lower monetary cost. Matched API ceilings are not matched realized spend or matched internal computation.",
            "publication": "Raw requests, responses and strategy traces stay local; no automatic GitHub publication.",
        })


def _case_inventory(cases):
    if (type(cases) is not list or len(cases) != 32
            or any(type(c) is not dict or not {"id", "expression", "variables", "group"} <= set(c) for c in cases)
            or any(type(c["id"]) is not str or not c["id"] or type(c["expression"]) is not str
                   or not c["expression"] or type(c["group"]) is not str
                   or type(c["variables"]) is not list for c in cases)
            or len({c["id"] for c in cases}) != 32):
        raise ValueError("Expected a complete 32-case prior inventory")
    return cases


def read_priors(v3_folder, v4_folder):
    v3, data = read_v3(v3_folder)
    _case_inventory(v3["cases"])
    folder = Path(v4_folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    summary_data = (folder / "summary.json").read_bytes()
    summary = json.loads(summary_data)
    if (manifest.get("study") != "state-bound-rule-execution-algebra-v4"
            or manifest.get("development") is not False
            or manifest.get("manifest_sha256") != digest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
            or summary.get("manifest_sha256") != manifest["manifest_sha256"]):
        raise ValueError("Expected the intact completed formal v4 study")
    cases = _case_inventory(manifest.get("cases"))
    if (manifest.get("prior", {}).get("study_manifest_sha256") != v3["study_manifest_sha256"]
            or manifest["prior"].get("cases") != v3["cases"]):
        raise ValueError("v4 does not bind the supplied full v3 inventory")
    knowledge_sha = hashlib.sha256(data).hexdigest()
    if ((folder / "knowledge.json").read_bytes() != data
            or summary["library"]["snapshot_sha256"] != knowledge_sha
            or manifest["prior"]["knowledge_sha256"] != knowledge_sha):
        raise ValueError("v3 and v4 must share the same audited library")
    expressions = [c["expression"] for c in v3["cases"] + cases]
    exclusions = exclusion_metadata(expressions)
    if exclusions["unique_ast_count"] != 64:
        raise ValueError("Prior studies must contain 64 distinct expression ASTs")
    return dict(v3=v3, v4=dict(study_manifest_sha256=manifest["manifest_sha256"],
                               summary_sha256=hashlib.sha256(summary_data).hexdigest(), cases=cases),
                knowledge_sha256=knowledge_sha, exclusions=exclusions,
                historical_acquisition=v3["historical_acquisition"]), data


def prior_expressions(value):
    return [c["expression"] for name in ("v3", "v4") for c in value["prior"][name]["cases"]]


def selected_cases(value):
    if value["development"]:
        inventory = {c["id"]: c for c in value["prior"]["v4"]["cases"]}
        return tuple(Case(c["id"], c["expression"], tuple(c["variables"]), c["group"])
                     for c in (inventory[key] for key in DEVELOPMENT_IDS))
    return generate_cases(int(value["seed_hex"], 16), per_group=4, exclude_expressions=prior_expressions(value))


def freeze(folder, v3_folder, v4_folder, *, development=False):
    folder = Path(folder)
    if (folder / "manifest.json").exists():
        raise ValueError("Study already frozen; use a separate folder")
    prior, data = read_priors(v3_folder, v4_folder)
    value = configuration(development)
    value.update(prior=prior, source_sha256=sources())
    snapshot_sources(folder, value["source_sha256"])
    (folder / "knowledge.json").write_bytes(data)
    value["implementation_frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
    value["base_git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    value["seed_hex"] = secrets.token_hex(16)
    value["cases"] = [asdict(c) for c in selected_cases(value)]
    value["tasks_sampled_at_utc"] = datetime.now(timezone.utc).isoformat()
    if sources() != value["source_sha256"]:
        raise ValueError("Implementation changed during task sampling")
    value["manifest_sha256"] = digest(value)
    write_json(folder / "manifest.json", value)
    return value


def check_frozen(folder):
    folder = Path(folder)
    value = json.loads((folder / "manifest.json").read_text())
    if value["manifest_sha256"] != digest({k: v for k, v in value.items() if k != "manifest_sha256"}):
        raise ValueError("Manifest was modified")
    if value["source_sha256"] != sources():
        raise ValueError("Implementation changed after freeze")
    current = json.loads(canonical(configuration(value["development"])))
    if any(value.get(key) != expected for key, expected in current.items()):
        raise ValueError("Protocol changed after freeze")
    if hashlib.sha256((folder / "knowledge.json").read_bytes()).hexdigest() != value["prior"]["knowledge_sha256"]:
        raise ValueError("Frozen library changed")
    if value["prior"]["exclusions"] != exclusion_metadata(prior_expressions(value)):
        raise ValueError("Frozen 64-case exclusion inventory changed")
    if value["cases"] != json.loads(canonical([asdict(c) for c in selected_cases(value)])):
        raise ValueError("Cases differ from frozen sampling")
    return value


def adaptive_run(case, library, complete, max_calls, max_steps, *, symbolic=False):
    # Lazy import keeps freeze/reconstruction independent of optional model SDKs.
    from resimind.domains.adaptive_polynomial import AdaptiveStats, build_adaptive_learning_agent

    counts, stats = WorkCounts(), AdaptiveStats()
    def augmented(prompt):
        payload = json.loads(prompt)
        payload["evaluation_task"] = context(case)
        # Preserve raw null/schema/transport behavior for the adaptive controller.
        return complete(canonical(payload))
    learner = build_adaptive_learning_agent(
        PolynomialProblem(case.expression, case.variables), library,
        complete=None if symbolic else augmented, counts=counts, stats=stats,
        # Preserve the v5 controller treatment; growth control is a later tool addition.
        max_model_calls=max_calls, max_steps=max_steps, control_expression_growth=False,
        cost_aware_scheduling=False, goal_directed=False)
    outcome = learner.run(Task(case.id, GOAL, "algebra"), learn=False)
    run = outcome.result.run_result
    strategy = asdict(stats)
    strategy["policy_audit_available"] = True
    if any(type(strategy.get(key)) is not int or strategy[key] < 0 for key in STRATEGY_METRICS + ("model_calls", "action_attempts")):
        raise RuntimeError("Adaptive audit counters are missing or invalid")
    if strategy["model_calls"] > max_calls or (symbolic and strategy["model_calls"]):
        raise RuntimeError("Adaptive model budget violated")
    steps = sum(e.step > 0 for e in run.trace)
    if steps > max_steps or strategy["action_attempts"] > max_steps:
        raise RuntimeError("Adaptive action budget violated")
    uses = [dict(rule_id=f.value[2], fingerprint=f.value[3],
                 source_task_id=library.get(f.value[2]).candidate.source_task_id,
                 cross_task=library.get(f.value[2]).candidate.source_task_id != case.id)
            for f in run.state.facts if f.value[2]]
    result = dict(
        status=run.status, stop_reason=run.stop_reason,
        failure_types=sorted({r for e in run.trace if e.decision is Decision.INTERRUPT for r in e.reasons}),
        output=run.state.facts[-1].value[1] if run.status == "solved" and run.residual.solved else None,
        work=asdict(counts), accepted_steps=sum(e.step > 0 and e.decision is Decision.ACCEPT for e in run.trace),
        controller_steps=steps, strategy_audit=strategy,
        retrieved_rule_ids=list(outcome.retrieved_ids), committed_rule_uses=uses,
        admissions=[dict(id=r.candidate.id, status=r.status, fingerprint=r.fingerprint) for r in outcome.admissions],
        learning_errors=list(outcome.learning_errors),
        observations=[dict(step=e.step, decision=e.decision.value, reasons=list(e.reasons),
                           candidate=e.candidate.to_dict() if e.candidate else None) for e in run.trace])
    return add_action_metrics(result)


def run_case(case, arm, repeat, recorder, library, *, phase):
    if arm not in ARMS:
        raise ValueError("Unknown study arm")
    bound = recorder.bind(f"{phase}-{case.id}-r{repeat}-{arm}")
    start = time.perf_counter()
    max_calls = recorder.settings["max_calls_per_task"]
    if arm == "strong_verify_retry":
        result = strong_retry(case, bound, max_calls)
        result.update(dict.fromkeys(ACTION_METRICS, 0), rewrite_contract_rejections=0,
                      controller_steps=result["work"]["proposal_calls"])
    elif arm == "executable_growth":
        result = executable_run(case, library, bound, max_calls, arm="executable_growth")
        result["controller_steps"] = sum(e.get("step", 0) > 0 for e in result["observations"])
    else:
        result = adaptive_run(case, library, bound, max_calls, recorder.settings["max_action_steps"],
                              symbolic=arm == "symbolic_growth")
        if result["strategy_audit"]["model_calls"] != len(bound.request_ids):
            raise RuntimeError("Adaptive model counter disagrees with recorded requests")
    if len(bound.request_ids) > max_calls or (arm == "symbolic_growth" and bound.request_ids):
        raise RuntimeError("Per-episode model budget violated")
    result["controller_seconds"] = time.perf_counter() - start
    records = [recorder.record(key) for key in bound.request_ids]
    result.update(task_id=case.id, group=case.group, expression=case.expression, variables=list(case.variables),
                  arm=arm, repeat=repeat, request_ids=bound.request_ids, model_calls=len(bound.request_ids),
                  api_attempts=sum(r.get("api_attempted", False) for r in records),
                  usage=recorder.compact_summary(bound.request_ids)["usage"],
                  model_seconds=sum(r.get("elapsed_seconds", 0.0) for r in records),
                  validated_actions=result["accepted_steps"])
    result.setdefault("strategy_audit", {**dict.fromkeys(STRATEGY_METRICS, 0),
                                        "model_calls": len(bound.request_ids),
                                        "action_attempts": result["controller_steps"], "events": [],
                                        "policy_audit_available": False,
                                        "stop_reason": result["stop_reason"]})
    result["oracle"] = (score_expansion(case.expression, result["output"], case.variables)
                        if result["output"] is not None else dict(status="missing", reason="no_delivered_answer"))
    result["success"] = result["oracle"]["status"] == "valid"
    result["invalid_delivered"] = result["oracle"]["status"] == "invalid"
    result["unsuccessful_delivered"] = result["output"] is not None and not result["success"]
    result["technical_failure"] = any(r["status"] != "complete" for r in records)
    result["budget_failure"] = any(r["status"] == "blocked" for r in records)
    result["schema_failure"] = result["strategy_audit"]["schema_failures"] > 0 or result["status"] == "schema_failure" or (
        result["status"] == "error" and "ModelResponseError" in result.get("failure_types", []))
    result["controller_failure"] = result["status"] == "error" and not result["schema_failure"] and not result["technical_failure"]
    result["elapsed_seconds"] = result["controller_seconds"]
    write_json(recorder.folder / "cases" / f"{phase}-{case.id}-r{repeat}-{arm}.json", result)
    print(f"{phase} {case.id} r{repeat} {arm}: {result['oracle']['status']} "
          f"({len(bound.request_ids)} model calls, {result['controller_steps']} steps)", flush=True)
    return result


def totals(rows):
    result = previous_totals(rows)
    result.update(controller_steps=sum(r["controller_steps"] for r in rows),
                  validated_actions=sum(r["validated_actions"] for r in rows),
                  policy_audited_episodes=sum(r["strategy_audit"]["policy_audit_available"] for r in rows),
                  model_budget_exhausted_episodes=sum(r["strategy_audit"].get("model_budget_exhausted", False) for r in rows),
                  action_budget_exhausted_episodes=sum(r["strategy_audit"].get("action_budget_exhausted", False) for r in rows),
                  strategy_metrics={key: sum(r["strategy_audit"][key] for r in rows) for key in STRATEGY_METRICS})
    return result


def compact(row):
    value = {key: item for key, item in row.items() if key != "observations"}
    value["strategy_audit"] = {key: item for key, item in row["strategy_audit"].items() if key != "events"}
    return value


def run_study(folder, client):
    folder = Path(folder)
    manifest = check_frozen(folder)
    if (folder / "summary.json").exists():
        raise ValueError("Completed study exists; do not overwrite or selectively rerun")
    recorder = RecordedCompletion(folder, manifest["settings"], client, SYSTEM)
    requests_at_start = len(recorder.records())
    start = time.perf_counter()
    admission = CountedAdmission()
    verifiers = {"algebra": admission}
    library = KnowledgeLibrary.load(folder / "knowledge.json", verifiers)
    reload_calls = admission.calls
    frozen = _snapshot(library)
    if hashlib.sha256(frozen.encode()).hexdigest() != manifest["prior"]["knowledge_sha256"]:
        raise RuntimeError("Reload changed frozen knowledge")
    cases = [Case(c["id"], c["expression"], tuple(c["variables"]), c["group"]) for c in manifest["cases"]]
    rows, seed = [], int(manifest["seed_hex"], 16)
    for index, case in enumerate(cases):
        for repeat in range(manifest["repeats"]):
            offset = (seed + index + repeat) % len(ARMS)
            order = ARMS[offset:] + ARMS[:offset]
            with ThreadPoolExecutor(max_workers=manifest["settings"]["workers"]) as pool:
                futures = {arm: pool.submit(run_case, case, arm, repeat, recorder,
                                           library if arm != "strong_verify_retry" else KnowledgeLibrary(verifiers),
                                           phase="development" if manifest["development"] else "transfer") for arm in order}
                rows.extend(futures[arm].result() for arm in ARMS)
            if _snapshot(library) != frozen:
                raise RuntimeError("Transfer changed frozen knowledge")
    check_frozen(folder)
    expected = {(case.id, repeat, arm) for case in cases for repeat in range(manifest["repeats"]) for arm in ARMS}
    if len(rows) != len(expected) or {(r["task_id"], r["repeat"], r["arm"]) for r in rows} != expected:
        raise RuntimeError("Missing or duplicate experiment rows")
    if admission.calls != reload_calls:
        raise RuntimeError("Unexpected transfer admission")
    result = dict(
        schema_version=1, study=manifest["study"], manifest_sha256=manifest["manifest_sha256"],
        evaluation_kind="seen_case_development" if manifest["development"] else "frozen_fresh_expression_pilot",
        completed_at_utc=datetime.now(timezone.utc).isoformat(), wall_seconds=time.perf_counter() - start,
        requests_present_at_session_start=requests_at_start, settings=manifest["settings"],
        case_clusters=len(cases), repeats=manifest["repeats"], transfer=[compact(r) for r in rows],
        totals={arm: totals([r for r in rows if r["arm"] == arm]) for arm in ARMS},
        by_group={g: {a: totals([r for r in rows if r["arm"] == a and r["group"] == g]) for a in ARMS}
                  for g in sorted({case.group for case in cases})},
        statistics=None if manifest["development"] else analyze(rows, ARMS, comparisons=COMPARISONS),
        physical_requests=recorder.compact_summary(), historical_acquisition=manifest["prior"]["historical_acquisition"],
        library=dict(verified_rules=len(library.lookup("algebra")),
                     snapshot_sha256=hashlib.sha256(frozen.encode()).hexdigest(),
                     reverified_on_reload=True, frozen_during_transfer=True,
                     reload_calls=reload_calls, transfer_admission_calls=admission.calls - reload_calls,
                     rules=[dict(id=r.candidate.id, source_task_id=r.candidate.source_task_id,
                                 fingerprint=r.fingerprint, statement=r.candidate.statement)
                            for r in library.lookup("algebra")]), limitations=manifest["protocol"])
    write_json(folder / "summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run"))
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--v3-folder", type=Path)
    parser.add_argument("--v4-folder", type=Path)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "freeze":
        if args.v3_folder is None or args.v4_folder is None:
            parser.error("freeze requires --v3-folder and --v4-folder")
        value = freeze(args.folder, args.v3_folder, args.v4_folder, development=args.development)
        print(json.dumps(dict(manifest_sha256=value["manifest_sha256"], development=value["development"],
                              case_count=len(value["cases"]), repeats=value["repeats"])))
        return
    if not args.live:
        parser.error("run requires --live and makes paid API calls")
    manifest = check_frozen(args.folder)
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com",
                    timeout=manifest["settings"]["timeout_seconds"], max_retries=0)
    result = run_study(args.folder, client)
    print(json.dumps(result["totals"], indent=2))


if __name__ == "__main__":
    main()
