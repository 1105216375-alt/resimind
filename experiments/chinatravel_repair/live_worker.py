"""Isolated enhanced ReAct worker using a parent model broker, never gold."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import json
from multiprocessing.connection import Client
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

from experiments.chinatravel.upstream_worker import (
    WorkerStopped, _LocalGate, _ToolTrace, _apply_resource_limits,
    _ipc_llm, _json_default, _public_query, _save,
)
from .proposals import RepairingReActProposer, public_task_envelope
from .repair import repair_plan


ARM = "resimind_repair"


def _solve(agent, llm, query, upstream, output, settings):
    """Translate once, then propose and repair; never return unfinished drafts."""
    gate = _LocalGate(query, llm, upstream, output)
    gate._translate()
    translation = gate.translation
    reasons = list(gate.translation_reasons)
    header = public_task_envelope(query).get("explicit_header", {})
    if type(translation) is dict:
        reasons.extend("translation_public_header_mismatch:" + key
                       for key, value in header.items() if translation.get(key) != value)
    if reasons:
        _save(output / "translation_stop.json", {"reasons": reasons})
        return None, {"resimind_status": "unfinished",
                      "resimind_stop_reason": "translation_needs_regeneration",
                      "translation_reasons": reasons, "raw_proposals": 0}
    max_proposals = settings.get("max_proposals", 3)
    if type(max_proposals) is not int or not 1 <= max_proposals <= 3:
        raise ValueError("proposal_budget_configuration")
    schema = json.loads((upstream / "chinatravel/evaluation/output_schema.json").read_text())
    propose = RepairingReActProposer(agent, query, output / "raw_candidates", schema)
    feedback = None
    for attempt in range(1, max_proposals + 1):
        raw_plan = propose(feedback)
        if not raw_plan:
            return None, {"resimind_status": "unfinished", "resimind_stop_reason": "no_candidate",
                          "raw_proposals": attempt - 1}
        repaired = repair_plan(raw_plan, deepcopy(translation), upstream,
                               max_rounds=3, public_query=query)
        _save(output / f"repair_{attempt:02d}.json", repaired)
        final = repaired.get("final_checks")
        if (repaired.get("status") == "accepted" and type(final) is dict and final
                and all(value is True for value in final.values())
                and type(repaired.get("plan")) is dict and repaired["plan"]):
            return repaired["plan"], {
                "resimind_status": "solved", "resimind_stop_reason": "local_contract_passed",
                "raw_proposals": attempt, "acceptance_scope": "self_translated_local_contract_only",
            }
        feedback = {
            "previous_plan": repaired["draft"], "decision": "reject",
            "reasons": [key for key, value in (final or {}).items() if value is not True],
            "diagnostics": repaired.get("diagnostics", {}),
        }
    return None, {"resimind_status": "unfinished", "resimind_stop_reason": "max_proposals",
                  "raw_proposals": max_proposals}


def worker(connection, upstream_root: str, public_query_path: str, output_dir: str,
           settings: dict, sandbox_digest: str, arm: str = ARM):
    """Use the frozen worker's IPC/trace primitives inside the same OS sandbox."""
    started = time.monotonic()
    upstream, output = Path(upstream_root).resolve(), Path(output_dir).resolve()
    query_path = Path(public_query_path).resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {"type": "result", "status": "technical_failure", "plan": None,
              "details": {"arm": arm, "sandbox_digest": sandbox_digest}}
    old_cwd, old_path = Path.cwd(), list(sys.path)
    llm = agent = tool_trace = None
    with (output / "worker_stdout.log").open("w", encoding="utf-8") as stdout, \
            (output / "worker_stderr.log").open("w", encoding="utf-8") as stderr, \
            redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            if arm != ARM or type(settings) is not dict:
                raise ValueError("worker_configuration")
            query = _public_query(query_path)
            os.chdir(upstream)
            sys.path.insert(0, str(upstream))
            from chinatravel.agent import load_model
            from chinatravel.environment.world_env import WorldEnv

            llm = _ipc_llm(connection, settings, output)
            original_call = WorldEnv.__call__
            tool_trace = _ToolTrace(output)

            def recorded_tool(environment, command):
                return tool_trace.call(original_call, environment, command)

            with patch.object(load_model, "init_llm", return_value=llm), \
                    patch.object(WorldEnv, "__call__", recorded_tool):
                runtime = load_model.create_agent_runtime(
                    "ReAct", settings["model"], project_root_path=str(output / "native"),
                    lang="zh", oracle_translation=False, preference_search=False, debug=False,
                )
                agent = runtime.agent
                agent.debug = False
                plan, details = _solve(agent, llm, query, upstream, output, settings)
                result.update(status="completed", plan=plan)
                result["details"].update(details)
        except WorkerStopped as exc:
            result.update(status=exc.status, plan=None)
            result["details"].update(code=exc.code, exception_class=type(exc).__name__)
        except BaseException as exc:
            result.update(status="technical_failure", plan=None)
            result["details"].update(code="worker_exception", exception_class=type(exc).__name__)
            if type(getattr(exc, "errno", None)) is int:
                result["details"]["errno"] = exc.errno
        finally:
            if agent is not None and hasattr(agent, "_log"):
                _save(output / "react_log.json", agent._log)
                _save(output / "react_state.json", {
                    "cur_step": agent.cur_step, "max_steps": agent.max_steps,
                    "finished": agent.finished, "scratchpad": agent.json_scratchpad,
                    "notebook": agent.notebook.read(),
                })
            if llm is not None:
                result["details"]["worker_usage"] = {
                    "requests": llm.broker_requests,
                    "prompt_tokens": llm.broker_input_tokens,
                    "completion_tokens": llm.broker_output_tokens,
                    "authoritative_ledger": "parent_broker",
                }
            if tool_trace is not None:
                tool_trace.close()
                result["details"]["tool_trace"] = tool_trace.summary(finalized=True)
            result["elapsed_seconds"] = time.monotonic() - started
            result = json.loads(json.dumps(result, ensure_ascii=False, default=_json_default))
            _save(output / "result.json", result)
            os.chdir(old_cwd)
            sys.path[:] = old_path
    connection.send(result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--auth-key", required=True)
    parser.add_argument("--upstream-root", required=True)
    parser.add_argument("--public-query-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--settings-json", required=True)
    parser.add_argument("--sandbox-digest", required=True)
    parser.add_argument("--arm", choices=(ARM,), required=True)
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("host_must_be_loopback")
    settings = json.loads(args.settings_json)
    if type(settings) is not dict:
        parser.error("settings_must_be_object")
    try:
        _apply_resource_limits(settings)
    except (ImportError, AttributeError, OSError, ValueError) as exc:
        parser.exit(2, "resource_limits_unavailable:" + type(exc).__name__ + "\n")
    with Client((args.host, args.port), authkey=bytes.fromhex(args.auth_key)) as connection:
        worker(connection, args.upstream_root, args.public_query_path,
               args.output_dir, settings, args.sandbox_digest, args.arm)


if __name__ == "__main__":
    main()
