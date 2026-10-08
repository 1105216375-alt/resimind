#!/usr/bin/env python3
"""Post-run audit utility, outside the frozen generation source hash set.

Uses only the standard library. Reads existing formal records and writes
``<root>/audit.json``; never imports a provider SDK, calls an API, reads gold
content, executes a generator, or recomputes scores. Exit 0 means the complete
36-run record set passed, 1 means an inconsistency, and 2 means incomplete.
This is an integrity/accounting audit, not a replay of time-dependent NeSy.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys


ARMS = ("official_react", "official_nesy", "resimind_react")
TOTAL_KEYS = ("api_attempts", "prompt_bytes", "prompt_tokens", "completion_tokens",
              "cache_hit_tokens", "cache_miss_tokens", "unknown_usage_requests")
TERMINAL_STATUSES = {"completed", "technical_failure", "budget_exhausted"}
REQUEST_STATUSES = {"complete", "technical_failure", "budget_exhausted"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def nonnegative_integer(value):
    return type(value) is int and value >= 0


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


class Audit:
    def __init__(self, root: Path, cleanup_slack_seconds: float):
        self.root = root
        self.slack = cleanup_slack_seconds
        self.issues = []
        self.incomplete = False
        self.record_hashes = {}
        self.runs = []
        self.settings = {}
        self.frozen_unix = None
        self.manifest_hash = None

    def issue(self, code, path, *, incomplete=False, **details):
        self.issues.append({"code": code, "path": str(path), **details})
        self.incomplete |= incomplete

    def read(self, relative, *, incomplete_if_missing=True):
        path = self.root / relative
        try:
            raw = path.read_bytes()
            self.record_hashes[str(relative)] = hashlib.sha256(raw).hexdigest()
            return json.loads(raw)
        except FileNotFoundError:
            self.issue("missing_file", relative, incomplete=incomplete_if_missing)
        except (OSError, ValueError) as exc:
            self.issue("unreadable_json", relative, incomplete=True,
                       exception_class=type(exc).__name__)
        return None

    def compare_totals(self, actual, expected, path, code):
        if type(actual) is not dict:
            self.issue(code, path, reason="missing_or_invalid_ledger")
            return
        for key in TOTAL_KEYS:
            if not nonnegative_integer(actual.get(key)) or actual[key] != expected[key]:
                self.issue(code, path, field=key, expected=expected[key], actual=actual.get(key))

    def request(self, relative, expected_number, totals, stopped):
        record = self.read(relative)
        if type(record) is not dict:
            self.issue("invalid_request_record", relative, incomplete=True)
            return True, None
        if type(record.get("request_number")) is not int or record["request_number"] != expected_number:
            self.issue("request_number_mismatch", relative, expected=expected_number)
        status = record.get("status")
        if status in {"pending", "inflight"}:
            self.issue("unterminated_request", relative, incomplete=True, status=status)
        elif status not in REQUEST_STATUSES:
            self.issue("invalid_request_status", relative, status=status)
        attempted = record.get("api_attempted")
        if type(attempted) is not bool:
            self.issue("invalid_attempt_flag", relative)
            attempted = False
        started = record.get("started_unix")
        if not finite_number(started):
            self.issue("invalid_request_time", relative)
        elif self.frozen_unix is not None and started < self.frozen_unix:
            self.issue("request_before_freeze", relative, started_unix=started)

        body = record.get("request")
        prompt_bytes = None
        if body is not None:
            if type(body) is not dict:
                self.issue("invalid_request_body", relative)
            else:
                if record.get("request_sha256") != digest(body):
                    self.issue("request_digest_mismatch", relative)
                if type(body.get("messages")) is not list:
                    self.issue("invalid_request_messages", relative)
                else:
                    prompt_bytes = len(canonical(body["messages"]).encode("utf-8"))
                for field, expected in (
                    ("model", self.settings["model"]),
                    ("max_tokens", self.settings["max_output_tokens"]),
                    ("temperature", self.settings["temperature"]),
                    ("reasoning_effort", self.settings["reasoning_effort"]),
                ):
                    if body.get(field) != expected:
                        self.issue("request_setting_mismatch", relative, field=field)
        elif attempted or status == "complete":
            self.issue("attempt_missing_request_body", relative)

        usage = record.get("usage")
        known = (type(usage) is dict
                 and nonnegative_integer(usage.get("prompt_tokens"))
                 and nonnegative_integer(usage.get("completion_tokens")))
        if attempted:
            if stopped:
                self.issue("api_dispatch_after_stop", relative)
            if totals["api_attempts"] >= self.settings["max_calls"]:
                self.issue("call_limit_exceeded_before_dispatch", relative)
            if (totals["completion_tokens"] + self.settings["max_output_tokens"]
                    > self.settings["max_total_output_tokens"]):
                self.issue("output_reservation_limit_violated", relative)
            totals["api_attempts"] += 1
            declared_bytes = record.get("prompt_bytes")
            if not nonnegative_integer(declared_bytes) or declared_bytes != prompt_bytes:
                self.issue("request_prompt_bytes_mismatch", relative,
                           expected=prompt_bytes, actual=declared_bytes)
            if prompt_bytes is not None:
                totals["prompt_bytes"] += prompt_bytes
                if prompt_bytes > self.settings["max_request_prompt_bytes"]:
                    self.issue("single_prompt_limit_exceeded", relative)
            if known:
                totals["prompt_tokens"] += usage["prompt_tokens"]
                totals["completion_tokens"] += usage["completion_tokens"]
                if usage["completion_tokens"] > self.settings["max_output_tokens"]:
                    self.issue("single_output_limit_exceeded", relative)
                for field, target in (("prompt_cache_hit_tokens", "cache_hit_tokens"),
                                      ("prompt_cache_miss_tokens", "cache_miss_tokens")):
                    value = usage.get(field, 0)
                    if not nonnegative_integer(value):
                        self.issue("invalid_cache_usage", relative, field=field)
                    else:
                        totals[target] += value
            else:
                totals["unknown_usage_requests"] += 1
            elapsed = record.get("elapsed_seconds")
            if status in REQUEST_STATUSES and (not finite_number(elapsed) or elapsed < 0):
                self.issue("invalid_request_elapsed_seconds", relative)
        elif known:
            self.issue("usage_without_api_attempt", relative)

        if status == "complete":
            message = record.get("message")
            if (not attempted or not known or record.get("finish_reason") != "stop"
                    or type(message) is not dict or message.get("role") != "assistant"
                    or type(message.get("content")) is not str):
                self.issue("invalid_complete_response", relative)
        if status in REQUEST_STATUSES:
            self.compare_totals(record.get("totals_after"), totals, relative,
                                "request_totals_after_mismatch")
        return stopped or status != "complete", started if finite_number(started) else None

    def run(self, uid, arm):
        relative = Path("runs") / uid / arm
        directory = self.root / relative
        before = len(self.issues)
        result = self.read(relative / "result.json")
        totals = dict.fromkeys(TOTAL_KEYS, 0)
        request_dir = directory / "requests"
        numbered = []
        if not request_dir.is_dir():
            self.issue("missing_request_directory", relative / "requests", incomplete=True)
        else:
            for path in sorted(request_dir.iterdir()):
                if path.name.endswith(".tmp"):
                    self.issue("unfinished_record_write", path.relative_to(self.root), incomplete=True)
                    continue
                if not path.is_file() or not re.fullmatch(r"\d+\.json", path.name):
                    self.issue("unexpected_request_artifact", path.relative_to(self.root))
                    continue
                numbered.append((int(path.stem), path.relative_to(self.root)))
        numbered.sort()
        if [number for number, _ in numbered] != list(range(1, len(numbered) + 1)):
            self.issue("request_sequence_not_contiguous", relative / "requests")
        stopped, timestamps = False, []
        for expected, (_, path) in enumerate(numbered, start=1):
            stopped, timestamp = self.request(path, expected, totals, stopped)
            if timestamp is not None:
                timestamps.append(timestamp)

        status = elapsed = None
        delivered = False
        if type(result) is dict:
            status, elapsed = result.get("status"), result.get("elapsed_seconds")
            if result.get("uid") != uid or result.get("arm") != arm or result.get("type") != "result":
                self.issue("terminal_schedule_binding_mismatch", relative / "result.json")
            if status not in TERMINAL_STATUSES:
                self.issue("invalid_terminal_status", relative / "result.json", status=status)
            plan = result.get("plan")
            if "plan" not in result or (plan is not None and type(plan) is not dict):
                self.issue("invalid_terminal_plan_type", relative / "result.json")
            delivered = plan is not None
            if delivered and status != "completed":
                self.issue("plan_delivered_after_failure_or_budget_stop", relative / "result.json")
            if delivered and stopped:
                self.issue("plan_delivered_after_model_stop", relative / "result.json")
            if not finite_number(elapsed) or elapsed < 0:
                self.issue("invalid_run_elapsed_seconds", relative / "result.json")
            elif elapsed > self.settings["wall_seconds"] + self.slack:
                self.issue("run_wall_limit_exceeded", relative / "result.json",
                           elapsed_seconds=elapsed, cleanup_slack_seconds=self.slack)
            self.compare_totals(result.get("ledger"), totals, relative / "result.json",
                                "terminal_ledger_mismatch")
        elif result is not None:
            self.issue("invalid_terminal_record", relative / "result.json")
        for field, cap_field in (("api_attempts", "max_calls"),
                                  ("prompt_bytes", "max_total_prompt_bytes"),
                                  ("completion_tokens", "max_total_output_tokens")):
            if totals[field] > self.settings[cap_field]:
                self.issue("run_budget_limit_exceeded", relative, field=field,
                           actual=totals[field], cap=self.settings[cap_field])
        worst_output = (totals["completion_tokens"]
                        + totals["unknown_usage_requests"] * self.settings["max_output_tokens"])
        if worst_output > self.settings["max_total_output_tokens"]:
            self.issue("unknown_output_reservation_exceeds_budget", relative)
        self.runs.append({
            "uid": uid, "arm": arm, "terminal_present": type(result) is dict,
            "status": status, "plan_is_not_none": delivered,
            "request_records": len(numbered), "reconstructed_ledger": totals,
            "first_request_started_unix": timestamps[0] if timestamps else None,
            "elapsed_seconds": elapsed, "known_plus_reserved_output_tokens": worst_output,
            "issue_count": len(self.issues) - before,
        })

    def audit(self):
        manifest = self.read(Path("manifest.json"))
        if type(manifest) is not dict:
            self.issue("manifest_unavailable", "manifest.json", incomplete=True)
            return self.report()
        self.manifest_hash = self.record_hashes.get("manifest.json")
        try:
            expected_hash = (self.root / "manifest.sha256").read_text().strip()
            if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) or expected_hash.lower() != self.manifest_hash:
                self.issue("manifest_digest_mismatch", "manifest.sha256")
        except OSError as exc:
            self.issue("manifest_digest_unavailable", "manifest.sha256", incomplete=True,
                       exception_class=type(exc).__name__)
        if manifest.get("phase") != "formal":
            self.issue("not_a_formal_run_manifest", "manifest.json")
        try:
            frozen = datetime.fromisoformat(manifest["frozen_at"].replace("Z", "+00:00"))
            if frozen.tzinfo is None:
                raise ValueError("timezone_required")
            self.frozen_unix = frozen.timestamp()
        except (KeyError, AttributeError, TypeError, ValueError):
            self.issue("invalid_freeze_time", "manifest.json")
        settings = manifest.get("settings")
        numeric = ("max_calls", "max_output_tokens", "max_request_prompt_bytes",
                   "max_total_prompt_bytes", "max_total_output_tokens", "wall_seconds")
        if (type(settings) is not dict
                or any(not finite_number(settings.get(key)) or settings[key] <= 0 for key in numeric)
                or not all(key in settings for key in ("model", "temperature", "reasoning_effort"))):
            self.issue("invalid_manifest_budget_settings", "manifest.json")
            return self.report()
        self.settings = settings
        selected, schedule = manifest.get("selected_uids"), manifest.get("schedule")
        if (type(selected) is not list or len(selected) != 12 or len(set(map(str, selected))) != 12
                or any(type(uid) is not str or uid in {".", ".."}
                       or not re.fullmatch(r"[A-Za-z0-9_.-]+", uid) for uid in selected)):
            self.issue("formal_cohort_must_have_12_unique_safe_uids", "manifest.json")
            return self.report()
        if manifest.get("arms") != list(ARMS):
            self.issue("formal_arm_set_mismatch", "manifest.json")
        if type(schedule) is not list:
            self.issue("invalid_schedule", "manifest.json")
            return self.report()
        expected = {(uid, arm) for uid in selected for arm in ARMS}
        bound = []
        for item in schedule:
            if (type(item) is not dict or type(item.get("uid")) is not str
                    or type(item.get("arm")) is not str
                    or (item["uid"], item["arm"]) not in expected):
                self.issue("invalid_schedule_item", "manifest.json")
            else:
                bound.append((item["uid"], item["arm"]))
        if len(schedule) != 36 or len(bound) != 36 or set(bound) != expected:
            self.issue("schedule_is_not_exact_12_by_3", "manifest.json")
        # Audit every intended arm/case even if the recorded schedule is broken.
        ordered = list(dict.fromkeys(bound))
        ordered += sorted(expected - set(ordered))
        for uid, arm in ordered:
            self.run(uid, arm)
        for path in (self.root / "runs").glob("*/*"):
            if path.is_dir() and (path.parent.name, path.name) not in expected:
                self.issue("unscheduled_run_directory", path.relative_to(self.root))
        return self.report()

    def report(self):
        totals = {key: sum(row["reconstructed_ledger"][key] for row in self.runs)
                  for key in TOTAL_KEYS}
        present = sum(row["terminal_present"] for row in self.runs)
        complete = len(self.runs) == present == 36 and not self.incomplete
        status = "incomplete" if not complete else "failed" if self.issues else "passed"
        return {
            "utility": "post-run audit utility; outside frozen source_hashes",
            "audit_version": 1, "audited_at": datetime.now(timezone.utc).isoformat(),
            "root": str(self.root), "status": status,
            "complete_36_run_record_set": complete,
            "expected_runs": 36, "audited_runs": len(self.runs), "terminal_results_present": present,
            "terminal_status_counts": dict(Counter(row["status"] for row in self.runs if row["status"])),
            "request_record_count": sum(row["request_records"] for row in self.runs),
            "totals": totals, "usage_is_fully_known": complete and totals["unknown_usage_requests"] == 0,
            "cleanup_slack_seconds": self.slack, "manifest_sha256": self.manifest_hash,
            "frozen_at_unix": self.frozen_unix, "runs": self.runs, "issues": self.issues,
            "record_hashes": self.record_hashes,
            "record_set_sha256": digest(self.record_hashes),
            "limitations": [
                "Audits saved integrity and accounting only; does not replay generation or rescore plans.",
                "NeSy search depends on elapsed wall-clock/model time and cannot be exactly replayed from these records.",
                "Requests with unknown usage may still be billed; known token totals are not a zero-cost claim for those requests.",
                "The preregistration Git commit and its publication time are not verified by this offline utility.",
                "Frozen source hashes, OS isolation and provider behavior are not re-executed or independently certified here.",
            ],
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Existing formal artifact directory")
    parser.add_argument("--cleanup-slack-seconds", type=float, default=5.0,
                        help="Supervisor cleanup allowance beyond the frozen wall budget (default: 5)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.cleanup_slack_seconds) or args.cleanup_slack_seconds < 0:
        parser.error("cleanup slack must be finite and nonnegative")
    root = args.root.resolve()
    if not root.is_dir():
        print(json.dumps({"status": "incomplete", "code": "artifact_root_missing"}))
        return 2
    report = Audit(root, args.cleanup_slack_seconds).audit()
    temporary = root / "audit.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(root / "audit.json")
    print(json.dumps({key: report[key] for key in (
        "status", "terminal_results_present", "expected_runs", "request_record_count", "totals",
    )}, ensure_ascii=False))
    print("audit.json: " + str(root / "audit.json"))
    return {"passed": 0, "failed": 1, "incomplete": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
