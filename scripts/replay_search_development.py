"""Local-only paired search regression on all five previously seen candidates.

Full records stay at --output outside the public repository. Gold is read only
after all sandboxed search workers finish and their outputs have been hashed.
No model calls. This is not a held-out/end-to-end benchmark or an ablation.
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
    files = [p for p in (ROOT / "experiments/chinatravel_search").glob("*.py")
             if not p.name.startswith("test_")] + [Path(__file__).resolve()]
    return {**source_hashes(), **{str(p.relative_to(ROOT)): sha(p) for p in files}}


def run(source, baseline, output, upstream, data, optional_visits=None):
    original = verify_inputs(source, data, upstream)
    isolation_check(output, data, upstream)
    if output == ROOT or ROOT in output.parents:
        raise ValueError("raw_search_records_must_stay_outside_public_repository")
    old = read(baseline / "manifest.json")
    if (sha(baseline / "manifest.json") != (baseline / "manifest.sha256").read_text().strip()
            or old["source_live_manifest_sha256"] != sha(source / "manifest.json")):
        raise ValueError("baseline_manifest_mismatch")
    completed = read(baseline / "completed.json")["outputs"]
    optional_policy = read(optional_visits) if optional_visits else []
    if len(old["jobs"]) != 5 or len(completed) != 5:
        raise ValueError("expected_all_five_seen_candidates")
    output.mkdir(parents=True, exist_ok=False)
    jobs = []
    for job in old["jobs"]:
        path = baseline / job["input"]
        matching = [v for v in completed if (v["uid"], v["attempt"]) == (job["uid"], job["attempt"])]
        if (len(matching) != 1 or sha(path) != job["input_sha256"]
                or sha(path.with_name("repair.json")) != matching[0]["sha256"]):
            raise ValueError("baseline_record_mismatch")
        task, prior = read(path), read(path.with_name("repair.json"))
        if set(task["query"]) != {"uid", "nature_language"}:
            raise ValueError("public_query_only")
        new_input = output / job["input"]
        configured = [p for p in optional_policy if (p["uid"], p["attempt"]) == (job["uid"], job["attempt"])]
        if len(configured) > 1:
            raise ValueError("duplicate_optional_visit_configuration")
        save(new_input, {**task, "plan": prior["draft"],
                         "optional_visit_paths": configured[0]["paths"] if configured else []})
        jobs.append({**job, "input_sha256": sha(new_input),
                     "baseline_input_sha256": sha(path), "baseline_output_sha256": matching[0]["sha256"]})
    hashes = frozen_sources()
    with zipfile.ZipFile(output / "source-at-freeze.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, expected in hashes.items():
            if sha(ROOT / relative) != expected:
                raise ValueError("source_changed_while_archiving")
            archive.write(ROOT / relative, relative)
    manifest = dict(kind="offline_seen_candidate_schedule_search", frozen_at=datetime.now(timezone.utc).isoformat(),
                    source_manifest_sha256=sha(source / "manifest.json"),
                    baseline_manifest_sha256=sha(baseline / "manifest.json"), jobs=jobs,
                    source_sha256=hashes, source_zip_sha256=sha(output / "source-at-freeze.zip"),
                    upstream_commit=original["upstream_commit"], database_sha256=original["database_sha256"],
                    model_api_calls=0, source_task_count=2, source_candidate_count=5, python=sys.version,
                    optional_visit_policy=optional_policy)
    save(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha(output / "manifest.json") + "\n")

    def run_job(job):
        path = output / job["input"]
        directory = path.parent
        profile = profile_for_worker(sys.executable, upstream, ROOT, directory, path, data / "database", 65431)
        command = [SANDBOX_EXEC, "-p", profile, sys.executable, "-m", "experiments.chinatravel_search.worker",
                   "--input", str(path), "--output", str(directory / "search.json"), "--upstream", str(upstream)]
        with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            result = subprocess.run(command, cwd=upstream, env=clean_environment(directory),
                                    stdout=stdout, stderr=stderr, timeout=150, close_fds=True)
        if result.returncode or not (directory / "search.json").is_file():
            raise RuntimeError(f"search_worker_failed:{job['uid']}:{job['attempt']}")
        return {"uid": job["uid"], "attempt": job["attempt"], "sha256": sha(directory / "search.json")}

    with ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(run_job, jobs))
    save(output / "completed.json", {"outputs": outputs})
    if hashes != frozen_sources():
        raise ValueError("frozen_sources_changed")
    rows = []
    for job, done in zip(jobs, outputs):
        path = output / job["input"]
        if sha(path) != job["input_sha256"] or sha(path.with_name("search.json")) != done["sha256"]:
            raise ValueError("search_records_changed")
        rows.append((job, read(path), read(path.with_name("search.json"))))
    # Every output is frozen before gold is loaded; workers cannot see gold.
    from experiments.chinatravel.official_scoring import score
    cases = []
    for job, task, searched in rows:
        gold = source / "queries/gold" / (job["uid"] + ".json")
        if sha(gold) != original["gold_query_sha256"][job["uid"]]:
            raise ValueError("source_gold_changed")
        scores = {}
        for name, plan in (("baseline", task["plan"]), ("best_draft", searched["best_draft"]),
                           ("delivered", searched["plan"] or {})):
            result = score(read(gold), plan, upstream_root=upstream)
            scores[name] = {key: result[key] for key in ("schema", "env", "logical", "final")}
        cases.append(dict(uid=job["uid"], attempt=job["attempt"], score=scores, status=searched["status"],
                          reason=searched["reason"], generated=searched["generated"], evaluated=searched["evaluated"],
                          expansions=searched["expansions"]))
    tasks = []
    for uid in original["selected_uids"]:
        # Choose by local acceptance in original attempt order, never by gold.
        accepted = next((c for c in cases if c["uid"] == uid and c["status"] == "accepted"), None)
        tasks.append({"uid": uid, "first_local_accept": accepted["attempt"] if accepted else None,
                      "official_pass": bool(accepted and accepted["score"]["delivered"]["final"])})
    report = dict(kind="seen_candidate_development_not_new_model_run", model_api_calls=0,
                  manifest_sha256=sha(output / "manifest.json"), candidate_count=len(cases), task_count=len(tasks),
                  official={name: {key: sum(c["score"][name][key] for c in cases)
                                   for key in ("schema", "env", "logical", "final")}
                            for name in ("baseline", "best_draft", "delivered")},
                  cases=cases, tasks=tasks)
    save(output / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "baseline", "output", "upstream", "data"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--optional-visits", type=Path,
                        help="Explicit, reviewed [day,activity] omissions per uid/attempt; default none")
    args = parser.parse_args()
    run(*(getattr(args, name).resolve() for name in ("source", "baseline", "output", "upstream", "data")),
        optional_visits=args.optional_visits)
