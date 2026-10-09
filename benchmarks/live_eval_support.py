"""Local request audit and strict request budgets for live benchmark runners.

One shared recorder can serve several threads. Request text stays in the chosen
local output directory; ``compact_summary`` contains no prompts or responses.
The output budget reserves each request's full advertised output limit, even
after a short or failed response, so unknown usage cannot reopen the budget.
This is a single-process recorder, not a distributed request coordinator.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import threading
import time


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_json(path, value):
    """Atomically replace a JSON file, without exposing partially written data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class RequestFailure(RuntimeError):
    """A sanitized technical failure, never a successful model abstention."""


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")


class RecordedCompletion:
    """Persist attempted Chat Completions before sending, and never retry IDs.

    ``settings`` requires ``model``, ``max_output_tokens``,
    ``max_physical_calls``, ``max_prompt_bytes`` and ``timeout_seconds``.
    ``max_total_output_tokens`` optionally caps reserved output tokens across
    the whole run. The SDK client must support ``with_options``; SDK retries
    are disabled here. Pass ``client=None`` for strictly offline replay.

    The caller freezes the experiment's tasks, sources and settings separately.
    Schema validity is the runner's responsibility: completed transport does
    not imply a valid candidate, a verified answer, or benchmark success.
    """

    def __init__(self, folder, settings, client, system_prompt):
        self.folder = Path(folder).resolve()
        self.settings = json.loads(canonical(settings))
        self.settings.setdefault("temperature", 0.0)
        self.settings.setdefault("thinking", "disabled")
        if type(system_prompt) is not str or not system_prompt.strip():
            raise ValueError("system_prompt must be nonempty text")
        self.system_prompt = system_prompt
        for name in ("max_output_tokens", "max_physical_calls", "max_prompt_bytes"):
            if type(self.settings.get(name)) is not int or self.settings[name] <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.settings.get("model")) is not str or not self.settings["model"].strip():
            raise ValueError("model must be nonempty text")
        timeout = self.settings.get("timeout_seconds")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        total = self.settings.get("max_total_output_tokens")
        if total is not None and (type(total) is not int or total <= 0):
            raise ValueError("max_total_output_tokens must be a positive integer")
        temperature = self.settings["temperature"]
        if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("temperature must be finite and between zero and two")
        if self.settings["thinking"] != "disabled":
            raise ValueError("This bounded pilot requires thinking='disabled'")
        self.client = None
        if client is not None:
            if not callable(getattr(client, "with_options", None)):
                raise ValueError("client must support SDK with_options to disable retries")
            self.client = client.with_options(timeout=timeout, max_retries=0)
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.folder), threading.RLock())

    def path(self, request_id):
        if type(request_id) is not str or not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("request_id must be a safe local filename identifier")
        return self.folder / "requests" / f"{request_id}.json"

    def record(self, request_id):
        with self.lock:
            return json.loads(self.path(request_id).read_text(encoding="utf-8"))

    def records(self):
        with self.lock:
            return [json.loads(path.read_text(encoding="utf-8"))
                    for path in sorted((self.folder / "requests").glob("*.json"))]

    def bind(self, prefix):
        """Return a ``complete(prompt)`` callable with deterministic request IDs."""
        self.path(prefix)
        return _BoundCompletion(self, prefix)

    def __call__(self, request_id, prompt):
        path = self.path(request_id)
        if type(prompt) is not str or not prompt.strip():
            raise ValueError("prompt must be nonempty text")
        request_hash = digest({"system": self.system_prompt, "prompt": prompt,
                               "settings": self.settings})
        with self.lock:
            if path.exists():
                previous = self.record(request_id)
                if previous.get("request_sha256") != request_hash:
                    raise RequestFailure("Cached request differs from the frozen protocol")
                if previous.get("status") != "complete":
                    raise RequestFailure("Prior request failed or has unknown completion; no retry")
                text = previous.get("response_text")
                if type(text) is not str or not text.strip():
                    raise RequestFailure("Archived response text is missing or invalid")
                return text
            if self.client is None:
                raise RequestFailure("Offline replay requested an unrecorded model call")
            prompt_bytes = len(self.system_prompt.encode()) + len(prompt.encode())
            record = dict(
                request_id=request_id, request_sha256=request_hash,
                created_at_utc=datetime.now(timezone.utc).isoformat(), status="in_flight",
                model_requested=self.settings["model"], system_prompt=self.system_prompt,
                user_prompt=prompt, prompt_bytes=prompt_bytes,
                parameters=dict(max_tokens=self.settings["max_output_tokens"], stream=False,
                                timeout=self.settings["timeout_seconds"], max_retries=0,
                                temperature=self.settings["temperature"], thinking=self.settings["thinking"]),
                api_attempted=False, reserved_output_tokens=0, response_text=None,
                finish_reason=None, usage=None, elapsed_seconds=0.0,
            )
            attempted = [r for r in self.records() if r.get("api_attempted", True)]
            # Older/unrecognized records cannot silently free a token reservation.
            reserved = sum(r.get("reserved_output_tokens", self.settings["max_output_tokens"])
                           for r in attempted)
            blocked = None
            if len(attempted) >= self.settings["max_physical_calls"]:
                blocked = "Physical API call ceiling reached"
            elif prompt_bytes > self.settings["max_prompt_bytes"]:
                blocked = "Prompt byte ceiling reached before API call"
            elif (self.settings.get("max_total_output_tokens") is not None
                  and reserved + self.settings["max_output_tokens"] > self.settings["max_total_output_tokens"]):
                blocked = "Reserved output token ceiling reached before API call"
            if blocked:
                record.update(status="blocked", error=blocked)
                write_json(path, record)
                raise RequestFailure(blocked)
            record.update(api_attempted=True, reserved_output_tokens=self.settings["max_output_tokens"])
            # Persist first: a crash after this point leaves an unknown request that
            # will not be automatically submitted or billed a second time.
            write_json(path, record)
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(
                model=self.settings["model"],
                messages=[{"role": "system", "content": self.system_prompt},
                          {"role": "user", "content": prompt}],
                max_tokens=self.settings["max_output_tokens"], stream=False,
                temperature=self.settings["temperature"],
                extra_body={"thinking": {"type": self.settings["thinking"]}},
            )
            for target, source in (("model_returned", "model"), ("response_id", "id")):
                value = getattr(response, source, None)
                record[target] = value if type(value) is str else None
            usage = getattr(response, "usage", None)
            record["usage"] = {}
            for target, source in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"),
                                   ("total_tokens", "total_tokens"), ("cache_hit_tokens", "prompt_cache_hit_tokens"),
                                   ("cache_miss_tokens", "prompt_cache_miss_tokens")):
                value = getattr(usage, source, None)
                record["usage"][target] = value if type(value) is int and value >= 0 else None
            choices = getattr(response, "choices", None)
            if not isinstance(choices, (list, tuple)) or len(choices) != 1:
                raise RequestFailure("Response did not contain exactly one choice")
            choice = choices[0]
            finish = getattr(choice, "finish_reason", None)
            record["finish_reason"] = finish if type(finish) is str else None
            message = getattr(choice, "message", None)
            response_text = getattr(message, "content", None)
            record["response_text"] = response_text if type(response_text) is str else None
            if finish != "stop":
                raise RequestFailure("Incomplete response")
            if (getattr(message, "tool_calls", None) or getattr(message, "function_call", None)
                    or getattr(message, "refusal", None)):
                raise RequestFailure("Unexpected tool call or refusal")
            if type(response_text) is not str or not response_text.strip():
                raise RequestFailure("No completion text")
            record["status"] = "complete"
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc) if type(exc) is RequestFailure else "Provider request failed; raw exception omitted"
            status = getattr(exc, "status_code", None)
            if type(status) is int:
                record["http_status"] = status
        finally:
            record["elapsed_seconds"] = round(time.perf_counter() - started, 6)
            with self.lock:
                write_json(path, record)
        if record["status"] != "complete":
            raise RequestFailure(record["error"])
        return record["response_text"]

    def compact_summary(self, request_ids=None):
        """Public-safe transport metadata; absent usage remains unknown."""
        records = self.records() if request_ids is None else [self.record(i) for i in dict.fromkeys(request_ids)]
        attempted = [r for r in records if r.get("api_attempted", True)]
        usage = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            values = [(r.get("usage") or {}).get(key) for r in attempted]
            usage[key] = sum(values) if all(type(v) is int and v >= 0 for v in values) else None
        return dict(
            physical_calls=len(attempted), complete_requests=sum(r.get("status") == "complete" for r in records),
            failed_requests=sum(r.get("status") == "failed" for r in records),
            blocked_requests=sum(r.get("status") == "blocked" for r in records),
            in_flight_requests=sum(r.get("status") == "in_flight" for r in records),
            reserved_output_tokens=sum(r.get("reserved_output_tokens", self.settings["max_output_tokens"])
                                       for r in attempted),
            model_returned=sorted({r["model_returned"] for r in records if r.get("model_returned")}),
            model_seconds=sum(r.get("elapsed_seconds", 0.0) for r in attempted), usage=usage,
        )


class _BoundCompletion:
    def __init__(self, recorder, prefix):
        self.recorder = recorder
        self.prefix = prefix
        self.request_ids = []
        self._lock = threading.Lock()

    @property
    def calls(self):
        return len(self.request_ids)

    def __call__(self, prompt):
        with self._lock:
            request_id = f"{self.prefix}-{len(self.request_ids) + 1:03}"
            self.request_ids.append(request_id)
        return self.recorder(request_id, prompt)
