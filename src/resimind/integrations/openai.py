"""Optional OpenAI Responses completion, with bounded calls and no fallback.

The SDK is imported only when constructing a client. Completion text remains
an untrusted proposal: ModelProposer parses it and the domain verifier decides
whether any claim can become a fact. This wrapper does not execute model tools.
"""

from __future__ import annotations

import math
import os
from typing import Any
from urllib.parse import urlsplit


class OpenAICompletionError(RuntimeError):
    """A sanitized setup, transport, response, or call-budget failure."""


class OpenAICompletion:
    """A synchronous ``complete(prompt) -> str`` using the Responses API.

    Each instance has a lifetime call budget, counting attempted API calls even
    when they fail. SDK retries are disabled. ``timeout`` is the SDK's network
    timeout, not a hard wall-clock deadline for the complete Agent run. Usage
    totals are observed statistics, not a spending or input-token limit.

    Credentials come only from OPENAI_API_KEY when we construct a client. The
    official API URL is explicit, so OPENAI_BASE_URL cannot silently redirect
    credentials. An injected SDK client must already use this provider's
    official HTTPS endpoint. We validate and preserve that URL, overriding
    only timeout and retries using ``with_options``. The caller owns its
    credentials and trusted HTTP transport, which we never close. Use a
    separate instance for each synchronous task run.
    """

    _provider = "OpenAI"
    _key_env = "OPENAI_API_KEY"
    _base_url = "https://api.openai.com/v1"
    _allowed_paths = ("/v1", "/v1/")
    _error_type = OpenAICompletionError
    _extra = "openai"

    def __init__(
        self,
        model: str,
        *,
        timeout: float = 60.0,
        max_output_tokens: int = 4096,
        max_calls: int = 8,
        client: Any = None,
    ) -> None:
        if type(model) is not str or not model or model != model.strip():
            raise ValueError("model must be a nonempty string without surrounding whitespace")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        if type(max_output_tokens) is not int or max_output_tokens < 16:
            raise ValueError("max_output_tokens must be an integer of at least 16")
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        self.model = model
        self.timeout = float(timeout)
        self.max_output_tokens = max_output_tokens
        self.max_calls = max_calls
        self._calls = 0
        self._usage = dict(input_tokens=0, output_tokens=0, total_tokens=0, responses_with_usage=0)
        self._last_error: str | None = None
        self._closed = False
        self._owns_client = client is None

        if client is None:
            key = os.environ.get(self._key_env)
            if not key or not key.strip():
                self._fail(f"Set {self._key_env} locally before running the live example.")
            try:
                from openai import OpenAI
            except ImportError:
                self._fail(f"Install the optional SDK with: python -m pip install '.[{self._extra}]'")
            try:
                client = OpenAI(api_key=key, base_url=self._base_url, timeout=self.timeout, max_retries=0)
            except Exception:
                self._fail(f"Could not initialize the {self._provider} client; check local SDK configuration.")
        else:
            self._validate_client_endpoint(client)
            if not callable(getattr(client, "with_options", None)):
                raise ValueError("client must provide the OpenAI SDK with_options interface")
            try:
                client = client.with_options(timeout=self.timeout, max_retries=0)
            except Exception:
                self._fail(f"Could not configure the injected {self._provider} client.")
        if not self._has_api(client):
            if self._owns_client:
                client.close()
            raise ValueError(f"client must provide the {self._provider} completion API")
        self._client = client

    def _validate_client_endpoint(self, client: Any) -> None:
        """Never retarget a caller-owned credential to a different provider."""
        address = str(getattr(client, "base_url", ""))
        try:
            parsed = urlsplit(address)
            valid = (
                address == address.strip()
                and not any(character.isspace() for character in address)
                and "?" not in address and "#" not in address
                and parsed.scheme == "https"
                and parsed.hostname == urlsplit(self._base_url).hostname
                and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None
                and parsed.path in self._allowed_paths
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                f"Injected client must already use the official {self._provider} HTTPS API endpoint; "
                "construct a separate client with that provider's credentials."
            )

    @property
    def calls(self) -> int:
        """Number of attempted API calls, including transport failures."""
        return self._calls

    @property
    def usage(self) -> dict[str, int]:
        """Copy of reported token totals; missing usage is not estimated."""
        return dict(self._usage)

    @property
    def last_error(self) -> str | None:
        """A safe explanation, never the raw provider exception or response."""
        return self._last_error

    def _fail(self, message: str) -> None:
        self._last_error = message
        raise self._error_type(message) from None

    def __call__(self, prompt: str) -> str:
        if type(prompt) is not str or not prompt.strip():
            raise ValueError("prompt must be a nonempty string")
        if self._closed:
            self._fail(f"The {self._provider} completion is closed.")
        if self._calls >= self.max_calls:
            self._fail(f"{self._provider} call budget exhausted; unresolved work remains unverified.")
        self._calls += 1
        self._last_error = None
        try:
            response = self._request(prompt)
        except Exception as exc:
            if isinstance(exc, TimeoutError) or type(exc).__name__ == "APITimeoutError":
                self._fail(f"{self._provider} request timed out; no replacement answer was generated.")
            status = getattr(exc, "status_code", None)
            if type(status) is int and status in (401, 403):
                self._fail(f"{self._provider} access was denied; check the API key and model permissions.")
            if status == 429:
                self._fail(f"{self._provider} rate or quota limit reached; no automatic retry was made.")
            self._fail(f"{self._provider} request failed; check connectivity, model access, and request settings.")

        counts = self._usage_counts(response)
        if all(type(value) is int and value >= 0 for value in counts.values()):
            for name, value in counts.items():
                self._usage[name] += value
            self._usage["responses_with_usage"] += 1

        return self._extract_text(response)

    @staticmethod
    def _has_api(client: Any) -> bool:
        return callable(getattr(getattr(client, "responses", None), "create", None))

    def _request(self, prompt: str) -> Any:
        return self._client.responses.create(model=self.model, input=prompt,
                                             max_output_tokens=self.max_output_tokens, store=False)

    @staticmethod
    def _usage_counts(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        return {name: getattr(usage, name, None) for name in ("input_tokens", "output_tokens", "total_tokens")}

    def _extract_text(self, response: Any) -> str:
        if getattr(response, "status", None) != "completed" or getattr(response, "error", None) is not None:
            self._fail("OpenAI returned an incomplete or unsuccessful response; its text was not used.")
        for item in getattr(response, "output", ()) or ():
            if getattr(item, "type", None) == "message" and getattr(item, "status", "completed") != "completed":
                self._fail("OpenAI returned an incomplete message; its text was not used.")
            for part in getattr(item, "content", ()) or ():
                if getattr(part, "type", None) == "refusal":
                    self._fail("OpenAI refused this request; no replacement answer was generated.")
        text = getattr(response, "output_text", None)
        if type(text) is not str or not text.strip():
            self._fail("OpenAI returned no text; unresolved work remains unverified.")
        return text

    def close(self) -> None:
        """Close only a client created by this wrapper; idempotent."""
        if not self._closed:
            self._closed = True
            if self._owns_client:
                self._client.close()

    def __enter__(self) -> OpenAICompletion:
        if self._closed:
            self._fail(f"The {self._provider} completion is closed.")
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
