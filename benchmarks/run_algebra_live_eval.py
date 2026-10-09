"""Prospectively frozen, same-model four-arm algebra pilot.

Only ``run --live`` accesses the network. Keep the output folder outside Git;
publish the frozen manifest and compact summary, never raw request logs.
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
import subprocess
import time

from benchmarks.knowledge_growth import Case, _snapshot, score_expansion
from benchmarks.live_eval_support import RecordedCompletion, RequestFailure
from resimind import Task
from resimind.core import Decision
from resimind.domains.algebra import AlgebraVerifier, verify_identity
from resimind.domains.polynomial_learning import (
    PolynomialProblem, WorkCounts, build_learning_agent, is_expanded,
)
from resimind.knowledge import KnowledgeLibrary

ROOT = Path(__file__).resolve().parents[1]
ARMS = ("react", "verify_retry", "residual", "residual_growth")
SYSTEM = "Follow the requested JSON schema exactly. Return JSON only, without Markdown."
SETTINGS = dict(model="deepseek-flash", temperature=0.0, thinking="disabled",
                max_output_tokens=4096, timeout_seconds=90, max_physical_calls=360,
                max_prompt_bytes=48000, max_total_output_tokens=1474560,
                max_calls_per_task=8, workers=4, sdk_retries=0)
GOAL = (
    "Expand the supplied rational polynomial completely. Equivalent full expansions in one step "
    "are allowed; there is no restriction to primitive rewrites. Combining like terms is optional. "
    "No multiplication or power may enclose an addition/subtraction in the final expression. "
    "Grammar: declared variables, integer constants, +, -, *, integer powers **0 through **16, "
    "and division only by a nonzero integer literal. No decimals, functions or symbolic denominators. "
    "Use the available checker feedback, if any. Return null if unable to propose an answer."
)
DEVELOPMENT = (
    Case("dev-square", "(2*x-3*y)**2", ("x", "y"), "development"),
    Case("dev-product", "(x+1)*(x-1)", ("x",), "development"),
)
DISCOVERY = (
    Case("discover-square", "(u+v)**2", ("u", "v"), "discovery"),
    Case("discover-cube", "(u+v)**3", ("u", "v"), "discovery"),
    Case("discover-difference", "(u+v)*(u-v)", ("u", "v"), "discovery"),
)
TRANSFER = (
    Case("fractional-square", "(2*x/3+5*y/7)**2", ("x", "y"), "applicability"),
    Case("affine-cube", "(2*x+3*y+1)**3", ("x", "y"), "applicability"),
    Case("nested-square", "((x+y)**2+(x-y))**2", ("x", "y"), "applicability"),
    Case("trinomial-cube", "(x+y+z)**3", ("x", "y", "z"), "applicability"),
    Case("difference-scaled", "(2*x+3*y)*(2*x-3*y)", ("x", "y"), "applicability"),
    Case("difference-nested", "((x+y)+2*z)*((x+y)-2*z)", ("x", "y", "z"), "applicability"),
    Case("unmatched-product", "(x+2*y)*(x-3*y)*(2*x+y)", ("x", "y"), "unmatched-control"),
    Case("sixth-power", "(2*x-3*y+z)**6", ("x", "y", "z"), "unmatched-control"),
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def source_hashes():
    paths = sorted((ROOT / "src/resimind").rglob("*.py"))
    paths += [Path(__file__), ROOT / "benchmarks/knowledge_growth.py",
              ROOT / "benchmarks/live_eval_support.py"]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def protocol():
    return dict(schema_version=1, study="same-model-algebra-v1", settings=SETTINGS,
                system_prompt=SYSTEM, common_goal=GOAL, arms=list(ARMS),
                development=[asdict(c) for c in DEVELOPMENT],
                discovery=[asdict(c) for c in DISCOVERY], transfer=[asdict(c) for c in TRANSFER],
                source_sha256=source_hashes(),
                rules={
                    "react": "Model chooses check_identity or final; final is not delivery-gated.",
                    "verify_retry": "Whole-answer proposals; mandatory identity+expansion check; retry original task.",
                    "residual": "Production Agent; gated intermediate rewrites and persistent checked state.",
                    "residual_growth": "Identical production Agent plus frozen independently admitted discovery rules.",
                    "budget": "Eight model calls per task, same model/parameters/output cap; stop on completion, abstention, schema failure or technical failure. The harness normalizes JSON null to immediate abstention in the production arms.",
                    "discovery": "Three sequential model tasks; admission and disk reload are reverified; costs charged separately.",
                    "transfer": "No learning; no selective reruns; arm submission order rotates by task, four concurrent workers.",
                    "scoring": "Separate exact degree-complete Cartesian-grid oracle, never provided to proposers.",
                    "scope": "Handcrafted prospective mechanism pilot; one run, eight tasks, not an established benchmark or SOTA comparison.",
                    "confounds": "Schemas, state visibility, mandatory gating and stopping differ intentionally; this does not isolate residual scheduling alone.",
                    "residual_limit": "Polynomial adapter has one binary expansion obligation, not structured subterm residuals.",
                })


def freeze(folder):
    folder = Path(folder)
    path = folder / "manifest.json"
    if path.exists():
        raise ValueError("Manifest already exists; use a new study folder")
    value = protocol()
    value["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
    value["base_git_commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    value["protocol_sha256"] = digest({k: v for k, v in value.items() if k != "protocol_sha256"})
    write_json(path, value)
    return value


def check_frozen(folder):
    value = json.loads((Path(folder) / "manifest.json").read_text())
    if value["protocol_sha256"] != digest({k: v for k, v in value.items() if k != "protocol_sha256"}):
        raise ValueError("Frozen manifest was modified")
    current = json.loads(canonical(protocol()))
    if any(value.get(k) != v for k, v in current.items()):
        raise ValueError("Source or protocol changed after freeze; create a separately labelled study")
    return value


def context(case):
    return dict(goal=GOAL, expression=case.expression, variables=list(case.variables))


def strict_object(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate key")
            value[key] = item
        return value
    value = json.loads(text, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {"action", "expression"}
            or type(value["action"]) is not str or type(value["expression"]) is not str):
        raise ValueError("Expected exactly action and expression strings")
    return value


def check_answer(case, expression):
    result = verify_identity(case.expression, expression, case.variables)
    expanded = result.status == "verified" and is_expanded(expression)
    return dict(identity=result.status, reason=result.reason, expanded=expanded)


def baseline(case, arm, complete, max_calls):
    """Operational action-observation and mandatory-check baselines."""
    if arm not in ("react", "verify_retry"):
        raise ValueError("Unknown baseline")
    history, output, calls, checks = [], None, 0, 0
    status = "budget_exhausted"
    for _ in range(max_calls):
        request = dict(task=context(case), history=history,
                       response_schema={"action": "check_identity|final" if arm == "react" else "final",
                                        "expression": "polynomial string"},
                       controller=(
                           "Choose check_identity to call the exact identity tool, observe its result, then continue; "
                           "choose final to deliver. Checking is optional. A final action ends the task immediately."
                           if arm == "react" else
                           "Submit a complete expansion using final. An external exact checker accepts only a valid "
                           "complete expansion. Otherwise use the feedback to retry the original task."))
        calls += 1
        try:
            text = complete(canonical(request))
        except RequestFailure:
            status = "technical_failure"
            break
        try:
            candidate = strict_object(text)
            if candidate is None:
                status = "abstained"
                break
            if candidate["action"] not in (("check_identity", "final") if arm == "react" else ("final",)):
                raise ValueError("Unregistered action")
        except (ValueError, TypeError, RecursionError):
            history.append(dict(response=text, observation="malformed_response"))
            status = "schema_failure"
            break
        if arm == "react" and candidate["action"] == "final":
            output, status = candidate["expression"], "delivered"
            break
        checks += 1
        observation = check_answer(case, candidate["expression"])
        history.append(dict(response=candidate, observation=observation))
        if arm == "verify_retry" and observation["identity"] == "verified" and observation["expanded"]:
            output, status = candidate["expression"], "delivered"
            break
    return dict(status=status, stop_reason=status, output=output, partial_output=None,
                work=dict(proposal_calls=calls, primitive_node_visits=0, pattern_attempts=0, identity_checks=checks),
                accepted_steps=int(output is not None and arm == "verify_retry"),
                retrieved_rule_ids=[], committed_rule_uses=[], admissions=[], learning_errors=[],
                observations=history)


class ModelAbstention(ValueError):
    """Harness-only stop signal, normalized separately from technical errors."""


def residual(case, library, complete, max_calls, *, learn=False):
    counts = WorkCounts()
    abstained = False
    def with_context(prompt):
        nonlocal abstained
        payload = json.loads(prompt)
        payload["evaluation_task"] = context(case)
        text = complete(canonical(payload))
        if text.strip() == "null":
            abstained = True
            raise ModelAbstention()
        return text
    learner = build_learning_agent(PolynomialProblem(case.expression, case.variables), library,
                                   max_steps=max_calls, counts=counts, complete=with_context)
    outcome = learner.run(Task(case.id, GOAL, "algebra"), learn=learn)
    run = outcome.result.run_result
    last = run.state.facts[-1].value[1] if run.state.facts else None
    solved = run.status == "solved" and run.residual.solved
    uses = []
    for fact in run.state.facts:
        if fact.value[2]:
            record = library.get(fact.value[2])
            uses.append(dict(rule_id=fact.value[2], fingerprint=fact.value[3],
                             source_task_id=record.candidate.source_task_id,
                             cross_task=record.candidate.source_task_id != case.id))
    return dict(status="abstained" if abstained else run.status,
                stop_reason="model_abstained" if abstained else run.stop_reason,
                output=last if solved else None, partial_output=last if not solved else None,
                work=asdict(counts),
                accepted_steps=sum(event.step > 0 and event.decision is Decision.ACCEPT for event in run.trace),
                retrieved_rule_ids=list(outcome.retrieved_ids), committed_rule_uses=uses,
                admissions=[dict(id=r.candidate.id, status=r.status, fingerprint=r.fingerprint) for r in outcome.admissions],
                learning_errors=list(outcome.learning_errors),
                observations=[dict(step=e.step, decision=e.decision.value, reasons=list(e.reasons)) for e in run.trace])


def run_case(case, arm, recorder, library, *, phase, learn=False):
    bound = recorder.bind(f"{phase}-{case.id}-{arm}")
    started = time.perf_counter()
    result = (baseline(case, arm, bound, SETTINGS["max_calls_per_task"])
              if arm in ("react", "verify_retry") else
              residual(case, library, bound, SETTINGS["max_calls_per_task"], learn=learn))
    result["elapsed_seconds"] = time.perf_counter() - started
    result.update(task_id=case.id, expression=case.expression, variables=list(case.variables),
                  group=case.group, arm=arm, request_ids=list(bound.request_ids))
    result["oracle"] = (score_expansion(case.expression, result["output"], case.variables)
                        if result["output"] is not None else dict(status="missing", reason="no_delivered_answer"))
    result["success"] = result["oracle"]["status"] == "valid"
    result["invalid_delivered"] = result["oracle"]["status"] == "invalid"
    result["unsuccessful_delivered"] = result["output"] is not None and not result["success"]
    records = [recorder.record(key) for key in bound.request_ids]
    result["technical_failure"] = any(r["status"] != "complete" for r in records)
    result["schema_failure"] = result["status"] == "schema_failure" or (
        result["status"] == "error" and not result["technical_failure"])
    result["usage"] = recorder.compact_summary(bound.request_ids)["usage"]
    result["model_seconds"] = sum(r.get("elapsed_seconds", 0.0) for r in records)
    result["api_attempts"] = sum(r.get("api_attempted", False) for r in records)
    write_json(recorder.folder / "cases" / f"{phase}-{case.id}-{arm}.json", result)
    print(f"{phase} {case.id} {arm}: {result['oracle']['status']}, calls={len(bound.request_ids)}", flush=True)
    return result


class CountedAdmission:
    def __init__(self):
        self.calls = 0

    def verify(self, candidate):
        self.calls += 1
        return AlgebraVerifier().verify(candidate)


def totals(rows):
    def usage_total(key):
        values = [r["usage"][key] for r in rows]
        return sum(values) if all(type(v) is int for v in values) else None
    return dict(tasks=len(rows), successes=sum(r["success"] for r in rows),
                invalid_delivered=sum(r["invalid_delivered"] for r in rows),
                unsuccessful_delivered=sum(r["unsuccessful_delivered"] for r in rows),
                technical_failures=sum(r["technical_failure"] for r in rows),
                schema_failures=sum(r["schema_failure"] for r in rows),
                model_calls=sum(len(r["request_ids"]) for r in rows),
                api_attempts=sum(r["api_attempts"] for r in rows),
                input_tokens=usage_total("input_tokens"), output_tokens=usage_total("output_tokens"),
                identity_checks=sum(r["work"]["identity_checks"] for r in rows),
                accepted_steps=sum(r["accepted_steps"] for r in rows),
                rule_uses=sum(len(r["committed_rule_uses"]) for r in rows),
                tasks_with_cross_task_reuse=sum(any(u["cross_task"] for u in r["committed_rule_uses"]) for r in rows),
                model_seconds=sum(r["model_seconds"] for r in rows),
                summed_task_seconds=sum(r["elapsed_seconds"] for r in rows))


def run_study(folder, client):
    folder = Path(folder)
    manifest = check_frozen(folder)
    if (folder / "summary.json").exists():
        raise ValueError("Completed study already exists; do not overwrite its original timing")
    recorder = RecordedCompletion(folder, SETTINGS, client, SYSTEM)
    requests_at_start = len(recorder.records())
    start = time.perf_counter()
    admission = CountedAdmission()
    verifiers = {"algebra": admission}
    development = []
    # Smoke tasks are separate from reported transfer; they cannot tune a frozen run.
    for case in DEVELOPMENT:
        for arm in ARMS:
            development.append(run_case(case, arm, recorder, KnowledgeLibrary(verifiers), phase="development"))
    growing = KnowledgeLibrary(verifiers)
    discovery = [run_case(case, "residual_growth", recorder, growing, phase="discovery", learn=True)
                 for case in DISCOVERY]
    admission_calls = admission.calls
    growing.save(folder / "knowledge.json")
    frozen_snapshot = _snapshot(growing)
    growing = KnowledgeLibrary.load(folder / "knowledge.json", verifiers)
    reload_calls = admission.calls - admission_calls
    if _snapshot(growing) != frozen_snapshot:
        raise RuntimeError("Reload changed verified knowledge")
    transfer = []
    for index, case in enumerate(TRANSFER):
        order = ARMS[index % len(ARMS):] + ARMS[:index % len(ARMS)]
        with ThreadPoolExecutor(max_workers=SETTINGS["workers"]) as pool:
            futures = {arm: pool.submit(run_case, case, arm, recorder,
                                       growing if arm == "residual_growth" else KnowledgeLibrary(verifiers),
                                       phase="transfer") for arm in order}
            rows = {arm: futures[arm].result() for arm in ARMS}
        transfer.append(dict(task_id=case.id, **rows))
        if _snapshot(growing) != frozen_snapshot:
            raise RuntimeError("Transfer mutated the frozen library")
    # Summary deliberately excludes full prompts/responses and intermediate model outputs.
    def compact(row):
        return {key: value for key, value in row.items() if key not in ("observations", "partial_output")}
    result = dict(schema_version=1, study=manifest["study"], protocol_sha256=manifest["protocol_sha256"],
                  evaluation_kind="prospectively_frozen_same_model_handcrafted_pilot",
                  completed_at_utc=datetime.now(timezone.utc).isoformat(),
                  wall_seconds=time.perf_counter() - start,
                  requests_present_at_session_start=requests_at_start,
                  settings=SETTINGS, development_totals=totals(development),
                  discovery=[compact(r) for r in discovery], discovery_totals=totals(discovery),
                  transfer=[dict(task_id=r["task_id"], **{a: compact(r[a]) for a in ARMS}) for r in transfer],
                  totals={a: totals([r[a] for r in transfer]) for a in ARMS},
                  physical_requests=recorder.compact_summary(),
                  library=dict(verified_rules=len(growing.lookup("algebra")),
                               snapshot_sha256=hashlib.sha256(frozen_snapshot.encode()).hexdigest(),
                               reload_reverified=True, frozen_during_transfer=True,
                               admission_calls=admission_calls, reload_calls=reload_calls,
                               transfer_admission_calls=admission.calls-admission_calls-reload_calls,
                               rules=[dict(id=r.candidate.id, source_task_id=r.candidate.source_task_id,
                                           statement=r.candidate.statement, fingerprint=r.fingerprint)
                                      for r in growing.lookup("algebra")]),
                  limitations=manifest["rules"])
    write_json(folder / "summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run"))
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "freeze":
        value = freeze(args.folder)
        print(value["protocol_sha256"])
        return
    if not args.live:
        parser.error("run requires --live; this makes paid model calls")
    check_frozen(args.folder)
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com",
                    timeout=SETTINGS["timeout_seconds"], max_retries=0)
    result = run_study(args.folder, client)
    print(json.dumps(result["totals"], indent=2))


if __name__ == "__main__":
    main()
