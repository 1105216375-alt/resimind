"""Frozen four-arm study of current-state binding and verified-rule execution.

Reuse one audited, frozen v3 library in all three production arms. Freeze code,
library and the full v3 exclusion list before sampling new expressions. Only
``run --live`` makes paid API calls; keep all output directories outside Git.
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
from benchmarks.paired_algebra_stats import analyze, STATEFUL_ARMS, STATEFUL_COMPARISONS
from benchmarks.run_algebra_live_eval import GOAL, SYSTEM, CountedAdmission, ModelAbstention
from benchmarks.run_fresh_algebra_eval import (
    ROOT, SETTINGS, CORRECTION, context, normalized, sources, snapshot_sources,
    strong_retry, production_run as legacy_run, totals as previous_totals,
)
from resimind import Task
from resimind.core import Decision
from resimind.domains.polynomial_learning import PolynomialProblem, WorkCounts, build_learning_agent
from resimind.knowledge import KnowledgeLibrary

ARMS = STATEFUL_ARMS
DEVELOPMENT_IDS = (
    "transfer-affine-trinomial-cube-01", "transfer-nested-square-01",
    "transfer-three-factor-product-01", "transfer-mixed-products-powers-04",
)
ACTION_METRICS = ("state_binding_rejections", "rule_action_calls", "rule_action_accepts", "rule_action_rejections")


def configuration(development=False):
    settings = dict(SETTINGS)
    if development:
        settings.update(max_physical_calls=128, max_total_output_tokens=524288)
    return dict(schema_version=1, study="state-bound-rule-execution-algebra-v4",
                development=development, repeats=1 if development else 2,
                settings=settings, arms=list(ARMS), common_goal=GOAL, correction=CORRECTION,
                system_prompt=SYSTEM, generator=generator_metadata(),
                comparisons=STATEFUL_COMPARISONS, development_ids=list(DEVELOPMENT_IDS),
                protocol={
                    "sampling": ("Four prespecified v3 development cases; no fresh-case claim." if development else
                        "32 fresh expressions, eight unchanged public families with four per family. Source, shared library and the complete prior-v3 AST exclusion inventory are frozen before one random seed. Exclude old public tasks and every prior-v3 expression; no selective resampling."),
                    "budget": "Same eight-call ceiling, 4096 output tokens/call, 24000 prompt bytes/call, model and request parameters. Actual token spend differs. No new discovery calls; historical library acquisition costs are reported separately for each production arm.",
                    "arms": "Strong verifier retry is unchanged. Legacy structured growth preserves v3 production prompts and behavior. State-bound growth replaces copied before expressions with revision/fingerprint binding and exposes current-state instructions/examples. Executable growth adds apply_verified_rule plus a conditional instruction to prefer it when a listed identity matches an unexpanded current subexpression. This treatment includes tool availability and its selection instruction; it does not isolate execution from prompting.",
                    "rule_execution": "One supplied rule, one existing first-match AST substitution on the current verified expression, one ordinary model proposal/step. Manual rule citations must match that same substitution. Bound validation, exact identity verification and provenance checks still apply. No automatic rule selection, alternate paths, fallback, answer solver or extra model calls. Tool work and accepted/rejected actions are reported.",
                    "library": "All three production arms share the exact same three-rule library from the audited v3 study, independently reverified on reload and frozen during transfer. No new rule discovery or transfer learning. These comparisons do not estimate knowledge versus no knowledge.",
                    "baseline": "Unchanged full-answer retry, optional untrusted calculation text and full prior expression/checker history. Different candidate schemas and access to the experimental rule executor are disclosed; the reference is not a matched-interface ablation.",
                    "stopping": "Checked completion, null abstention, outer-response schema/technical failure or fixed budget. Inner claim errors remain verifier rejections and can retry. Every failure remains in the denominator; no selective reruns.",
                    "order": "Interleave repetitions by expression; rotate four-arm submission order using the frozen seed, case index and repetition. Four concurrent workers. No adaptive scheduling.",
                    "scoring": "Separate exact degree-complete Cartesian-grid oracle, never used to generate answers or as proposer feedback. Unsupported/unscorable or absent outputs are failures.",
                    "rejection_metrics": "Repeated rejections are state/action/target scoped, ignore candidate IDs, normalize expression ASTs in parsed claims and otherwise retain exact raw text. State-binding rejects and model-selected rule action attempts/accepts/rejects are counted separately.",
                    "statistics": ("Development diagnostics only; no confirmatory comparison." if development else
                        "32 expression clusters retaining both repetitions. Family-stratified cluster bootstrap, 10000 draws, seed20261009. Primary contrasts: state-bound minus legacy, executable minus state-bound. Exact paired cluster sign-swap tests, Holm across two primaries. Supported improvement requires absolute delta>=0.10 and adjusted p<0.05. Executable versus strong retry is exploratory. Compare only within this study; do not contrast percentages across different v3/v4 task samples."),
                    "scope": "Bounded polynomial expansion, directly solvable by a CAS. Tests state continuation and explicit verified-rule execution, not mathematical SOTA, model training, new theorem discovery, cross-domain generality or international ranking.",
                    "publication": "All requests, responses and intermediate traces stay local. No automatic GitHub publication.",
                })


def read_prior(folder):
    folder = Path(folder)
    manifest = json.loads((folder / "manifest.json").read_text())
    summary = json.loads((folder / "summary.json").read_text())
    if (manifest.get("study") != "structured-residual-fresh-algebra-v3"
            or manifest.get("development") is not False or len(manifest.get("cases", [])) != 32
            or manifest.get("manifest_sha256") != digest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
            or summary.get("manifest_sha256") != manifest["manifest_sha256"]):
        raise ValueError("Expected the intact completed formal v3 study")
    data = (folder / "knowledge.json").read_bytes()
    knowledge_sha = hashlib.sha256(data).hexdigest()
    if knowledge_sha != summary["library"]["snapshot_sha256"]:
        raise ValueError("Prior knowledge snapshot differs from its audited summary")
    library = KnowledgeLibrary.load(folder / "knowledge.json", {"algebra": CountedAdmission()})
    if len(library.lookup("algebra")) != 3 or _snapshot(library).encode() != data:
        raise ValueError("Expected three independently reverified unchanged rules")
    prior = dict(study_manifest_sha256=manifest["manifest_sha256"],
                 summary_sha256=hashlib.sha256((folder / "summary.json").read_bytes()).hexdigest(),
                 knowledge_sha256=knowledge_sha, cases=manifest["cases"],
                 exclusions=exclusion_metadata([c["expression"] for c in manifest["cases"]]),
                 historical_acquisition=summary["discovery_totals"])
    return prior, data


def selected_cases(value):
    if value["development"]:
        inventory = {r["id"]: r for r in value["prior"]["cases"]}
        return tuple(Case(r["id"], r["expression"], tuple(r["variables"]), r["group"])
                     for r in (inventory[key] for key in DEVELOPMENT_IDS))
    return generate_cases(int(value["seed_hex"], 16), per_group=4,
                          exclude_expressions=[r["expression"] for r in value["prior"]["cases"]])


def freeze(folder, prior_folder, *, development=False):
    folder = Path(folder)
    if (folder / "manifest.json").exists():
        raise ValueError("Study already frozen; use a separate folder")
    prior, data = read_prior(prior_folder)
    value = configuration(development)
    value["prior"] = prior
    value["source_sha256"] = sources()
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
    if any(value.get(k) != v for k, v in current.items()):
        raise ValueError("Protocol changed after freeze")
    if hashlib.sha256((folder / "knowledge.json").read_bytes()).hexdigest() != value["prior"]["knowledge_sha256"]:
        raise ValueError("Frozen library changed")
    if value["prior"]["exclusions"] != exclusion_metadata([r["expression"] for r in value["prior"]["cases"]]):
        raise ValueError("Frozen prior-case exclusion inventory changed")
    if value["cases"] != json.loads(canonical([asdict(c) for c in selected_cases(value)])):
        raise ValueError("Cases differ from frozen sampling")
    return value


def rejection_key(event):
    candidate = event["candidate"]
    try:
        claim = json.loads(candidate["claim"])
        if type(claim) is dict:
            claim = dict(claim)
            for key in ("before", "after"):
                if type(claim.get(key)) is str:
                    claim[key] = normalized(claim[key])
            return canonical(claim)
    except (ValueError, TypeError, RecursionError):
        pass
    return candidate["claim"]


def add_action_metrics(result):
    metrics = dict.fromkeys(ACTION_METRICS, 0)
    revision, duplicates, contract = 0, 0, 0
    seen = set()
    for event in result["observations"]:
        candidate = event.get("candidate")
        if candidate is None or event.get("step", 0) <= 0:
            continue
        if candidate["action"] == "apply_verified_rule":
            metrics["rule_action_calls"] += 1
            metrics["rule_action_accepts"] += event["decision"] == "accept"
            metrics["rule_action_rejections"] += event["decision"] == "reject"
        if event["decision"] == "reject":
            reasons = event["reasons"]
            metrics["state_binding_rejections"] += any(
                r.startswith(("rewrite_before_mismatch:", "polynomial_state_mismatch")) for r in reasons)
            contract += "malformed_or_unsupported_rewrite" in reasons
            key = (revision, candidate["action"], candidate["target"], rejection_key(event))
            duplicates += key in seen
            seen.add(key)
        revision += event["decision"] == "accept"
    result.update(metrics, repeat_rejections=duplicates, rewrite_contract_rejections=contract)
    return result


def production_run(case, library, complete, max_calls, *, arm):
    if arm == "legacy_structured_growth":
        return add_action_metrics(legacy_run(case, library, complete, max_calls, structured=True))
    if arm not in {"state_bound_growth", "executable_growth"}:
        raise ValueError("Unknown production arm")
    counts, abstained = WorkCounts(), False
    def augmented(prompt):
        nonlocal abstained
        payload = json.loads(prompt)
        payload["evaluation_task"] = context(case)
        text = complete(canonical(payload))
        if text.strip() == "null":
            abstained = True
            raise ModelAbstention()
        return text
    learner = build_learning_agent(PolynomialProblem(case.expression, case.variables), library,
                                   max_steps=max_calls, counts=counts, complete=augmented,
                                   guidance="structured", proposal_protocol="state_bound",
                                   allow_rule_execution=arm == "executable_growth")
    outcome = learner.run(Task(case.id, GOAL, "algebra"), learn=False)
    run = outcome.result.run_result
    uses = [dict(rule_id=f.value[2], fingerprint=f.value[3],
                 source_task_id=library.get(f.value[2]).candidate.source_task_id,
                 cross_task=library.get(f.value[2]).candidate.source_task_id != case.id)
            for f in run.state.facts if f.value[2]]
    result = dict(status="abstained" if abstained else run.status,
                  stop_reason="model_abstained" if abstained else run.stop_reason,
                  failure_types=sorted({r for e in run.trace if e.decision is Decision.INTERRUPT for r in e.reasons}),
                  output=run.state.facts[-1].value[1] if run.status == "solved" and run.residual.solved else None,
                  work=asdict(counts), accepted_steps=sum(e.step > 0 and e.decision is Decision.ACCEPT for e in run.trace),
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
    if arm == "strong_verify_retry":
        result = strong_retry(case, bound, recorder.settings["max_calls_per_task"])
        result.update(dict.fromkeys(ACTION_METRICS, 0), rewrite_contract_rejections=0)
    else:
        result = production_run(case, library, bound, recorder.settings["max_calls_per_task"], arm=arm)
    result["controller_seconds"] = time.perf_counter() - start
    records = [recorder.record(key) for key in bound.request_ids]
    result.update(task_id=case.id, group=case.group, expression=case.expression, variables=list(case.variables),
                  arm=arm, repeat=repeat, request_ids=bound.request_ids, model_calls=len(bound.request_ids),
                  api_attempts=sum(r.get("api_attempted", False) for r in records),
                  usage=recorder.compact_summary(bound.request_ids)["usage"],
                  model_seconds=sum(r.get("elapsed_seconds", 0.0) for r in records))
    result["oracle"] = (score_expansion(case.expression, result["output"], case.variables)
                        if result["output"] is not None else dict(status="missing", reason="no_delivered_answer"))
    result["success"] = result["oracle"]["status"] == "valid"
    result["invalid_delivered"] = result["oracle"]["status"] == "invalid"
    result["unsuccessful_delivered"] = result["output"] is not None and not result["success"]
    result["technical_failure"] = any(r["status"] != "complete" for r in records)
    result["budget_failure"] = any(r["status"] == "blocked" for r in records)
    result["schema_failure"] = result["status"] == "schema_failure" or (
        result["status"] == "error" and "ModelResponseError" in result.get("failure_types", []))
    result["controller_failure"] = result["status"] == "error" and not result["schema_failure"] and not result["technical_failure"]
    result["elapsed_seconds"] = result["controller_seconds"]
    write_json(recorder.folder / "cases" / f"{phase}-{case.id}-r{repeat}-{arm}.json", result)
    print(f"{phase} {case.id} r{repeat} {arm}: {result['oracle']['status']} ({len(bound.request_ids)} calls)", flush=True)
    return result


def totals(rows):
    result = previous_totals(rows)
    result.update({key: sum(r[key] for r in rows) for key in ACTION_METRICS})
    result.update(pattern_attempts=sum(r["work"]["pattern_attempts"] for r in rows),
                  primitive_node_visits=sum(r["work"]["primitive_node_visits"] for r in rows))
    return result


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
        raise RuntimeError("Reload changed the frozen library")
    cases = [Case(r["id"], r["expression"], tuple(r["variables"]), r["group"]) for r in manifest["cases"]]
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
                raise RuntimeError("Transfer changed the frozen library")
    check_frozen(folder)
    expected = {(case.id, repeat, arm) for case in cases for repeat in range(manifest["repeats"]) for arm in ARMS}
    if {(r["task_id"], r["repeat"], r["arm"]) for r in rows} != expected or len(rows) != len(expected):
        raise RuntimeError("Missing or duplicate experiment rows")
    if admission.calls != reload_calls:
        raise RuntimeError("Unexpected admission during transfer")
    result = dict(schema_version=1, study=manifest["study"], manifest_sha256=manifest["manifest_sha256"],
                  evaluation_kind="seen_case_development" if manifest["development"] else "frozen_fresh_expression_pilot",
                  completed_at_utc=datetime.now(timezone.utc).isoformat(), wall_seconds=time.perf_counter() - start,
                  requests_present_at_session_start=requests_at_start, settings=manifest["settings"],
                  case_clusters=len(cases), repeats=manifest["repeats"],
                  transfer=[{k: v for k, v in r.items() if k != "observations"} for r in rows],
                  totals={a: totals([r for r in rows if r["arm"] == a]) for a in ARMS},
                  by_group={g: {a: totals([r for r in rows if r["arm"] == a and r["group"] == g]) for a in ARMS}
                            for g in sorted({c.group for c in cases})},
                  statistics=None if manifest["development"] else analyze(rows, ARMS, comparisons=STATEFUL_COMPARISONS),
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
    parser.add_argument("--prior-folder", type=Path)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "freeze":
        if args.prior_folder is None:
            parser.error("freeze requires --prior-folder containing the audited v3 study")
        value = freeze(args.folder, args.prior_folder, development=args.development)
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
