"""Offline, paired refinement replay of every nonempty candidate from a live run.

No model requests are made. Public tasks, self-translations and saved plans are
repaired in isolated workers. Gold is loaded only after every worker finishes.
This measures changes to seen candidates, not a new end-to-end success rate.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from experiments.chinatravel.recording import save
from experiments.chinatravel.run import clean_environment, isolation_check, read, sha
from experiments.chinatravel.sandbox import profile_for_worker, SANDBOX_EXEC
from experiments.chinatravel_repair.replay import source_hashes, verify_inputs


def frozen_sources():
    return {**source_hashes(), str(Path(__file__).resolve().relative_to(ROOT)): sha(Path(__file__))}


def run(source, output, upstream, data):
    original = verify_inputs(source, data, upstream)
    isolation_check(source, data, upstream)
    isolation_check(output, data, upstream)
    uids = original["selected_uids"]
    if len(uids) != 2 or original["arms"] != ["resimind_repair"]:
        raise ValueError("expected_two_task_repair_development_run")
    if not all((source / "runs" / uid / "resimind_repair/result.json").is_file() for uid in uids):
        raise ValueError("source_live_runs_must_be_terminal")
    output.mkdir(parents=True, exist_ok=False)
    jobs, source_records, task_sources = [], {}, []
    for uid in uids:
        query_path = source / "queries/public" / f"{uid}.json"
        if sha(query_path) != original["public_query_sha256"][uid]:
            raise ValueError("source_public_query_changed")
        query = read(query_path)
        if set(query) != {"uid", "nature_language"}:
            raise ValueError("public_query_only")
        worker = source / "runs" / uid / "resimind_repair/worker"
        translation_path = worker / "self_translation.json"
        source_records[str(translation_path.relative_to(source))] = sha(translation_path)
        candidate_paths = sorted((worker / "raw_candidates").glob("candidate_*.json"))
        attempts = []
        for path in candidate_paths:
            raw = read(path)
            source_records[str(path.relative_to(source))] = sha(path)
            attempts.append({"attempt": raw["attempt"], "present": bool(raw.get("plan"))})
            if not raw.get("plan"):
                continue
            directory = output / "jobs" / uid / str(raw["attempt"])
            save(directory / "input.json", dict(uid=uid, attempt=raw["attempt"], query=query,
                 plan=raw["plan"], self_translation=read(translation_path)))
            jobs.append(dict(uid=uid, attempt=raw["attempt"],
                             input=str((directory / "input.json").relative_to(output)),
                             input_sha256=sha(directory / "input.json")))
        task_sources.append({"uid": uid, "recorded_attempts": attempts,
                             "terminal_attempt": attempts[-1]["attempt"],
                             "terminal_candidate_present": attempts[-1]["present"]})
    if len(jobs) != 5 or len({job["uid"] for job in jobs}) != 2:
        raise ValueError("expected_all_five_nonempty_candidates")
    hashes = frozen_sources()
    with zipfile.ZipFile(output / "source-at-freeze.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, expected in hashes.items():
            if sha(ROOT / relative) != expected:
                raise ValueError("source_changed_while_archiving")
            archive.write(ROOT / relative, relative)
    manifest = dict(kind="offline_seen_live_candidate_refinement", frozen_at=datetime.now(timezone.utc).isoformat(),
                    source_live_manifest_sha256=sha(source / "manifest.json"), jobs=jobs,
                    source_records_sha256=source_records, source_sha256=hashes,
                    source_zip_sha256=sha(output / "source-at-freeze.zip"),
                    upstream_commit=original["upstream_commit"], database_sha256=original["database_sha256"],
                    model_api_calls=0, source_task_count=2, source_candidate_count=5,
                    task_sources=task_sources, max_repair_rounds=3, python=sys.version,
                    dependencies=subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True).splitlines())
    save(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha(output / "manifest.json") + "\n")

    def run_job(job):
        input_path = output / job["input"]
        directory = input_path.parent
        profile = profile_for_worker(sys.executable, upstream, ROOT, directory,
                                     input_path, data / "database", 65431)
        command = [SANDBOX_EXEC, "-p", profile, sys.executable, "-m",
                   "experiments.chinatravel_repair.replay_worker", "--input", str(input_path),
                   "--output", str(directory / "repair.json"), "--upstream", str(upstream)]
        with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            result = subprocess.run(command, cwd=upstream, env=clean_environment(directory),
                                    stdout=stdout, stderr=stderr, timeout=150, close_fds=True)
        if result.returncode or not (directory / "repair.json").is_file():
            raise RuntimeError(f"repair_worker_failed:{job['uid']}:{job['attempt']}")
        return {"uid": job["uid"], "attempt": job["attempt"], "sha256": sha(directory / "repair.json")}

    with ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(run_job, jobs))
    save(output / "completed.json", {"outputs": outputs})
    if hashes != frozen_sources():
        raise ValueError("frozen_sources_changed")
    for relative, expected in source_records.items():
        if sha(source / relative) != expected:
            raise ValueError("source_record_changed")
    rows = []
    for job, completed in zip(jobs, outputs):
        path = output / job["input"]
        if sha(path) != job["input_sha256"] or sha(path.with_name("repair.json")) != completed["sha256"]:
            raise ValueError("repair_records_changed")
        rows.append((job, read(path), read(path.with_name("repair.json"))))
    # Every repair is complete and hashed before the parent imports scoring or
    # reads gold. Children cannot read the source live-run directory.
    from experiments.chinatravel.official_scoring import score
    cases = []
    for job, task, repaired in rows:
        gold_path = source / "queries/gold" / (job["uid"] + ".json")
        if sha(gold_path) != original["gold_query_sha256"][job["uid"]]:
            raise ValueError("source_gold_changed")
        values = {}
        for name, plan in (("original", task["plan"]), ("draft", repaired["draft"]),
                           ("delivered", repaired["plan"] or {})):
            evaluated = score(read(gold_path), plan, upstream_root=upstream)
            values[name] = {key: evaluated[key] for key in ("schema", "env", "logical", "final")}
        cases.append(dict(uid=job["uid"], attempt=job["attempt"], score=values,
                          local_accepted=repaired["status"] == "accepted", stop_reason=repaired["stop_reason"],
                          resolved=repaired["resolved"], regressed=repaired["regressed"],
                          committed_transactions=sum(t["result"]["status"] in {"accepted", "repaired"}
                                                     for t in repaired["transactions"])))
    tasks = []
    for task in task_sources:
        subset = [row for row in cases if row["uid"] == task["uid"]]
        # Selection depends only on local acceptance and original order, never gold.
        selected = next((row for row in subset if row["local_accepted"]), None)
        terminal = next((row for row in subset if row["attempt"] == task["terminal_attempt"]), None)
        tasks.append({**task, "first_locally_accepted_attempt": selected["attempt"] if selected else None,
                      "first_locally_accepted_official_pass": selected["score"]["delivered"]["final"] if selected else False,
                      "terminal_candidate_score": terminal["score"] if terminal else None})
    report = dict(kind="offline_seen_candidate_refinement_not_new_model_run", model_api_calls=0,
                  manifest_sha256=sha(output / "manifest.json"), source_task_count=2, candidate_count=5,
                  official={name: {key: sum(row["score"][name][key] for row in cases)
                                   for key in ("schema", "env", "logical", "final")}
                            for name in ("original", "draft", "delivered")},
                  candidate_local_accepts=sum(row["local_accepted"] for row in cases),
                  tasks_with_local_accept=sum(row["first_locally_accepted_attempt"] is not None for row in tasks),
                  cases=cases, tasks=tasks)
    save(output / "report.json", report)
    print(json.dumps({k:v for k,v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "output", "upstream", "data"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    run(*(getattr(args, name).resolve() for name in ("source", "output", "upstream", "data")))
