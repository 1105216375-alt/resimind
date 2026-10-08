"""Paired development replay of all 28 published v1 candidate plans.

repair: isolate candidates from gold and repair only from sandbox facts.
score: after all repair jobs finish, evaluate unchanged originals and outputs.
These are seen development cases, not new end-to-end or held-out results.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tarfile

from experiments.chinatravel.recording import save
from experiments.chinatravel.run import (ROOT, clean_environment, read, sha, upstream_check,
                                          isolation_check)
from experiments.chinatravel.sandbox import profile_for_worker, SANDBOX_EXEC


def source_hashes():
    paths = list((ROOT / "src/resimind").rglob("*.py"))
    paths += list((ROOT / "experiments/chinatravel").glob("*.py"))
    paths += list((ROOT / "experiments/chinatravel_repair").glob("*.py"))
    paths += [ROOT / "experiments/__init__.py"]
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(paths)
            if not path.name.startswith("test_")}


def verify_inputs(source, data, upstream):
    frozen = read(source / "manifest.json")
    if sha(source / "manifest.json") != (source / "manifest.sha256").read_text().strip():
        raise ValueError("original_manifest_changed")
    upstream_check(upstream, frozen["upstream_commit"])
    if (upstream / "chinatravel/environment/database").resolve() != (data / "database").resolve():
        raise ValueError("sandbox_link_mismatch")
    for relative, expected in frozen["database_sha256"].items():
        if sha(data / "database" / relative) != expected:
            raise ValueError("sandbox_changed")
    return frozen


def run_repair(source, output, data, upstream):
    frozen = verify_inputs(source, data, upstream)
    isolation_check(output, data, upstream)
    output.mkdir(parents=True, exist_ok=False)
    jobs = []
    archive_index = read(source / "archive-index.json")
    # Archives are the public, immutable source of candidates, even when an
    # ignored local raw-runs directory also happens to exist.
    archive_hashes = {}
    for uid in frozen["selected_uids"]:
        path = source / "archives" / (uid + ".tar.xz")
        expected = archive_index[uid + ".tar.xz"]["sha256"]
        if sha(path) != expected:
            raise ValueError("original_archive_changed")
        archive_hashes[str(path.relative_to(source))] = expected
        query_path = source / "queries/public" / (uid + ".json")
        if sha(query_path) != frozen["public_query_sha256"][uid]:
            raise ValueError("original_query_changed")
        query = read(query_path)
        prefix = f"runs/{uid}/resimind_react/worker/"
        with tarfile.open(path, "r:xz") as archive:
            translation = json.load(archive.extractfile(prefix + "self_translation.json"))
            names = set(archive.getnames())
            for attempt in (1, 2, 3):
                name = prefix + f"candidate_{attempt:02d}.json"
                if name not in names:
                    continue
                candidate = json.load(archive.extractfile(name))
                if not candidate.get("plan"):
                    continue
                job = output / "jobs" / uid / str(attempt)
                save(job / "input.json", dict(uid=uid, attempt=attempt, query=query,
                     plan=candidate["plan"], self_translation=translation))
                jobs.append(dict(uid=uid, attempt=attempt,
                                 input=str((job / "input.json").relative_to(output)),
                                 input_sha256=sha(job / "input.json")))
    if len(jobs) != 28 or len({job["uid"] for job in jobs}) != 12:
        raise ValueError("expected_all_28_candidates_and_12_tasks")
    manifest = dict(kind="posthoc_paired_repair_development", generated_at=datetime.now(timezone.utc).isoformat(),
                    original_manifest_sha256=sha(source / "manifest.json"),
                    archive_sha256=archive_hashes, jobs=jobs, source_sha256=source_hashes(),
                    upstream_commit=frozen["upstream_commit"], database_sha256=frozen["database_sha256"],
                    model_api_calls=0, max_repair_rounds=3, python=sys.version,
                    dependencies=subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True).splitlines())
    save(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(sha(output / "manifest.json") + "\n")

    def run_job(job):
        input_path = output / job["input"]
        directory = input_path.parent.resolve()
        profile = profile_for_worker(sys.executable, upstream, ROOT, directory,
                                     input_path, data / "database", 65431)
        command = [SANDBOX_EXEC, "-p", profile, sys.executable, "-m",
                   "experiments.chinatravel_repair.replay_worker", "--input", str(input_path),
                   "--output", str(directory / "repair.json"), "--upstream", str(upstream)]
        with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
            result = subprocess.run(command, cwd=upstream, env=clean_environment(directory),
                                    stdout=stdout, stderr=stderr, timeout=150)
        if result.returncode or not (directory / "repair.json").is_file():
            raise RuntimeError(f"repair_worker_failed:{job['uid']}:{job['attempt']}")
        return {"uid": job["uid"], "attempt": job["attempt"], "sha256": sha(directory / "repair.json")}

    with ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(run_job, jobs))
    save(output / "completed.json", {"outputs": outputs})
    print(json.dumps({"replayed": len(outputs), "model_api_calls": 0}), flush=True)


def score_repair(source, output, data, upstream):
    frozen = verify_inputs(source, data, upstream)
    manifest = read(output / "manifest.json")
    if sha(output / "manifest.json") != (output / "manifest.sha256").read_text().strip():
        raise ValueError("replay_manifest_changed")
    if manifest["original_manifest_sha256"] != sha(source / "manifest.json"):
        raise ValueError("original_manifest_mismatch")
    if manifest["source_sha256"] != source_hashes():
        raise ValueError("replay_sources_changed")
    completed = read(output / "completed.json")["outputs"]
    if len(completed) != len(manifest["jobs"]):
        raise ValueError("replay_incomplete")
    rows = []
    # Validate every repair output before loading any gold or importing scoring.
    for job in manifest["jobs"]:
        input_path = output / job["input"]
        matching = [item for item in completed if (item["uid"], item["attempt"]) == (job["uid"], job["attempt"])]
        if (len(matching) != 1 or sha(input_path) != job["input_sha256"]
                or sha(input_path.parent / "repair.json") != matching[0]["sha256"]):
            raise ValueError("replay_records_changed")
        rows.append((job, read(input_path), read(input_path.parent / "repair.json")))
    from experiments.chinatravel.official_scoring import score
    results = []
    for job, task, repaired in rows:
        gold_path = source / "queries/gold" / (job["uid"] + ".json")
        if sha(gold_path) != frozen["gold_query_sha256"][job["uid"]]:
            raise ValueError("original_gold_changed")
        gold = read(gold_path)
        values = {}
        for name, plan in (("original", task["plan"]), ("draft", repaired["draft"]),
                           ("delivered", repaired["plan"] or {})):
            evaluated = score(gold, plan, upstream_root=upstream)
            values[name] = {key: evaluated[key] for key in ("schema", "env", "logical", "final")}
        results.append({"uid": job["uid"], "attempt": job["attempt"], "score": values,
                        "status": repaired["status"], "stop_reason": repaired["stop_reason"],
                        "resolved": len(repaired["resolved"]), "regressed": repaired["regressed"],
                        "transactions": len(repaired["transactions"]),
                        "committed_transactions": sum(t["result"]["status"] in {"accepted", "repaired"}
                                                      for t in repaired["transactions"])})
    def summarize(items):
        return {"n": len(items), "improved_local_checks": sum(row["resolved"] > 0 for row in items),
                "regressed_local_checks": sum(bool(row["regressed"]) for row in items),
                "accepted": sum(row["status"] == "accepted" for row in items),
                "invalid_deliveries": sum(row["status"] == "accepted" and not row["score"]["delivered"]["final"] for row in items),
                "official": {name: {key: sum(row["score"][name][key] for row in items)
                                    for key in ("schema", "env", "logical", "final")}
                             for name in ("original", "draft", "delivered")}}
    report = {"kind": "seen_candidate_development_replay_not_held_out", "model_api_calls": 0,
              "manifest_sha256": sha(output / "manifest.json"),
              "all_candidates": summarize(results),
              "shared_first_candidates": summarize([row for row in results if row["attempt"] == 1]),
              "cases": results}
    save(output / "report.json", report)
    print(json.dumps({key: report[key] for key in ("all_candidates", "shared_first_candidates")}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("repair", "score"))
    for arg in ("source", "output", "data", "upstream"):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    function = run_repair if args.command == "repair" else score_repair
    function(*(getattr(args, name).resolve() for name in ("source", "output", "data", "upstream")))


if __name__ == "__main__":
    main()
