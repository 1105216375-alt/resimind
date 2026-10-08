"""Isolated ChinaTravel execution; all model traffic goes to a parent broker.

This module has no provider client, API key, dataset loader, or gold evaluator.
The launcher must enforce filesystem/network and resource isolation. Run one
worker per case and arm; only the parent owns the authoritative budget ledger.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import json
import math
from multiprocessing.connection import Client
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch


ARMS = ("official_react", "official_nesy", "resimind_react")
ENVIRONMENT_CHECKS = (
    "Is_activity_grounded", "Is_intercity_transport_correct",
    "Is_attractions_correct", "Is_hotels_correct", "Is_restaurants_correct",
    "Is_transport_correct", "Is_time_correct", "Is_space_correct",
)


class WorkerStopped(BaseException):
    """Bypass upstream's catch-and-continue handling of ordinary exceptions."""

    def __init__(self, status: str, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


def _json_default(value):
    if isinstance(value, type):
        return value.__module__ + "." + value.__qualname__
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict(orient="records")
        except TypeError:
            return value.to_dict()
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    return {"python_type": type(value).__name__, "text": str(value)}


def _save(path: Path, value) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=_json_default)
        stream.write("\n")


def _append(path: Path, value) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, default=_json_default) + "\n")


class _ToolTrace:
    """Bound audit storage without changing tool results or propagating I/O errors."""

    def __init__(self, output_dir: Path, *, max_trace_bytes=8 * 1024 * 1024,
                 max_records=10_000, max_result_bytes=1024 * 1024,
                 max_blob_bytes=16 * 1024 * 1024):
        self.output_dir = output_dir
        self.max_trace_bytes = max_trace_bytes
        self.max_records = max_records
        self.max_result_bytes = max_result_bytes
        self.max_blob_bytes = max_blob_bytes
        self.total_calls = self.recorded_calls = self.omitted_calls = 0
        self.omitted_results = self.trace_bytes = self.stored_result_bytes = 0
        self.audit_errors = 0
        self.truncated = False
        self._stored_hashes = set()
        self._write_summary(finalized=False)

    def summary(self, *, finalized=False):
        return {
            "total_calls": self.total_calls, "recorded_calls": self.recorded_calls,
            "omitted_calls": self.omitted_calls, "omitted_results": self.omitted_results,
            "truncated": self.truncated, "trace_bytes": self.trace_bytes,
            "stored_result_count": len(self._stored_hashes),
            "stored_result_bytes": self.stored_result_bytes, "audit_errors": self.audit_errors,
            "finalized": finalized,
            "limits": {"trace_bytes": self.max_trace_bytes, "records": self.max_records,
                       "single_result_bytes": self.max_result_bytes,
                       "total_blob_bytes": self.max_blob_bytes},
        }

    def _write_summary(self, *, finalized):
        try:
            _save(self.output_dir / "tool_trace_summary.json", self.summary(finalized=finalized))
        except Exception:
            self.audit_errors += 1

    def close(self):
        self._write_summary(finalized=True)

    def _result_encoding(self, value):
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                   default=_json_default)
        digest = hashlib.sha256()
        size = 0
        buffered = bytearray()
        for text in encoder.iterencode(value):
            chunk = text.encode("utf-8")
            digest.update(chunk)
            size += len(chunk)
            if size <= self.max_result_bytes:
                buffered.extend(chunk)
            else:
                buffered.clear()
        return digest.hexdigest(), size, bytes(buffered) if size <= self.max_result_bytes else None

    def record(self, command, value, elapsed_seconds, *, exception=None):
        self.total_calls += 1
        if self.truncated:
            self.omitted_calls += 1
            return
        if self.recorded_calls >= self.max_records or self.trace_bytes >= self.max_trace_bytes:
            self.truncated = True
            self.omitted_calls += 1
            self._write_summary(finalized=False)
            return
        try:
            record = {"command": command, "elapsed_seconds": elapsed_seconds}
            payload = None
            new_blob = False
            if exception is not None:
                record.update(result_sha256=None, result_omitted=True,
                              exception_class=type(exception).__name__, code="tool_exception")
                if type(getattr(exception, "errno", None)) is int:
                    record["errno"] = exception.errno
            else:
                result_hash, size, payload = self._result_encoding(value)
                new_blob = result_hash not in self._stored_hashes
                omission = None
                if size > self.max_result_bytes:
                    omission = "single_result_limit"
                elif new_blob and self.stored_result_bytes + size > self.max_blob_bytes:
                    omission = "total_blob_limit"
                record.update(result_sha256=result_hash, result_bytes=size,
                              result_omitted=omission is not None)
                if omission:
                    record["omission_reason"] = omission
                    new_blob = False
            line = (json.dumps(record, ensure_ascii=False, separators=(",", ":"),
                               default=_json_default) + "\n").encode("utf-8")
            if self.trace_bytes + len(line) > self.max_trace_bytes:
                self.truncated = True
                self.omitted_calls += 1
                self._write_summary(finalized=False)
                return  # Never write a blob for an omitted trace record.
            if new_blob:
                directory = self.output_dir / "tool_results"
                directory.mkdir(exist_ok=True)
                blob_path = directory / (record["result_sha256"] + ".json")
                blob_path.write_bytes(payload)
                self.stored_result_bytes += len(payload)
                self._stored_hashes.add(record["result_sha256"])
            with (self.output_dir / "tools.jsonl").open("ab") as stream:
                stream.write(line)
            self.trace_bytes += len(line)
            self.recorded_calls += 1
            self.omitted_results += int(record["result_omitted"])
            if self.recorded_calls >= self.max_records or self.trace_bytes >= self.max_trace_bytes:
                self.truncated = True
                self._write_summary(finalized=False)
        except Exception:
            # Audit failures must never turn a successful native tool call into
            # an algorithm failure. Stop further audit writes for this run.
            self.audit_errors += 1
            self.omitted_calls += 1
            self.truncated = True
            self._write_summary(finalized=False)

    def call(self, invoke, environment, command):
        started = time.monotonic()
        try:
            value = invoke(environment, command)
        except Exception as exc:
            self.record(command, None, time.monotonic() - started, exception=exc)
            raise
        self.record(command, value, time.monotonic() - started)
        return value


