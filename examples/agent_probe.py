"""Public, offline integration probe. Run from a checkout after installing core.

python -m examples.agent_probe --json
python -m examples.agent_probe --requests | python -m resimind api

Expectations are public. This checks tool integration, not model performance.
There are no network calls, persistent writes or model callbacks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from resimind.tool_api import ToolSession


def run(cases):
    session = ToolSession()
    results = []
    for case in cases:
        request, expected = case["request"], case["expected"]
        before = session.agent.fingerprint()
        result = session.call(request["tool"], request["arguments"])
        checks = {"status": result["status"] == expected["status"],
                  "zero_model_calls": result["model_calls"] == 0}
        for field in ("route", "search_actions"):
            if field in expected:
                checks[field] = result.get(field) == expected[field]
        if "new_rule_count" in expected:
            ids = result.get("learning", {}).get("new_rule_ids")
            checks["rule_admission"] = isinstance(ids, (list, tuple)) and len(ids) == expected["new_rule_count"]
        if "rule_count" in expected:
            checks["rule_count"] = len(result.get("rules", [])) == expected["rule_count"]
        if expected.get("certificate_required"):
            certificate = result.get("certificate") or {}
            checks["source_bound_certificate"] = (
                certificate.get("source") == request["arguments"]["expression"]
                and certificate.get("variables") == request["arguments"]["variables"]
                and bool(certificate.get("terms")))
        if request["tool"] != "learn_polynomial":
            checks["memory_unchanged"] = before == session.agent.fingerprint()
        results.append({"id": case["id"], "passed": all(checks.values()),
                        "checks": checks, "result": result})
    return {"scope": "public integration smoke test; not an external Agent benchmark",
            "passed": all(row["passed"] for row in results),
            "passed_cases": sum(row["passed"] for row in results),
            "total_cases": len(results), "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="Print full checks and tool evidence")
    output.add_argument("--requests", action="store_true", help="Print JSON-lines requests without executing them")
    args = parser.parse_args()
    cases = json.loads(Path(__file__).with_name("agent_probe_cases.json").read_text())["cases"]
    if args.requests:
        for case in cases:
            print(json.dumps(case["request"]))
        return 0
    report = run(cases)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for row in report["results"]:
            print(f'{"PASS" if row["passed"] else "FAIL"} {row["id"]}: {row["result"]["status"]}')
        print(f'{report["passed_cases"]}/{report["total_cases"]} public integration cases; zero model calls.')
        print(report["scope"])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
