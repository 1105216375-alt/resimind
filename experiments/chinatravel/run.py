"""Freeze, run and score a bounded original-task ChinaTravel pilot.

Runtime installation/data instructions are in PROTOCOL.md. The live command
requires --live; only the parent reads DEEPSEEK_API_KEY (or --credential-file).
No provider key, gold constraint, or offline score reaches the solving process.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from multiprocessing.connection import Listener
import os
from pathlib import Path
import secrets
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time

from .recording import Recorder, digest, save

ROOT = Path(__file__).resolve().parents[2]
ARMS = ("official_react", "official_nesy", "resimind_react")
SETTINGS = dict(
    model="deepseek-flash", temperature=0, reasoning_effort="none", max_output_tokens=8192,
    max_calls=128, max_request_prompt_bytes=320000, max_total_prompt_bytes=16000000,
    max_total_output_tokens=65536, request_timeout_seconds=90, wall_seconds=600,
    search_seconds=300, max_proposals=3, workers=3, sdk_retries=0,
    cpu_seconds=420, max_file_bytes=67108864,
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def source_hashes():
    files = list((ROOT / "src/resimind").rglob("*.py"))
    files += list((ROOT / "experiments/chinatravel").glob("*.py"))
    files += [ROOT / "experiments/chinatravel/PROTOCOL.md", ROOT / "experiments/__init__.py"]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(files)}


def upstream_check(upstream, expected):
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=upstream, text=True).strip()
    if actual != expected:
        raise RuntimeError("upstream_revision_mismatch")
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--"], cwd=upstream,
                   check=True, stdout=subprocess.DEVNULL)


def isolation_check(folder, data, upstream):
    readable_trees = (ROOT / "src", ROOT / "experiments", upstream / "chinatravel", data / "database")
    for private in (folder / "queries/gold", data / "gold", data / "source"):
        if any(private.resolve().is_relative_to(root.resolve()) for root in readable_trees):
            raise RuntimeError("gold_or_raw_data_inside_worker_readable_tree")


def freeze(folder, data, upstream, phase):
    isolation_check(folder, data, upstream)
    dataset = read(data / "dataset_manifest.json")
    upstream_check(upstream, dataset["upstream"]["commit"])
    for relative, expected in dataset["normalized_files"].items():
        if sha(data / relative) != expected:
            raise RuntimeError("normalized_data_mismatch")
    all_ids = sorted(dataset["all_uids"])
    dev = all_ids[:2]
    if dataset["dev_uids"] != dev:
        raise RuntimeError("development_split_mismatch")
    formal = sorted(set(all_ids) - set(dev),
                    key=lambda uid: hashlib.sha256(("resimind-chinatravel-v1:" + uid).encode()).hexdigest())[:12]
    selected = dev if phase == "development" else formal
    folder.mkdir(parents=True, exist_ok=False)
    for uid in selected:
        query = read(data / "public" / f"{uid}.json")
        if set(query) != {"uid", "nature_language"}:
            raise RuntimeError("public_query_has_extra_fields")
        save(folder / "queries/public" / f"{uid}.json", query)
        save(folder / "queries/gold" / f"{uid}.json", read(data / "gold" / f"{uid}.json"))
    shutil.copyfile(data / "dataset_manifest.json", folder / "dataset_manifest.json")
    if (upstream / "chinatravel/environment/database").resolve() != (data / "database").resolve():
        raise RuntimeError("upstream_uses_different_database")
    database = {str(p.relative_to(data / "database")): sha(p)
                for p in sorted((data / "database").rglob("*")) if p.is_file()}
    schedule = [{"uid": uid, "arm": arm} for i, uid in enumerate(selected)
                for arm in ARMS[i % 3:] + ARMS[:i % 3]]
    manifest = dict(
        schema_version=1, phase=phase, frozen_at=datetime.now(timezone.utc).isoformat(),
        settings=SETTINGS, selected_uids=selected, excluded_development_uids=dev,
        selection="sha256('resimind-chinatravel-v1:'+uid) ascending, first 12 after dev exclusion",
        schedule=schedule, arms=ARMS, dataset_manifest_sha256=sha(folder / "dataset_manifest.json"),
        upstream_commit=dataset["upstream"]["commit"], upstream_tracked_files_dirty=False,
        database_sha256=database,
        public_query_sha256={uid: sha(folder / "queries/public" / f"{uid}.json") for uid in selected},
        gold_query_sha256={uid: sha(folder / "queries/gold" / f"{uid}.json") for uid in selected},
        source_sha256=source_hashes(), python=sys.version,
        dependencies=subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True).splitlines(),
        pre_freeze_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    )
    save(folder / "manifest.json", manifest)
    (folder / "manifest.sha256").write_text(sha(folder / "manifest.json") + "\n")
    print(json.dumps({"phase": phase, "tasks": len(selected), "runs": len(schedule),
                      "manifest_sha256": sha(folder / "manifest.json")}), flush=True)


def verify(folder, data, upstream, *, check_source=True):
    isolation_check(folder, data, upstream)
    manifest = read(folder / "manifest.json")
    if sha(folder / "manifest.json") != (folder / "manifest.sha256").read_text().strip():
        raise RuntimeError("manifest_mismatch")
    if check_source and manifest["source_sha256"] != source_hashes():
        raise RuntimeError("experiment_source_changed_after_freeze")
    if manifest["settings"] != SETTINGS:
        raise RuntimeError("settings_changed_after_freeze")
    if manifest["python"] != sys.version or manifest["dependencies"] != subprocess.check_output(
            [sys.executable, "-m", "pip", "freeze"], text=True).splitlines():
        raise RuntimeError("runtime_changed_after_freeze")
    upstream_check(upstream, manifest["upstream_commit"])
    if (upstream / "chinatravel/environment/database").resolve() != (data / "database").resolve():
        raise RuntimeError("upstream_uses_different_database")
    if sha(folder / "dataset_manifest.json") != manifest["dataset_manifest_sha256"]:
        raise RuntimeError("dataset_manifest_mismatch")
    for relative, expected in manifest["database_sha256"].items():
        if sha(data / "database" / relative) != expected:
            raise RuntimeError("sandbox_data_mismatch")
    for category in ("public", "gold"):
        for uid, expected in manifest[f"{category}_query_sha256"].items():
            if sha(folder / "queries" / category / f"{uid}.json") != expected:
                raise RuntimeError("query_mismatch")
    return manifest


def clean_environment(output):
    # A whitelist, not a best-effort secret-name blacklist.
    return dict(PATH="/usr/bin:/bin:/usr/sbin:/sbin", HOME=str(output), TMPDIR=str(output),
                LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8", PYTHONHASHSEED="0",
                PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1",
                PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "src")]),
                CHINATRAVEL_OPENAI_RAISE_ERRORS="1", OMP_NUM_THREADS="1",
                OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")


def run_one(folder, data, upstream, manifest, item, client):
    from .sandbox import build_worker_command

    uid, arm = item["uid"], item["arm"]
    job = folder / "runs" / uid / arm
    if (job / "result.json").exists():
        return read(job / "result.json")
    if job.exists():
        # An interrupted/unknown run cannot be silently retried or resampled.
        raise RuntimeError("incomplete_job_requires_audit_no_retry")
    output = job / "worker"
    output.mkdir(parents=True)
    public_query = output / "query.json"
    shutil.copyfile(folder / "queries/public" / f"{uid}.json", public_query)
    started = time.monotonic()
    deadline = started + manifest["settings"]["wall_seconds"]
    recorder = Recorder(job / "requests", manifest["settings"], client, deadline)
    auth = secrets.token_bytes(32)
    result = dict(type="result", status="technical_failure", plan=None,
                  details={"code": "worker_did_not_finish"})
    process = connection = watchdog = None
    with Listener(("127.0.0.1", 0), authkey=auth) as listener:
        port = listener.address[1]
        worker_args = ["--host", "127.0.0.1", "--port", str(port), "--auth-key", auth.hex(),
                       "--upstream-root", str(upstream), "--public-query-path", str(public_query),
                       "--output-dir", str(output), "--settings-json", json.dumps(manifest["settings"]),
                       "--sandbox-digest", digest(manifest["database_sha256"]), "--arm", arm]
        command = build_worker_command(sys.executable, worker_args, upstream_root=upstream,
                    public_repo=ROOT, output_dir=output, public_query_path=public_query,
                    database_dir=data / "database", port=port)
        # Listener has no public timeout API; its private socket is used only to
        # enforce a bounded startup wait on our pinned CPython runtime.
        listener._listener._socket.settimeout(20)
        with (job / "launch_stdout.log").open("w") as stdout, (job / "launch_stderr.log").open("w") as stderr:
            try:
                process = subprocess.Popen(command, cwd=upstream, env=clean_environment(output),
                                           stdout=stdout, stderr=stderr, start_new_session=True, close_fds=True)
                def deadline_stop():
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                watchdog = threading.Timer(max(0, deadline - time.monotonic()), deadline_stop)
                watchdog.daemon = True
                watchdog.start()
                connection = listener.accept()
                while time.monotonic() < deadline:
                    if connection.poll(min(0.25, max(0, deadline - time.monotonic()))):
                        message = connection.recv()
                        if message.get("type") == "model_request":
                            connection.send(recorder(message))
                        elif message.get("type") == "result":
                            result = message
                            break
                        else:
                            raise ValueError("worker_protocol")
                    elif process.poll() is not None:
                        break
                else:
                    result.update(status="budget_exhausted", details={"code": "wall_limit"})
            except (Exception, EOFError) as exc:
                result["details"] = {"code": "supervisor_exception", "exception_class": type(exc).__name__}
            finally:
                if watchdog is not None:
                    watchdog.cancel()
                if connection is not None:
                    connection.close()
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=2)
                    result["worker_exit_code"] = process.returncode
    if time.monotonic() >= deadline:
        result.update(status="budget_exhausted", plan=None, details={"code": "wall_limit"})
    result.update(uid=uid, arm=arm, ledger=recorder.totals(), elapsed_seconds=time.monotonic() - started)
    save(job / "result.json", result)
    print(json.dumps({"uid": uid, "arm": arm, "status": result["status"],
                      "delivered": bool(result.get("plan")), "ledger": result["ledger"],
                      "seconds": round(result["elapsed_seconds"], 1)}), flush=True)
    return result


def run(folder, data, upstream, credential_file=None):
    manifest = verify(folder, data, upstream)
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key and credential_file:
        key = read(credential_file).get("api_key")
    if not key:
        raise RuntimeError("Set DEEPSEEK_API_KEY locally or pass a local credential file")
    from openai import OpenAI
    with OpenAI(api_key=key, base_url="https://api.deepseek.com", max_retries=0,
                timeout=SETTINGS["request_timeout_seconds"]) as client:
        with ThreadPoolExecutor(max_workers=manifest["settings"]["workers"]) as pool:
            futures = [pool.submit(run_one, folder, data, upstream, manifest, item, client)
                       for item in manifest["schedule"]]
            for future in as_completed(futures):
                future.result()


def summarize(folder, data, upstream):
    from .official_scoring import score_batch
    manifest = verify(folder, data, upstream)
    gold = {uid: read(folder / "queries/gold" / f"{uid}.json") for uid in manifest["selected_uids"]}
    summary, cases, evaluations = {}, {}, {}
    for arm in ARMS:
        runs = {uid: read(folder / "runs" / uid / arm / "result.json") for uid in gold}
        plans = {uid: result.get("plan") or {} for uid, result in runs.items()}
        evaluated = score_batch(gold, plans, upstream_root=upstream)
        evaluations[arm] = evaluated
        rows = evaluated["per_query"]
        summary[arm] = dict(
            n=len(gold), success=sum(row["final"] for row in rows.values()),
            delivered=sum(bool(plan) for plan in plans.values()),
            invalid_deliveries=sum(bool(plans[uid]) and not rows[uid]["final"] for uid in gold),
            completed_without_plan=sum(result["status"] == "completed" and not plans[uid] for uid, result in runs.items()),
            technical_failures=sum(result["status"] == "technical_failure" for result in runs.values()),
            budget_exhausted=sum(result["status"] == "budget_exhausted" for result in runs.values()),
            api_attempts=sum(result["ledger"]["api_attempts"] for result in runs.values()),
            prompt_tokens=sum(result["ledger"]["prompt_tokens"] for result in runs.values()),
            completion_tokens=sum(result["ledger"]["completion_tokens"] for result in runs.values()),
            unknown_usage_requests=sum(result["ledger"]["unknown_usage_requests"] for result in runs.values()),
            mean_seconds=statistics.mean(result["elapsed_seconds"] for result in runs.values()),
            median_seconds=statistics.median(result["elapsed_seconds"] for result in runs.values()),
            official_metrics=evaluated["metrics"],
        )
        for uid in gold:
            cases.setdefault(uid, {})[arm] = dict(status=runs[uid]["status"], delivered=bool(plans[uid]),
                                                  score=rows[uid], ledger=runs[uid]["ledger"],
                                                  elapsed_seconds=runs[uid]["elapsed_seconds"])
    result = dict(manifest_sha256=sha(folder / "manifest.json"), phase=manifest["phase"],
                  summary=summary, cases=cases)
    save(folder / "scored-results.json", result)
    save(folder / "official-evaluation.json", evaluations)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "run", "score", "verify"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--phase", choices=("development", "formal"), default="formal")
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    folder, data, upstream = (p.resolve() for p in (args.output, args.data, args.upstream))
    if args.command == "freeze":
        freeze(folder, data, upstream, args.phase)
    elif args.command == "run":
        if not args.live:
            parser.error("run requires --live; no requests sent")
        run(folder, data, upstream, args.credential_file)
    elif args.command == "score":
        summarize(folder, data, upstream)
    else:
        verify(folder, data, upstream)
        print("Frozen inputs, code, upstream and sandbox verified.")


if __name__ == "__main__":
    main()
