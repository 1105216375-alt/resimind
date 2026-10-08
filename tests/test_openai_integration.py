"""No API credentials or network calls: fakes plus the real SDK MockTransport."""

import importlib.util
import json
import os
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from resimind.agent import Task
from resimind.domains.optimization import DOMAIN, build_agent, demo_problem, run_demo
from resimind.integrations.openai import OpenAICompletion, OpenAICompletionError


def response(text="null", **changes):
    fields = dict(status="completed", error=None, output=[], output_text=text,
                  usage=NS(input_tokens=10, output_tokens=5, total_tokens=15))
    fields.update(changes)
    return NS(**fields)


class FakeClient:
    def __init__(self, reply=None, error=None, base_url="https://api.openai.com/v1"):
        self.reply = response() if reply is None else reply
        self.base_url = base_url
        self.error = error
        self.requests = []
        self.options = []
        self.closed = 0
        self.responses = NS(create=self.create)
        self.chat = NS(completions=NS(create=self.create))

    def with_options(self, **options):
        self.options.append(options)
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return self.reply

    def close(self):
        self.closed += 1


class OpenAICompletionTests(unittest.TestCase):
    def test_request_has_explicit_provider_model_storage_timeout_and_retry_policy(self):
        client = FakeClient(response("candidate text"))
        complete = OpenAICompletion("explicit-model", timeout=12, max_output_tokens=800, client=client)
        self.assertEqual(complete("exact prompt"), "candidate text")
        self.assertEqual(client.options, [dict(timeout=12.0, max_retries=0)])
        self.assertEqual(client.requests, [dict(model="explicit-model", input="exact prompt", max_output_tokens=800, store=False)])
        self.assertEqual(complete.calls, 1)
        self.assertEqual(complete.usage, dict(input_tokens=10, output_tokens=5, total_tokens=15, responses_with_usage=1))
        complete.usage["total_tokens"] = 999
        self.assertEqual(complete.usage["total_tokens"], 15)

    def test_missing_key_fails_before_sdk_import_or_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.dict(sys.modules, {"openai": None}):
            with self.assertRaisesRegex(OpenAICompletionError, "OPENAI_API_KEY"):
                OpenAICompletion("explicit-model")

    def test_injected_endpoint_rejects_untrusted_urls_before_configuring_or_requesting(self):
        invalid = ("http://api.openai.com/v1", "https://api.openai.com:444/v1",
                   "https://api.openai.com.attacker.invalid/v1", "https://api.deepseek.com/v1",
                   "https://name:private-value@api.openai.com/v1", "https://name@api.openai.com/v1",
                   "https://api.openai.com/v1?private-value", "https://api.openai.com/v1#fragment",
                   "https://api.openai.com/v1?", "https://api.openai.com/v1#",
                   "https://api.openai.com/v1/other", "https://api.openai.com/", "",
                   "https://api.openai.com:invalid/v1", "https://api.openai.com\n/v1")
        for address in invalid:
            with self.subTest(address=address):
                client = FakeClient(base_url=address)
                with self.assertRaisesRegex(ValueError, "official OpenAI") as caught:
                    OpenAICompletion("test-model", client=client)
                self.assertNotIn("private-value", str(caught.exception))
                self.assertEqual(client.options, [])
                self.assertEqual(client.requests, [])

    def test_valid_injected_endpoint_is_preserved(self):
        for address in ("https://api.openai.com/v1", "https://api.openai.com/v1/", "https://api.openai.com:443/v1/"):
            client = FakeClient(base_url=address)
            OpenAICompletion("test-model", client=client)("prompt")
            self.assertEqual(client.base_url, address)
            self.assertNotIn("base_url", client.options[0])

    def test_sdk_is_optional_and_import_error_is_actionable(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only"}, clear=True), patch.dict(sys.modules, {"openai": None}):
            with self.assertRaisesRegex(OpenAICompletionError, r"\[openai\]"):
                OpenAICompletion("explicit-model")

    def test_owned_client_fixed_endpoint_ignores_environment_redirect_and_closes(self):
        seen = []
        client = FakeClient()
        def factory(**kwargs):
            seen.append(kwargs)
            return client
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "OPENAI_BASE_URL": "https://untrusted.invalid"}, clear=True):
            with patch.dict(sys.modules, {"openai": NS(OpenAI=factory)}):
                with OpenAICompletion("explicit-model") as complete:
                    complete("prompt")
                complete.close()
        self.assertEqual(seen[0]["base_url"], "https://api.openai.com/v1")
        self.assertEqual(seen[0]["api_key"], "test-only")
        self.assertEqual(seen[0]["max_retries"], 0)
        self.assertEqual(client.closed, 1)

    def test_caller_owned_client_is_not_closed(self):
        client = FakeClient()
        with OpenAICompletion("explicit-model", client=client) as complete:
            complete("prompt")
        self.assertEqual(client.closed, 0)
        with self.assertRaisesRegex(OpenAICompletionError, "closed"):
            complete("prompt")
        self.assertEqual(len(client.requests), 1)

    def test_call_budget_prevents_an_additional_api_call(self):
        client = FakeClient()
        complete = OpenAICompletion("explicit-model", max_calls=1, client=client)
        complete("first")
        with self.assertRaisesRegex(OpenAICompletionError, "budget exhausted"):
            complete("second")
        self.assertEqual(complete.calls, 1)
        self.assertEqual(len(client.requests), 1)

    def test_transport_failure_counts_attempt_and_never_leaks_raw_exception(self):
        for error in (RuntimeError("private-provider-response"), TimeoutError("private-provider-response")):
            with self.subTest(error=type(error).__name__):
                complete = OpenAICompletion("explicit-model", max_calls=1, client=FakeClient(error=error))
                with self.assertRaises(OpenAICompletionError) as caught:
                    complete("prompt")
                self.assertNotIn("private-provider-response", str(caught.exception))
                self.assertNotIn("private-provider-response", complete.last_error)
                self.assertEqual(complete.calls, 1)
                self.assertEqual(complete.usage["responses_with_usage"], 0)

    def test_incomplete_failed_refusal_and_empty_text_are_not_candidates(self):
        invalid = [response(status="incomplete"), response(status="failed"), response(status="queued"),
                   response(error=NS(message="private-error")), response(""), response("   "), response(None),
                   response(output=[NS(type="message", status="incomplete", content=[])]),
                   response(output=[NS(type="message", status="completed", content=[NS(type="refusal", refusal="private")])])]
        for reply in invalid:
            with self.subTest(reply=reply):
                complete = OpenAICompletion("explicit-model", client=FakeClient(reply))
                result = build_agent(demo_problem(), complete=complete).run(Task("fail", "Verify QP", DOMAIN))
                self.assertEqual(result.run_result.status, "error")
                self.assertEqual(result.run_result.stop_reason, "proposer_error")
                self.assertEqual(result.run_result.state.facts, ())
                self.assertFalse(result.run_result.residual.solved)
                self.assertEqual(complete.usage["responses_with_usage"], 1)

    def test_missing_or_invalid_usage_is_not_estimated(self):
        for usage in (None, NS(input_tokens=-1, output_tokens=2, total_tokens=1), NS(input_tokens=True, output_tokens=2, total_tokens=3)):
            complete = OpenAICompletion("explicit-model", client=FakeClient(response(usage=usage)))
            complete("prompt")
            self.assertEqual(complete.usage, dict(input_tokens=0, output_tokens=0, total_tokens=0, responses_with_usage=0))

    def test_invalid_configuration_never_calls_provider(self):
        invalid = [(None, {}), ("", {}), (" model", {}), ("model", {"timeout": 0}),
                   ("model", {"timeout": float("inf")}), ("model", {"timeout": True}),
                   ("model", {"max_output_tokens": 15}), ("model", {"max_output_tokens": True}),
                   ("model", {"max_calls": 0}), ("model", {"max_calls": True})]
        for model, kwargs in invalid:
            with self.subTest(model=model, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    OpenAICompletion(model, client=FakeClient(), **kwargs)

    def test_cli_does_not_publish_a_final_minimizer_before_global_proof(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from examples.openai_optimization import main
        candidates = [event.candidate.to_json() for event in run_demo().run_result.trace]
        replies = iter((candidates[0], candidates[2]))  # Convexity and KKT only.
        client = FakeClient()
        client.responses.create = lambda **kwargs: response(next(replies, "null"))
        complete = OpenAICompletion("test-model", client=client)
        output = StringIO()
        with patch("examples.openai_optimization.OpenAICompletion", return_value=complete), redirect_stdout(output):
            code = main(["--model", "test-model"])
        self.assertEqual(code, 1)
        self.assertNotIn("Verified minimizer", output.getvalue())
        self.assertIn("Verified partial fact IDs:", output.getvalue())
        self.assertIn("qp:primal", output.getvalue())
        self.assertIn("Pending obligations:", output.getvalue())
        self.assertIn("stalled", output.getvalue())


def sdk_response(text, **changes):
    payload = dict(id="resp_test", object="response", created_at=1, model="test-model", status="completed",
                   error=None, incomplete_details=None, instructions=None, max_output_tokens=4096,
                   output=[dict(type="message", id="msg_test", role="assistant", status="completed",
                                content=[dict(type="output_text", text=text, annotations=[])])],
                   parallel_tool_calls=False, tool_choice="auto", tools=[], temperature=1, top_p=1,
                   usage=dict(input_tokens=10, output_tokens=5, total_tokens=15,
                              input_tokens_details=dict(cached_tokens=0), output_tokens_details=dict(reasoning_tokens=0)))
    payload.update(changes)
    return payload


@unittest.skipUnless(importlib.util.find_spec("openai") and importlib.util.find_spec("httpx"), "optional OpenAI SDK + httpx not installed")
class OpenAISDKTransportTests(unittest.TestCase):
    def test_real_sdk_roundtrip_correction_uses_actual_verifier_feedback(self):
        import httpx
        from openai import OpenAI

        # Scripted fixture, not a live-model result. Actual verifier rejects the
        # infeasible point; the following HTTP request must carry that feedback.
        candidates = [event.candidate.to_json() for event in run_demo().run_result.trace]
        prompts = []
        def handler(request):
            self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
            payload = json.loads(request.content)
            self.assertFalse(payload["store"])
            prompts.append(json.loads(payload["input"]))
            return httpx.Response(200, json=sdk_response(candidates[len(prompts) - 1]))
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
            client = OpenAI(api_key="test-only", http_client=transport)
            complete = OpenAICompletion("test-model", max_calls=4, client=client)
            result = build_agent(demo_problem(), complete=complete).run(Task("test", "Prove QP minimum", DOMAIN))
        run = result.run_result
        self.assertEqual(run.status, "solved")
        self.assertEqual([event.decision.value for event in run.trace], ["accept", "reject", "accept", "accept"])
        rejected = run.trace[1]
        self.assertEqual(rejected.before, rejected.after)
        self.assertEqual(prompts[2]["last_feedback"]["decision"], "reject")
        self.assertIn("primal_inequality_violation", prompts[2]["last_feedback"]["reasons"])
        self.assertEqual(prompts[2]["last_feedback"]["before_revision"], prompts[2]["last_feedback"]["after_revision"])
        self.assertEqual(complete.calls, 4)
        self.assertEqual(complete.usage["total_tokens"], 60)

    def test_real_sdk_http_errors_do_not_retry_or_commit(self):
        import httpx
        from openai import OpenAI
        for status in (400, 401, 403, 429, 500):
            with self.subTest(status=status):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, json={"error": {"message": "private-provider-error", "type": "test_error"}})
                with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
                    complete = OpenAICompletion("test-model", client=OpenAI(api_key="test-only", http_client=transport))
                    result = build_agent(demo_problem(), complete=complete).run(Task("test", "Prove minimum", DOMAIN))
                self.assertEqual(len(calls), 1)
                self.assertEqual(complete.calls, 1)
                self.assertEqual(result.run_result.state.facts, ())
                self.assertEqual(result.run_result.status, "error")
                self.assertNotIn("private-provider-error", result.to_json())
                self.assertNotIn("private-provider-error", complete.last_error)

    def test_real_sdk_timeout_does_not_retry(self):
        import httpx
        from openai import OpenAI
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("private-network-detail", request=request)
        with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
            complete = OpenAICompletion("test-model", client=OpenAI(api_key="test-only", http_client=transport))
            with self.assertRaisesRegex(OpenAICompletionError, "timed out"):
                complete("prompt")
        self.assertEqual(len(calls), 1)
        self.assertEqual(complete.calls, 1)


if __name__ == "__main__":
    unittest.main()
