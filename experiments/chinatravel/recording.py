"""Parent-only, no-retry API ledger with enforceable per-case/arm budgets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import queue
import threading
import time


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


class Recorder:
    def __init__(self, folder, settings, client, deadline):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.settings = settings
        self.client = client
        self.deadline = deadline
        self.calls = self.prompt_bytes = self.prompt_tokens = self.completion_tokens = 0
        self.cache_hit_tokens = self.cache_miss_tokens = 0
        self.unknown_usage_requests = 0
        self.serial = 0
        self.stopped = False

    def totals(self):
        return dict(api_attempts=self.calls, prompt_bytes=self.prompt_bytes,
                    prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens,
                    cache_hit_tokens=self.cache_hit_tokens, cache_miss_tokens=self.cache_miss_tokens,
                    unknown_usage_requests=self.unknown_usage_requests)

    def _request(self, body, allowance):
        # SDK socket timeouts are not total deadlines. Do not let a slow stream
        # consume extra agent time; a late result is discarded without a retry.
        # A dispatched remote request may still be billed after local timeout.
        local_deadline = time.monotonic() + allowance
        results = queue.Queue(maxsize=1)
        def perform():
            try:
                results.put((True, self.client.chat.completions.create(**body, timeout=allowance)))
            except Exception as exc:
                results.put((False, exc))
        threading.Thread(target=perform, daemon=True).start()
        try:
            success, value = results.get(timeout=max(0, local_deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError("total_request_deadline") from None
        if time.monotonic() > local_deadline:
            raise TimeoutError("total_request_deadline")
        if not success:
            raise value
        return value

    def __call__(self, message):
        self.serial += 1
        record = {"request_number": self.serial, "api_attempted": False,
                  "status": "pending", "started_unix": time.time()}
        path = self.folder / f"{self.serial:03}.json"
        settings = self.settings
        args = message.get("request_args", {})
        messages = message.get("messages")
        if (type(args) is not dict or type(messages) is not list
                or set(args) - {"model", "max_tokens", "temperature", "reasoning_effort", "stop", "response_format"}
                or args.get("model", settings["model"]) != settings["model"]
                or args.get("max_tokens", settings["max_output_tokens"]) != settings["max_output_tokens"]
                or args.get("temperature", 0) != 0
                or args.get("reasoning_effort", "none") != "none"):
            record.update(status="technical_failure", code="request_configuration")
        else:
            body = dict(args, model=settings["model"], messages=messages,
                        max_tokens=settings["max_output_tokens"], temperature=0, reasoning_effort="none")
            record.update(request=body, request_sha256=digest(body))
            size = len(canonical(messages).encode())
            reason = None
            if self.stopped:
                reason = "prior_failure"
            elif time.monotonic() >= self.deadline:
                reason = "wall_limit"
            elif self.calls >= settings["max_calls"]:
                reason = "call_limit"
            elif size > settings["max_request_prompt_bytes"]:
                reason = "request_prompt_limit"
            elif self.prompt_bytes + size > settings["max_total_prompt_bytes"]:
                reason = "cumulative_prompt_limit"
            elif self.completion_tokens + settings["max_output_tokens"] > settings["max_total_output_tokens"]:
                reason = "output_reservation_limit"
            if reason:
                record.update(status="budget_exhausted", code=reason)
            else:
                self.calls += 1
                self.prompt_bytes += size
                record.update(status="inflight", api_attempted=True, prompt_bytes=size)
                save(path, record)  # Durable intent before billable dispatch.
                started = time.monotonic()
                usage_known = False
                try:
                    response = self._request(body, min(settings["request_timeout_seconds"],
                                                       max(0.001, self.deadline - time.monotonic())))
                    usage = response.usage.model_dump(exclude_none=True) if response.usage else {}
                    record.update(model_returned=response.model, usage=usage)
                    prompt_tokens, completion_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    if any(type(n) is not int or n < 0 for n in (prompt_tokens, completion_tokens)):
                        raise ValueError("usage_unavailable")
                    usage_known = True
                    self.prompt_tokens += prompt_tokens
                    self.completion_tokens += completion_tokens
                    self.cache_hit_tokens += usage.get("prompt_cache_hit_tokens", 0)
                    self.cache_miss_tokens += usage.get("prompt_cache_miss_tokens", 0)
                    choice = response.choices[0]
                    content = choice.message.content
                    # Deliberately archive no hidden reasoning or auth/error bodies.
                    record.update(finish_reason=choice.finish_reason,
                                  message={"role": "assistant", "content": content})
                    if choice.finish_reason != "stop" or type(content) is not str:
                        record.update(status="technical_failure", code="incomplete_response")
                    elif time.monotonic() > self.deadline:
                        record.update(status="budget_exhausted", code="wall_limit_after_response")
                    else:
                        record["status"] = "complete"
                except Exception as exc:
                    record.update(status="technical_failure", code="provider_request_failed",
                                  exception_class=type(exc).__name__)
                    http_status = getattr(exc, "status_code", None)
                    if type(http_status) is int:
                        record["http_status"] = http_status
                if not usage_known:
                    self.unknown_usage_requests += 1
                record["elapsed_seconds"] = time.monotonic() - started
        if record["status"] != "complete":
            self.stopped = True
        record["totals_after"] = self.totals()
        save(path, record)
        return {key: record[key] for key in ("status", "message", "usage", "code") if key in record}
