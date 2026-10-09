"""Frozen fresh-expression study: strong retry, plain/structured residual, growth.

Use old tasks with ``freeze --development`` before freezing a fresh study. Fresh
sampling occurs only after source hashes are captured. Only ``run --live``
calls a paid API. Keep output directories outside the repository.
"""
from __future__ import annotations

import argparse
import ast
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

from benchmarks.algebra_task_generator import generate_cases, generator_metadata
from benchmarks.knowledge_growth import Case, _snapshot, score_expansion
from benchmarks.live_eval_support import RecordedCompletion, RequestFailure, canonical, digest, write_json
from benchmarks.paired_algebra_stats import analyze
from benchmarks.run_algebra_live_eval import (
    DISCOVERY, GOAL, SYSTEM, TRANSFER as OLD_TRANSFER, CountedAdmission, ModelAbstention,
    check_answer, totals as old_totals,
)
from resimind import Task
from resimind.core import Decision
from resimind.domains.polynomial_learning import PolynomialProblem, WorkCounts, build_learning_agent
from resimind.knowledge import KnowledgeLibrary

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("strong_verify_retry", "plain_residual", "structured_residual", "structured_growth")
DEVELOPMENT = tuple(OLD_TRANSFER[index] for index in (1, 2, 6, 7))
SETTINGS = dict(model="deepseek-flash", temperature=0.0, thinking="disabled",
                max_output_tokens=4096, timeout_seconds=90, max_calls_per_task=8,
                max_prompt_bytes=24000, max_physical_calls=2100,
                max_total_output_tokens=8601600, workers=4, sdk_retries=0)
CORRECTION = (
    "A coefficient diagnostic refers only to its named monomial. Correct that coefficient, "
    "not a different term. Recompute if needed; do not repeat a rejected expression unchanged. "
    "All proposed calculations and retrieved rules remain subject to independent verification."
)


