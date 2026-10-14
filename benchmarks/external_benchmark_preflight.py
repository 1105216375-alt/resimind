"""Preflight the fair external-benchmark protocol.

This command deliberately does not call a model, download data, or turn a
missing resource into a score of zero.  It records the exact reason an
external run is ready, blocked, or only partially supported.  It is the
auditable boundary between the pinned MiniF2F/LeanDojo manifest compiler,
the ChinaTravel runner, and local development fixtures; it never claims a
benchmark score by itself.

Examples::

    python -m benchmarks.external_benchmark_preflight \
      --output /tmp/resimind-external-preflight.json

    python -m benchmarks.external_benchmark_preflight --strict
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "deepseek-flash"
MANIFEST_ADAPTER = "benchmarks.external_theorem_adapter"


def _exists(path: Path | None) -> bool:
    return bool(path and path.exists())


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lean_version(binary: str | None) -> str | None:
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True,
            timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr).strip()
    return output or None


def _resource(name: str, path: Path | None, *, required: str) -> dict[str, Any]:
    present = _exists(path)
    return {
        "name": name,
        "path": str(path) if path else None,
        "present": present,
        "required_for": required,
        "status": "available" if present else "missing",
        "sha256": _sha256(path) if present and path and path.is_file() else None,
    }


def _china_resources(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return [_resource("chinatravel_data_dir", None, required="ChinaTravel")]
    query = path / "human.csv"
    if not query.exists():
        query = path / "source" / "query" / "human.csv"
    archive = path / "ChinaTravel_sandbox_zh.zip"
    if not archive.exists():
        archive = path / "source" / "sandbox" / "raw" / "ChinaTravel_sandbox_zh.zip"
    database = path / "database"
    return [
        _resource("chinatravel_human_csv", query, required="ChinaTravel"),
        _resource("chinatravel_sandbox_zip", archive, required="ChinaTravel preparation"),
        _resource("chinatravel_prepared_manifest", path / "dataset_manifest.json", required="ChinaTravel live scoring"),
        _resource("chinatravel_database", database, required="ChinaTravel live scoring"),
    ]


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    lean = shutil.which(args.lean) if args.lean else shutil.which("lean")
    model_key = bool(os.environ.get("DEEPSEEK_API_KEY"))
    model = os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL
    mathlib = Path(args.mathlib).expanduser() if args.mathlib else None
    minif2f = Path(args.minif2f).expanduser() if args.minif2f else None
    leandojo = Path(args.leandojo).expanduser() if args.leandojo else None
    chinatravel = Path(args.chinatravel).expanduser() if args.chinatravel else None

    checks = {
        "model": {
            "status": "available" if model_key else "blocked",
            "provider": "DeepSeek-compatible",
            "model": model,
            "api_key_configured": model_key,
            "secret_value_in_report": False,
        },
        "miniF2F": {
            "status": "ready" if model_key and _exists(minif2f) and _exists(mathlib) and lean else "blocked",
            "adapter": MANIFEST_ADAPTER,
            "data": _resource("miniF2F", minif2f, required="MiniF2F"),
            "mathlib": _resource("mathlib", mathlib, required="MiniF2F"),
            "lean": {"binary": lean, "version": _lean_version(lean)},
            "blocking_reasons": [],
        },
        "LeanDojo": {
            "status": "ready" if model_key and _exists(leandojo) and _exists(mathlib) and lean else "blocked",
            "adapter": MANIFEST_ADAPTER,
            "data": _resource("LeanDojo", leandojo, required="LeanDojo"),
            "mathlib": _resource("mathlib", mathlib, required="LeanDojo"),
            "lean": {"binary": lean, "version": _lean_version(lean)},
            "blocking_reasons": [],
        },
        "ChinaTravel": {
            "status": "ready" if model_key and all(item["present"] for item in _china_resources(chinatravel)) else "blocked",
            "adapter": "available",
            "resources": _china_resources(chinatravel),
            "official_scorer": "available",
            "blocking_reasons": [],
        },
    }

    for name in ("miniF2F", "LeanDojo"):
        check = checks[name]
        reasons = check["blocking_reasons"]
        # The manifest adapter is intentionally narrower than a general
        # theorem prover: data must be pre-pinned and prepared with one proof
        # hole.  Keep this boundary explicit rather than calling it missing.
        if not check["data"]["present"]:
            reasons.append("pinned dataset path/manifest was not supplied")
        if not check["mathlib"]["present"]:
            reasons.append("pinned Mathlib checkout was not supplied or is missing")
        if not check["lean"]["binary"]:
            reasons.append("Lean executable was not found")
        if not model_key:
            reasons.append("DEEPSEEK_API_KEY is not configured")

    ct = checks["ChinaTravel"]
    if not model_key:
        ct["blocking_reasons"].append("DEEPSEEK_API_KEY is not configured")
    for resource in ct["resources"]:
        if not resource["present"]:
            ct["blocking_reasons"].append(f"missing {resource['name']}")

    return {
        "schema": "resimind.external-benchmark-preflight.v1",
        "repository": str(ROOT),
        "python": sys.version.split()[0],
        "model": model,
        "protocol": {
            "same_model": True,
            "temperature": 0,
            "reasoning_effort": "none",
            "test_split_frozen_before_calls": True,
            "memory_policy": "cold and verified-warm reported separately; no test leakage",
            "primary_metrics": ["proof_or_plan_success", "false_accept", "timeout", "calls", "tokens", "wall_seconds"],
            "arms": ["base_same_model", "resimind_cold", "resimind_verified_warm"],
        },
        "checks": checks,
        "interpretation": {
            "missing_is_not_zero": True,
            "architecture_vs_model": "ResiMind is evaluated as a vertical-domain Agent runtime around a frozen base model; it is not a foundation-model leaderboard score.",
            "miniF2F_and_leandojo": "not_run_until_pinned manifests, Mathlib, Lean and model credentials are supplied",
            "chinatravel": "full 154-query run requires the pinned official data, macOS sandbox supervisor, and live model credentials",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--lean", help="Lean executable (default: PATH lookup)")
    parser.add_argument("--mathlib", help="pinned Mathlib checkout")
    parser.add_argument("--minif2f", help="pinned MiniF2F data/checkout")
    parser.add_argument("--leandojo", help="pinned LeanDojo data/checkout")
    parser.add_argument("--chinatravel", help="prepared ChinaTravel data directory")
    parser.add_argument("--strict", action="store_true", help="return 2 when any requested external benchmark is blocked")
    args = parser.parse_args(argv)
    report = build_report(args)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded, end="")
    blocked = any(value.get("status") == "blocked" for value in report["checks"].values())
    return 2 if args.strict and blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