def _public_query(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        query = json.load(stream)
    if type(query) is not dict or set(query) != {"uid", "nature_language"}:
        raise ValueError("public_query_keys")
    if (type(query["uid"]) is not str or query["uid"] in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", query["uid"])):
        raise ValueError("public_query_uid")
    if type(query["nature_language"]) is not str or not query["nature_language"].strip():
        raise ValueError("public_query_text")
    return query


def _decode_plan(raw):
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            decoded = json.loads(raw)
        except (ValueError, TypeError):
            # Match run_exp's treatment of a non-JSON answer: retain it so the
            # separate evaluator can mark an invalid delivered artifact.
            return {"plan": raw}
    else:
        decoded = raw
    if decoded is None:
        return None
    return decoded if type(decoded) is dict else {"plan": decoded}


def _ipc_llm(connection, settings: dict, output_dir: Path):
    from chinatravel.agent.llms import OpenAICompatibleLLM
    from chinatravel.agent.load_model import normalize_run_name

    class BrokerLLM(OpenAICompatibleLLM):
        def _send_request(self, messages, request_args):
            number = self.broker_requests + 1
            self.broker_requests = number
            request = {"type": "model_request", "messages": messages,
                       "request_args": request_args}
            _append(output_dir / "model_requests.jsonl", {"request": number, **request})
            try:
                connection.send(request)
                response = connection.recv()
            except (EOFError, OSError):
                raise WorkerStopped("technical_failure", "broker_disconnected") from None
            if type(response) is not dict:
                raise WorkerStopped("technical_failure", "broker_protocol")
            if response.get("status") != "complete":
                status = response.get("status")
                budget = status in {"budget_exhausted", "budget_denied", "timeout",
                                    "wall_timeout", "token_limit", "call_limit"}
                raise WorkerStopped("budget_exhausted" if budget else "technical_failure",
                                    "broker_budget_stop" if budget else "broker_request_failed")
            message, usage = response.get("message"), response.get("usage")
            if (type(message) is not dict or message.get("role") != "assistant"
                    or type(message.get("content")) is not str or type(usage) is not dict):
                raise WorkerStopped("technical_failure", "broker_protocol")
            counts = (usage.get("prompt_tokens"), usage.get("completion_tokens"))
            if any(type(count) is not int or count < 0 for count in counts):
                raise WorkerStopped("technical_failure", "broker_usage_missing")
            prompt_tokens, completion_tokens = counts
            self.input_token_count += prompt_tokens
            self.output_token_count += completion_tokens
            self.input_token_maxx = max(self.input_token_maxx, prompt_tokens)
            self.broker_input_tokens += prompt_tokens
            self.broker_output_tokens += completion_tokens
            _append(output_dir / "model_responses.jsonl", {
                "request": number, "message": message, "usage": usage,
            })
            return message

    model = settings["model"]
    if type(model) is not str or not model.strip():
        raise ValueError("model_configuration")
    if type(settings["max_output_tokens"]) is not int or settings["max_output_tokens"] < 1:
        raise ValueError("output_token_configuration")
    # A dummy explicit key prevents the upstream constructor's key-env lookup.
    # The lazy SDK property is never accessed by this adapter.
    llm = BrokerLLM(
        model=model, name=normalize_run_name(model), api_key="IPC_ONLY",
        wire_api="chat", token_limit_arg="max_tokens",
        default_request_args={"temperature": 0, "reasoning_effort": "none"},
        max_tokens=settings["max_output_tokens"],
    )
    llm.broker_requests = 0
    llm.broker_input_tokens = 0
    llm.broker_output_tokens = 0
    return llm


class _ReActProposals:
    """Continue the same official agent without reset, requery, or step credit."""

    def __init__(self, agent, query: dict, output_dir: Path):
        self.agent = agent
        self.query = query
        self.output_dir = output_dir
        self.attempt = 0
        self.original_plan_prompt = agent.plan_prompt

    def __call__(self, feedback: dict | None):
        self.attempt += 1
        if self.attempt == 1:
            result = self.agent(self.query["nature_language"])
            raw = result["ans"]
        else:
            if type(feedback) is not dict:
                raise ValueError("continuation_feedback_missing")
            _save(self.output_dir / f"feedback_{self.attempt:02d}.json", feedback)
            instructions = (
                "\n验收反馈：下面是上一完整计划及本地检查结果。请基于已有笔记和查询结果修正计划，"
                "必要时补充查询，然后使用 plan(...) 提交完整计划。不要把检查结果当作已满足约束。\n"
                + json.dumps(feedback, ensure_ascii=False)
            )
            self.agent.json_scratchpad.append({"role": "user", "content": instructions})
            self.agent._log.append({f"AcceptanceFeedback[{self.attempt}]": feedback})
            # ActAgent.plan starts a fresh final-answer message, so it must also
            # receive the feedback rather than relying only on the scratchpad.
            self.agent.plan_prompt = self.original_plan_prompt + instructions + "\n"
            self.agent.finished = False
            self.agent._ans = ""
            while self.agent.cur_step < self.agent.max_steps and not self.agent.finished:
                self.agent.step()
            raw = self.agent._ans if self.agent.finished else None
        plan = _decode_plan(raw)
        _save(self.output_dir / f"candidate_{self.attempt:02d}.json", {
            "attempt": self.attempt, "react_step": self.agent.cur_step,
            "finished": self.agent.finished, "raw_answer": raw, "plan": plan,
        })
        _save(self.output_dir / "react_log.json", self.agent._log)
        return plan


class _LocalGate:
    """Public schema/world checks plus the model's own translated constraints."""

    def __init__(self, query, llm, upstream_root: Path, output_dir: Path):
        from jsonschema.validators import validator_for

        with (upstream_root / "chinatravel/evaluation/output_schema.json").open(
            encoding="utf-8"
        ) as stream:
            schema = json.load(stream)
        validator = validator_for(schema)
        validator.check_schema(schema)
        self.validator = validator(schema)
        self.query = query
        self.llm = llm
        self.output_dir = output_dir
        self.translation = None
        self.translation_reasons = None
        self.attempt = 0

    def _translate(self):
        from chinatravel.agent.nesy_agent.nl2sl_hybrid import nl2sl_reflect
        from chinatravel.environment.language import city_names
        from chinatravel.symbol_verification.concept_func import func_dict
        from chinatravel.symbol_verification.dsl import validate_dsl_code

        if self.translation_reasons is not None:
            return
        self.translation_reasons = []
        try:
            self.translation = nl2sl_reflect(deepcopy(self.query), self.llm, lang="zh")
            _save(self.output_dir / "self_translation.json", self.translation)
            translated = self.translation
            if type(translated) is not dict:
                self.translation_reasons.append("translation_not_object")
                return
            if any(translated.get(field) not in city_names("zh")
                   for field in ("start_city", "target_city")):
                self.translation_reasons.append("translation_city_invalid")
            if (type(translated.get("days")) is not int or not 1 <= translated["days"] <= 6
                    or type(translated.get("people_number")) is not int
                    or translated["people_number"] < 1):
                self.translation_reasons.append("translation_structure_invalid")
            codes = translated.get("hard_logic_py")
            if (type(codes) is not list or not codes
                    or any(type(code) is not str or not code.strip() for code in codes)):
                self.translation_reasons.append("translation_constraints_empty")
            else:
                for code in codes:
                    validate_dsl_code(code, allowed_names=set(func_dict) | {"plan"})
            if translated.get("ood") or translated.get("error"):
                self.translation_reasons.append("translation_reported_error")
            reflection = translated.get("reflect_info", [])
            if reflection and (reflection[-1].get("run_error_list")
                               or reflection[-1].get("value_error_list")):
                self.translation_reasons.append("translation_unresolved")
        except Exception as exc:
            self.translation_reasons.append("translation_exception")
            _save(self.output_dir / "translation_error.json", {
                "code": "translation_exception", "exception_class": type(exc).__name__,
            })

    def __call__(self, plan):
        from experiments.chinatravel.resimind_adapter import CheckResult
        from chinatravel.symbol_verification import commonsense_constraint as common
        from chinatravel.symbol_verification.hard_constraint import evaluate_constraints_py

        self.attempt += 1
        self._translate()
        reasons = list(self.translation_reasons)
        diagnostics = {"scope": "self_translated_local_contract", "schema": [],
                       "environment": {}, "self_translated_constraints": []}
        if reasons:
            result = CheckResult(False, tuple(reasons), diagnostics, deferred=True)
        else:
            errors = list(self.validator.iter_errors(plan))
            diagnostics["schema"] = [
                {"path": list(error.absolute_path), "validator": error.validator}
                for error in errors
            ]
            if errors:
                reasons.append("schema_failed")
            uncertain = False
            if not errors:
                common._set_tool_lang("zh")
                for name in ENVIRONMENT_CHECKS:
                    try:
                        table, info = getattr(common, name)(
                            deepcopy(self.translation), deepcopy(plan), verbose=False,
                        )
                        counts = {str(key): int(value) for key, value in table.iloc[0].items()}
                        failed = any(value != 0 for value in counts.values())
                        diagnostics["environment"][name] = {
                            "passed": not failed, "counts": counts, "details": info,
                        }
                        if failed:
                            reasons.append(name + "_failed")
                    except Exception as exc:
                        uncertain = True
                        reasons.append(name + "_exception")
                        diagnostics["environment"][name] = {
                            "passed": None, "exception_class": type(exc).__name__,
                        }
                for index, code in enumerate(self.translation["hard_logic_py"]):
                    local_plan = deepcopy(plan)
                    try:
                        passed = evaluate_constraints_py([code], local_plan, verbose=False)[0]
                        mutated = local_plan != plan
                        diagnostics["self_translated_constraints"].append({
                            "index": index, "passed": bool(passed), "mutated_plan": mutated,
                        })
                        if mutated:
                            uncertain = True
                            reasons.append("constraint_mutated_plan")
                        elif not passed:
                            reasons.append(f"self_constraint_{index}_failed")
                    except Exception as exc:
                        uncertain = True
                        reasons.append("constraint_exception")
                        diagnostics["self_translated_constraints"].append({
                            "index": index, "passed": None, "exception_class": type(exc).__name__,
                        })
            result = CheckResult(not reasons, tuple(dict.fromkeys(reasons)), diagnostics,
                                 deferred=uncertain)
        _save(self.output_dir / f"gate_{self.attempt:02d}.json", {
            "accepted": result.accepted, "deferred": result.deferred,
            "reasons": result.reasons, "diagnostics": result.diagnostics,
        })
        return result


def worker(connection, upstream_root: str, public_query_path: str, output_dir: str,
           settings: dict, sandbox_digest: str, arm: str):
    """Execute one case/arm and send one terminal result to the parent."""
    started = time.monotonic()
    upstream = Path(upstream_root).resolve()
    output = Path(output_dir).resolve()
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
            if arm not in ARMS:
                raise ValueError("unsupported_arm")
            if type(settings) is not dict:
                raise ValueError("settings_must_be_object")
            if arm == "official_nesy":
                seconds = settings.get("search_seconds")
                if (type(seconds) not in (int, float) or not math.isfinite(seconds)
                        or seconds <= 0):
                    raise ValueError("search_time_configuration")
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

            method = "LLMNeSy" if arm == "official_nesy" else "ReAct"
            with patch.object(load_model, "init_llm", return_value=llm), \
                    patch.object(WorldEnv, "__call__", recorded_tool):
                runtime = load_model.create_agent_runtime(
                    method, settings["model"], project_root_path=str(output / "native"),
                    lang="zh", oracle_translation=False, preference_search=False, debug=False,
                )
                agent = runtime.agent
                agent.debug = False
                if arm == "official_nesy":
                    agent.TIME_CUT = settings["search_seconds"]
                    success, plan = agent.run(deepcopy(query), load_cache=False,
                                              oralce_translation=False, preference_search=False)
                    result["plan"] = plan if type(plan) is dict else None
                    result["details"]["native_success"] = bool(success)
                    _save(output / "candidate_01.json", {"plan": result["plan"]})
                else:
                    propose = _ReActProposals(agent, query, output)
                    if arm == "official_react":
                        result["plan"] = propose(None)
                    else:
                        from experiments.chinatravel.resimind_adapter import run_resimind

                        gate = _LocalGate(query, llm, upstream, output)
                        adapted = run_resimind(
                            query, sandbox_digest, propose, gate,
                            max_proposals=settings.get("max_proposals", 3),
                        )
                        _save(output / "resimind_trace.json", adapted.to_dict())
                        result["details"]["resimind_status"] = adapted.run_result.status
                        result["details"]["resimind_stop_reason"] = adapted.run_result.stop_reason
                        if adapted.run_result.status in {"error", "interrupted"}:
                            raise WorkerStopped("technical_failure", "resimind_runtime_error")
                        result["plan"] = adapted.accepted_plan
                result["status"] = "completed"
        except WorkerStopped as exc:
            result.update(status=exc.status, plan=None)
            result["details"].update(code=exc.code, exception_class=type(exc).__name__)
        except BaseException as exc:
            result.update(status="technical_failure", plan=None)
            result["details"].update(code="worker_exception", exception_class=type(exc).__name__)
            if type(getattr(exc, "errno", None)) is int:
                result["details"]["errno"] = exc.errno
        finally:
            # NeSy installs its own Logger objects; close them before restoring
            # the wrapper's streams, without including raw exception text.
            for stream, expected in ((sys.stdout, stdout), (sys.stderr, stderr)):
                if stream is not expected:
                    native_file = getattr(stream, "log", None)
                    if native_file is not None:
                        native_file.flush()
                        native_file.close()
            sys.stdout, sys.stderr = stdout, stderr
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
            # Native NeSy plans may contain NumPy scalars. Normalize once so
            # the durable result and the IPC packet have identical JSON types.
            result = json.loads(json.dumps(result, ensure_ascii=False, default=_json_default))
            _save(output / "result.json", result)
            os.chdir(old_cwd)
            sys.path[:] = old_path
    connection.send(result)


def _apply_resource_limits(settings: dict) -> None:
    """Set process limits before IPC; unsupported limits are fatal, not ignored."""
    import resource

    cpu = settings.get("cpu_seconds", 420)
    file_bytes = settings.get("max_file_bytes", 67_108_864)
    if any(type(value) is not int or value < 1 for value in (cpu, file_bytes)):
        raise ValueError("resource_limit_configuration")
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


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
    parser.add_argument("--arm", choices=ARMS, required=True)
    args = parser.parse_args(argv)
    # Only loopback IPC is a supported transport; no provider network exists in
    # this process. The launcher additionally enforces this with the OS sandbox.
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