def sources():
    paths = sorted((ROOT / "src/resimind").rglob("*.py"))
    paths += sorted((ROOT / "benchmarks").glob("*.py"))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def snapshot_sources(folder, hashes):
    """Retain the exact evaluated implementation locally, including development."""
    for relative, expected in hashes.items():
        data = (ROOT / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Source changed before its local snapshot")
        target = Path(folder) / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def configuration(development=False):
    settings = dict(SETTINGS)
    if development:
        settings.update(max_physical_calls=160, max_total_output_tokens=655360)
    result = dict(schema_version=1, study="structured-residual-fresh-algebra-v3",
                development=development, repeats=1 if development else 2,
                settings=settings, arms=list(ARMS), common_goal=GOAL, correction=CORRECTION,
                system_prompt=SYSTEM, generator=generator_metadata(),
                discovery=[asdict(c) for c in DISCOVERY],
                protocol={
                    "sampling": "32 fresh expressions from eight known families, four per family; seed sampled only after implementation hashes are captured. New inputs, not unknown model training data or new mathematical families.",
                    "budget": "Same eight-call ceiling, 4096 output tokens/call, 24000 prompt bytes/call, model and request parameters. These are matched ceilings, not equal realized token spend. Actual input/output tokens and discovery cost are reported.",
                    "strong_baseline": "Full-answer verifier retry with optional untrusted calculation text, complete prior expressions/observations and repeat warnings. Past calculation text is omitted from later prompts. No trusted intermediate-state commitment or library.",
                    "ablation": "Plain and structured residual use the same production candidate interface and verification. Structured growth adds only the frozen library to structured guidance. Common correction advice is supplied to every arm.",
                    "context": "Never trim the task/current verified state or active diagnostic. Production guidance bounds syntactic subterms/rejection history; baseline retains at most eight expression/observation pairs. Exceeding the shared byte ceiling is a recorded budget failure.",
                    "discovery": "Three sequential model tasks, once per study; independently admit, save and reverify. Charge discovery to growth, amortized across all growth transfer episodes. No transfer learning.",
                    "order": "Two repetitions interleaved by case; rotate four-arm submission order using sampled seed and case/repeat. Four concurrent workers. No adaptive scheduling or selective reruns.",
                    "stopping": "Stop at checked completion, null abstention, outer-response schema/technical failure or budget. Production inner rewrite-claim errors remain verifier rejections and may retry. Normalize production null to immediate abstention. Every failed attempt remains in the denominator.",
                    "scoring": "Separate exact degree-complete Cartesian-grid oracle, no scorer feedback to models. All unsupported/unscorable outputs count as unsuccessful, never success.",
                    "rejection_metrics": "Repeated production rejections are counted per state/action/target: AST-normalized valid rewrite claims, otherwise exact raw claim text, ignoring candidate IDs. Rewrite-contract rejections include malformed inner JSON, schema, input binding and unsupported expression syntax; outer schema failures are reported separately.",
                    "statistics": "32 expression clusters, retaining both repetitions and all arms; family-stratified cluster bootstrap, 10000 draws, seed20261009. Two primary contrasts: structured-minus-plain and growth-minus-structured. Exact paired cluster sign-swap p, Holm-adjusted across those two. Support requires delta>=0.10 and adjusted p<0.05. Failure to meet criteria is inconclusive, not equivalence.",
                    "scope": "This bounded algebra is directly solvable by a CAS; the study probes controller and reuse behavior, not mathematical SOTA or cross-domain generalization.",
                    "publication": "Compact methods and results can be reviewed separately. Full requests, responses and intermediate traces stay local. No automatic GitHub publication.",
                })
    if development:
        result["protocol"].update(
            sampling="Four existing development expressions from the preceding study; no fresh transfer seed is sampled for task selection.",
            order="One repetition per case; rotate four-arm submission order using a run seed. Four concurrent workers.",
            statistics="Development diagnostics only. No confirmatory statistics or fresh-task claims.")
    return result


def freeze(folder, *, development=False):
    folder = Path(folder)
    if (folder / "manifest.json").exists():
        raise ValueError("Study already frozen; use a separate folder")
    value = configuration(development)
    value["source_sha256"] = sources()
    snapshot_sources(folder, value["source_sha256"])
    value["implementation_frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
    value["base_git_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    seed_hex = secrets.token_hex(16)
    cases = DEVELOPMENT if development else generate_cases(int(seed_hex, 16), per_group=4)
    value.update(seed_hex=seed_hex, cases=[asdict(c) for c in cases],
                 tasks_sampled_at_utc=datetime.now(timezone.utc).isoformat())
    if sources() != value["source_sha256"]:
        raise ValueError("Implementation changed during task sampling")
    value["manifest_sha256"] = digest(value)
    write_json(folder / "manifest.json", value)
    return value


def check_frozen(folder):
    value = json.loads((Path(folder) / "manifest.json").read_text())
    if value["manifest_sha256"] != digest({k: v for k, v in value.items() if k != "manifest_sha256"}):
        raise ValueError("Manifest was modified")
    if value["source_sha256"] != sources():
        raise ValueError("Implementation changed after freeze")
    current = json.loads(canonical(configuration(value["development"])))
    if any(value.get(k) != v for k, v in current.items()):
        raise ValueError("Protocol changed after freeze")
    expected = DEVELOPMENT if value["development"] else generate_cases(int(value["seed_hex"], 16), per_group=4)
    if value["cases"] != json.loads(canonical([asdict(c) for c in expected])):
        raise ValueError("Cases differ from frozen sampling")
    return value


def context(case):
    return dict(goal=GOAL, correction=CORRECTION, expression=case.expression, variables=list(case.variables))


def normalized(expression):
    try:
        return ast.dump(ast.parse(expression.strip(), mode="eval"), include_attributes=False)
    except (SyntaxError, ValueError, RecursionError):
        return expression


def parse_baseline(text):
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("Nonfinite JSON")
    value = json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
    if value is None:
        return None
    if (type(value) is not dict or not {"action", "expression"} <= set(value)
            or set(value) - {"action", "expression", "calculation"}
            or value["action"] != "final" or type(value["expression"]) is not str
            or ("calculation" in value and type(value["calculation"]) is not str)):
        raise ValueError("Expected final action, expression and optional calculation strings")
    return value


def strong_retry(case, complete, max_calls):
    history, signatures = [], {}
    output, status, checks, calls, duplicate_rejections = None, "budget_exhausted", 0, 0, 0
    for _ in range(max_calls):
        prompt = dict(task=context(case), previous_attempts=history,
                      response_schema={"action": "final", "expression": "complete polynomial expansion",
                                       "calculation": "optional concise mathematical calculation; untrusted"},
                      controller=("Work factor by factor and check signs/coefficient arithmetic. You may write a "
                                  "concise calculation in calculation before your final expression. An exact checker "
                                  "must accept your complete expansion. Use the full attempt history and correct "
                                  "the named monomial on rejection. Repeating a rejected expression cannot pass. "
                                  "An equivalent incomplete attempt can guide your next calculation but is not delivered."))
        calls += 1
        try:
            text = complete(canonical(prompt))
        except RequestFailure:
            status = "technical_failure"
            break
        try:
            response = parse_baseline(text)
        except (ValueError, TypeError, RecursionError):
            status = "schema_failure"
            break
        if response is None:
            status = "abstained"
            break
        checks += 1
        observation = check_answer(case, response["expression"])
        if observation["identity"] == "verified" and observation["expanded"]:
            output, status = response["expression"], "delivered"
            break
        signature = normalized(response["expression"])
        occurrences = signatures.get(signature, 0) + 1
        signatures[signature] = occurrences
        duplicate_rejections += occurrences > 1
        history.append(dict(expression=response["expression"], observation=observation,
                            times_this_expression_rejected=occurrences))
    return dict(status=status, stop_reason=status, output=output,
                work=dict(proposal_calls=calls, identity_checks=checks, primitive_node_visits=0, pattern_attempts=0),
                accepted_steps=int(output is not None), repeat_rejections=duplicate_rejections,
                retrieved_rule_ids=[], committed_rule_uses=[], admissions=[], learning_errors=[],
                observations=history)


def production_run(case, library, complete, max_calls, *, structured, learn=False):
    counts = WorkCounts()
    abstained = False
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
                                   guidance="structured" if structured else "default")
    outcome = learner.run(Task(case.id, GOAL, "algebra"), learn=learn)
    run = outcome.result.run_result
    solved = run.status == "solved" and run.residual.solved
    uses = []
    for fact in run.state.facts:
        if fact.value[2]:
            record = library.get(fact.value[2])
            uses.append(dict(rule_id=fact.value[2], fingerprint=fact.value[3],
                             source_task_id=record.candidate.source_task_id,
                             cross_task=record.candidate.source_task_id != case.id))
    signatures, duplicates, contract_rejections = set(), 0, 0
    for event in run.trace:
        if event.step <= 0 or event.decision is not Decision.REJECT or event.candidate is None:
            continue
        contract_rejections += "malformed_or_unsupported_rewrite" in event.reasons
        key = (event.before.revision, event.candidate.action, event.candidate.target,
               "raw", event.candidate.claim)
        try:
            claim = json.loads(event.candidate.claim)
            if (type(claim) is dict and set(claim) == {"before", "after", "rule_id"}
                    and all(type(value) is str for value in claim.values())):
                key = (event.before.revision, event.candidate.action, event.candidate.target,
                       "parsed", normalized(claim["before"]), normalized(claim["after"]), claim["rule_id"])
        except (ValueError, KeyError, TypeError, RecursionError):
            pass
        duplicates += key in signatures
        signatures.add(key)
    failure_types = sorted({reason for event in run.trace if event.decision is Decision.INTERRUPT
                            for reason in event.reasons})
    return dict(status="abstained" if abstained else run.status,
                stop_reason="model_abstained" if abstained else run.stop_reason,
                failure_types=failure_types,
                output=run.state.facts[-1].value[1] if solved else None,
                work=asdict(counts),
                accepted_steps=sum(e.step > 0 and e.decision is Decision.ACCEPT for e in run.trace),
                repeat_rejections=duplicates, rewrite_contract_rejections=contract_rejections,
                retrieved_rule_ids=list(outcome.retrieved_ids),
                committed_rule_uses=uses,
                admissions=[dict(id=r.candidate.id, status=r.status, fingerprint=r.fingerprint) for r in outcome.admissions],
                learning_errors=list(outcome.learning_errors),
                observations=[dict(step=e.step, decision=e.decision.value, reasons=list(e.reasons),
                                   candidate=e.candidate.to_dict() if e.candidate else None) for e in run.trace])


def run_case(case, arm, repeat, recorder, library, *, phase, learn=False):
    bound = recorder.bind(f"{phase}-{case.id}-r{repeat}-{arm}")
    start = time.perf_counter()
    result = (strong_retry(case, bound, recorder.settings["max_calls_per_task"])
              if arm == "strong_verify_retry" else
              production_run(case, library, bound, recorder.settings["max_calls_per_task"],
                             structured=arm != "plain_residual", learn=learn))
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
    result["controller_failure"] = (result["status"] == "error" and not result["schema_failure"]
                                    and not result["technical_failure"])
    result["elapsed_seconds"] = result["controller_seconds"]
    write_json(recorder.folder / "cases" / f"{phase}-{case.id}-r{repeat}-{arm}.json", result)
    print(f"{phase} {case.id} r{repeat} {arm}: {result['oracle']['status']} ({len(bound.request_ids)} calls)", flush=True)
    return result


def totals(rows):
    result = old_totals(rows)
    result.update(repeat_rejections=sum(r["repeat_rejections"] for r in rows),
                  rewrite_contract_rejections=sum(r.get("rewrite_contract_rejections", 0) for r in rows),
                  budget_failures=sum(r["budget_failure"] for r in rows),
                  controller_failures=sum(r["controller_failure"] for r in rows))
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
    growing = KnowledgeLibrary(verifiers)
    discovery = [run_case(case, "structured_growth", 0, recorder, growing, phase="discovery", learn=True)
                 for case in DISCOVERY]
    discovery_admissions = admission.calls
    growing.save(folder / "knowledge.json")
    frozen = _snapshot(growing)
    growing = KnowledgeLibrary.load(folder / "knowledge.json", verifiers)
    reload_calls = admission.calls - discovery_admissions
    if _snapshot(growing) != frozen:
        raise RuntimeError("Reload changed the knowledge library")
    cases = [Case(r["id"], r["expression"], tuple(r["variables"]), r["group"]) for r in manifest["cases"]]
    rows = []
    seed = int(manifest["seed_hex"], 16)
    for index, case in enumerate(cases):
        for repeat in range(manifest["repeats"]):
            offset = (seed + index + repeat) % len(ARMS)
            order = ARMS[offset:] + ARMS[:offset]
            with ThreadPoolExecutor(max_workers=manifest["settings"]["workers"]) as pool:
                futures = {arm: pool.submit(run_case, case, arm, repeat, recorder,
                                           growing if arm == "structured_growth" else KnowledgeLibrary(verifiers),
                                           phase="development" if manifest["development"] else "transfer") for arm in order}
                rows.extend(futures[arm].result() for arm in ARMS)
            if _snapshot(growing) != frozen:
                raise RuntimeError("Transfer changed the frozen library")
    check_frozen(folder)
    expected = {(case.id, repeat, arm) for case in cases for repeat in range(manifest["repeats"]) for arm in ARMS}
    if {(r["task_id"], r["repeat"], r["arm"]) for r in rows} != expected or len(rows) != len(expected):
        raise RuntimeError("Missing or duplicate experiment rows")
    def compact(row):
        return {k: v for k, v in row.items() if k != "observations"}
    result = dict(schema_version=1, study=manifest["study"], manifest_sha256=manifest["manifest_sha256"],
                  evaluation_kind="seen_case_development" if manifest["development"] else "frozen_fresh_expression_pilot",
                  completed_at_utc=datetime.now(timezone.utc).isoformat(),
                  wall_seconds=time.perf_counter() - start, requests_present_at_session_start=requests_at_start,
                  settings=manifest["settings"], case_clusters=len(cases), repeats=manifest["repeats"],
                  discovery=[compact(r) for r in discovery], discovery_totals=totals(discovery),
                  transfer=[compact(r) for r in rows], totals={a: totals([r for r in rows if r["arm"] == a]) for a in ARMS},
                  by_group={g: {a: totals([r for r in rows if r["arm"] == a and r["group"] == g]) for a in ARMS}
                            for g in sorted({c.group for c in cases})},
                  statistics=None if manifest["development"] else analyze(rows, ARMS),
                  physical_requests=recorder.compact_summary(),
                  library=dict(verified_rules=len(growing.lookup("algebra")),
                               snapshot_sha256=hashlib.sha256(frozen.encode()).hexdigest(),
                               reverified_on_reload=True, frozen_during_transfer=True,
                               discovery_admission_calls=discovery_admissions, reload_calls=reload_calls,
                               transfer_admission_calls=admission.calls - discovery_admissions - reload_calls,
                               rules=[dict(id=r.candidate.id, source_task_id=r.candidate.source_task_id,
                                           fingerprint=r.fingerprint, statement=r.candidate.statement)
                                      for r in growing.lookup("algebra")]),
                  limitations=manifest["protocol"])
    write_json(folder / "summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run"))
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--development", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "freeze":
        manifest = freeze(args.folder, development=args.development)
        print(json.dumps(dict(manifest_sha256=manifest["manifest_sha256"], development=manifest["development"],
                              case_count=len(manifest["cases"]), repeats=manifest["repeats"])))
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
