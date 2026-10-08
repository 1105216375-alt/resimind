# Connect a real model to the verifier

Use DeepSeek to propose the certificates for the [constrained optimization example](constrained-optimization.md). ResiMind independently checks each certificate before committing any facts. You can keep the same Agent and verifier when changing the completion provider.

The live path does not insert a deliberate mistake, choose a precomputed answer, or fall back to the offline solver. A model may solve the task, be rejected, stall, produce invalid JSON, or fail at the provider. The CLI reports the result that actually happened.

## Run with DeepSeek

From a checkout of this repository:

```bash
python -m pip install -e '.[deepseek]'
```

Set `DEEPSEEK_API_KEY` in your local environment and set `DEEPSEEK_MODEL` to a model ID available to your account. Keep the key out of code, commits, and shared transcripts. The example does not load `.env` files or read credentials from another application.

```bash
python -m examples.deepseek_optimization --max-calls 8
# Or specify the model explicitly:
python -m examples.deepseek_optimization --model YOUR_DEEPSEEK_MODEL_ID --json
```

The `deepseek` extra installs the OpenAI Python SDK because DeepSeek provides an OpenAI-compatible Chat Completions API. **Requests go to `https://api.deepseek.com`, using only `DEEPSEEK_API_KEY`.** The wrapper does not reuse an OpenAI key or accept an endpoint from `OPENAI_BASE_URL`.

The model ID is explicit rather than silently substituting a newer model. Check your account and [DeepSeek's model listing API](https://api-docs.deepseek.com/api/list-models/) for availability. The adapter sends `model`, `messages`, `max_tokens`, and `stream=False`, following [DeepSeek's Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/). It accepts a single text message with `finish_reason="stop"`; incomplete output, refusals, and tool calls stop the run.

## Python integration

```python
import os
from resimind.agent import Task
from resimind.domains.optimization import DOMAIN, build_agent, demo_problem
from resimind.integrations.deepseek import DeepSeekCompletion

with DeepSeekCompletion(
    model=os.environ["DEEPSEEK_MODEL"],
    timeout=60.0,
    max_output_tokens=4096,
    max_calls=8,
) as complete:
    agent = build_agent(demo_problem(), complete=complete)
    result = agent.run(Task("qp-live", "Prove the unique global minimum.", DOMAIN))
    print(result.to_json())  # Includes accepted and rejected proposals, facts, and residuals.
    print(complete.calls, complete.usage, complete.last_error)

if result.run_result.status != "solved":
    raise SystemExit(1)
```

The existing `ModelProposer` prepares each request from the current verified state, outstanding obligations, registered evidence, and the most recent verifier feedback. A rejection leaves the state unchanged. The next request includes that rejection and its reason; this does not guarantee that the model will correct it.

The optimization domain still checks exact rational LDL factors, primal and dual KKT conditions, and the global gap identity. The model cannot label a fact as verified. The wrapper changes the source of proposals; it does not replace those checks. You can also supply it to another domain's `build_agent(..., complete=complete)` or your own `ModelProposer`.

For a concrete workflow integration, see the [LangGraph node example](langgraph.md).

## Optional OpenAI Responses path

Install `python -m pip install -e '.[openai]'`, then set `OPENAI_API_KEY` and `OPENAI_MODEL` locally:

```bash
python -m examples.openai_optimization --max-calls 8
python -m examples.openai_optimization --model YOUR_OPENAI_MODEL_ID --json
```

`OpenAICompletion` has the same constructor and callable interface as `DeepSeekCompletion`. It uses the official `https://api.openai.com/v1` endpoint and reads `response.output_text` from a completed [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create) result. The request sets `store=False` and `max_output_tokens`; the latter includes reasoning tokens. `store=False` is the response-storage setting, not a blanket promise about all provider data handling. See the [official Python SDK documentation](https://developers.openai.com/api/reference/python) for its transport behavior.

No OpenAI API key was available for this release's preparation, so the OpenAI path was checked with the real SDK and simulated HTTP responses, without a live OpenAI request. Provider validation and any recorded DeepSeek live runs are reported in [validation](validation.md); simulated transport tests are not evidence of model accuracy or success rate.

## What the limits and output mean

| Setting or output | Meaning |
|---|---|
| `--max-calls` / `max_calls` | Maximum attempted API requests for this completion instance. Failures count. No SDK retries. |
| `--max-output-tokens` / `max_output_tokens` | Per-response generation limit; sent as DeepSeek `max_tokens` or OpenAI `max_output_tokens`. |
| `--timeout` / `timeout` | Positive finite SDK network timeout. It is not a deadline for the entire Agent run. |
| `usage` | Reported input, output, and total token counts plus the number of responses that supplied valid usage. Missing usage is not estimated. |
| `last_error` | A sanitized explanation for a provider or budget failure; raw provider errors are not copied to the audit. |
| Exit `0` | The domain's residual is solved. |
| Exit `1` | The run is unsolved, including provider failure, invalid candidate format, or exhausted progress/call budget. |
| Exit `2` | Missing model/key/SDK or invalid configuration. |

These controls are **not a monetary budget** or an input-token limit. There is no automatic retry, alternate provider, or offline answer after failure. The Agent's existing step and no-progress limits may stop the run before the API call budget is exhausted.

`--json` writes the actual audit with provider name, configured model, API call count, reported token usage, provider error if any, and the full `AgentResult`. It does not print credentials. Task evidence and candidate text are part of the audit, so treat it as task data when you save or share it.

Both wrappers support `with` and `.close()`. They close clients they create. An explicitly injected SDK `client=` must already be configured for the matching provider, with that provider's credentials. The wrapper validates its official HTTPS origin and API path before use, preserves its URL, and overrides only timeout and retry policy. OpenAI accepts `https://api.openai.com/v1/`; DeepSeek accepts `https://api.deepseek.com/` or its `/v1/` path (trailing slashes are optional). URLs containing user information, query strings, fragments, other hosts, or nonstandard ports are rejected. An OpenAI-default client cannot be converted to a DeepSeek client by passing it to `DeepSeekCompletion`, or vice versa. The caller owns its trusted transport and closes it. Use a fresh completion instance for each synchronous run.

## 中文速览

先运行 `python -m pip install -e '.[deepseek]'`，在本机设置 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_MODEL`，再运行 `python -m examples.deepseek_optimization --json`。模型负责提出候选，独立验证器决定能否提交事实；失败不会自动换成离线求解结果。模型不一定出错，也不保证一定能修正错误；终端和 JSON 展示的都是这一次的实际结果。调用次数与输出长度有限额，token 统计不等于费用上限。
