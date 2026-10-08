# Customer support: check a refund before promising one

A customer paid **¥249 for an item and ¥10 for shipping**. The demo merchant's policy excludes the original shipping charge from this refund. A plausible “refund ¥259” answer is therefore wrong. ResiMind rejects that proposal, keeps the unfinished work visible, and accepts a corrected ¥249 resolution only after checking the required evidence and rules.

This is the same Agent architecture used by the mathematics and bridge examples: collect evidence → propose → verify → commit checked facts → inspect remaining obligations. Here the obligations concern an order, delivery evidence, a merchant policy, and a proposed support resolution.

## Run it in one command

After installing ResiMind, the package includes an offline demo:

```bash
python -m resimind demo --domain customer-support
python -m resimind demo --domain customer-support --scenario missing-delivery
python -m resimind demo --domain customer-support --scenario expired
```

From a source checkout, the dedicated example also supports JSON output:

```bash
python -m examples.customer_support
python -m examples.customer_support --scenario missing-delivery --json
python -m examples.customer_support --scenario expired --json
```

These commands use deterministic **offline fixtures**, without an API key or model request. The deliberately excessive refund makes the verification step visible; it is not a measured model mistake.

| Scenario | What the verifier must resolve | Expected offline outcome |
| --- | --- | --- |
| `refund` | Match the order and evidence; apply the configured merchant policy; exclude shipping from the amount | Reject ¥259, then finish with a verified ¥249 resolution |
| `missing-delivery` | Obtain the delivery evidence required to apply the policy | Keep the missing obligation pending; no completed resolution |
| `expired` | Route a request outside the demo's 14-day merchant window | Produce a human-review resolution, without treating the policy as a legal denial |

**A finished support resolution does not mean money has moved.** The example does not call a payment system, issue a refund, send a message, or promise when funds will arrive. An application can use the checked result in a separate review or execution step.

## Use the Agent in Python

```python
from resimind.domains.customer_support import run_demo, verified_resolution

result = run_demo(scenario="refund")
assert result.run_result.status == "solved" and result.run_result.residual.solved
resolution = verified_resolution(result)
print(resolution)
print(result.to_json())  # Evidence, candidates, decisions, facts, and remaining work.

pending = run_demo(scenario="missing-delivery")
assert not pending.run_result.residual.solved
print(pending.run_result.residual.pending)
```

`verified_resolution(result)` is the output gate: it rechecks the completed resolution and raises `ValueError` for an incomplete or inconsistent result. Check both the run status and residual before calling it. Do not turn a partial fact, a candidate, or generated prose into a promise to the customer. A human-review outcome is a completed routing decision, not an approved payment.

Money uses integer cents. A successful refund recommendation contains `outcome="refund_recommended"`, `refund_cents=24900`, `excluded_shipping_cents=1000`, and `next_action="merchant_refund_review"`. An outside-window resolution contains `outcome="human_review"`, no refund amount, and `next_action="manual_policy_review"`. Both have `payment_executed=false`.

The lower-level `build_agent(case, complete=None)` entry point accepts a completion callback with the same `complete(prompt: str) -> str` interface as the other ResiMind domains. The callback proposes candidates; it does not replace the verifier or mark its own answers as verified.

## Ask DeepSeek for proposals

From a source checkout:

```bash
python -m pip install -e '.[deepseek]'
# Set DEEPSEEK_API_KEY and DEEPSEEK_MODEL in your local environment first.
python -m examples.customer_support --live --max-calls 8 --json
```

Choose a model explicitly with `DEEPSEEK_MODEL` or `--model MODEL_ID`. The live path makes billable API requests and sends the synthetic case and verification feedback to DeepSeek. It does not insert the offline mistake or fall back to an offline answer. Its result can remain unresolved.

During v0.6.0 preparation, one real `deepseek-flash` run completed in **five calls**. Two proposals were rejected for missing order-snapshot references before the model corrected them. The verified recommendation was ¥249, with no refund executed. [Inspect the run, its exact settings, and both rejected proposals →](evidence/customer-support/README.md) This is a single smoke test, not a model success-rate benchmark.

For an embedded integration:

```python
import os
from resimind import Task
from resimind.domains.customer_support import (
    DOMAIN, build_agent, demo_case, verified_resolution,
)
from resimind.integrations.deepseek import DeepSeekCompletion

# DeepSeekCompletion reads DEEPSEEK_API_KEY from the local environment.
with DeepSeekCompletion(
    model=os.environ["DEEPSEEK_MODEL"],
    max_calls=8,
    max_output_tokens=4096,
    timeout=60.0,
) as complete:
    result = build_agent(demo_case("refund"), complete=complete).run(
        Task("support-live", "Resolve this refund request using the case evidence.", DOMAIN)
    )
    print(result.to_json())
    print(complete.calls, complete.usage, complete.last_error)

run = result.run_result
if run.status == "solved" and run.residual.solved:
    print(verified_resolution(result))
else:
    print("More evidence or review is needed:", run.residual.pending)
```

Keep credentials in the local environment. The wrapper reads `DEEPSEEK_API_KEY`; this example does not search another application's configuration. See [live model integration](live-model.md) for transport behavior, call limits, token accounting, and provider errors. Those controls are not a monetary budget or an overall task deadline.

## What is checked, and what must come from your application

The order data, policy, and delivery record are synthetic inputs. The verifier checks the proposed resolution against those supplied inputs, including any prior item refund when computing the remaining eligible amount. A customer message is a request, not proof of payment or delivery, and missing evidence remains missing rather than being filled in by model confidence.

The **14-day window and shipping exclusion are fictional merchant rules for this demo**, not a statement of consumer law. Production systems must provide their own applicable policies, trusted order and delivery sources, and any required review rules. An outside-window case in this example goes to human review.

This adapter provides a concrete support workflow with a completed-result gate. ID comparisons check consistency within the supplied case; they do not authenticate a customer. Connecting real records, deciding authorization, rendering the customer-facing wording, and executing an approved operation belong to the host application. No inbox, payment account, or customer record is connected by this example. The full `AgentResult` audit is for the trusted host application; do not send it directly to a customer.

## 中文速览

客服也能使用同一套 Agent：模型提出方案，规则核验订单、证据和金额，通过后才提交事实；缺少什么证据，就继续显示为待解决事项。

- **正常退款：**商品实付 249 元，原运费 10 元按示例政策不退。离线演示先提议退 259 元，被拒绝，再改成 249 元通过。
- **缺配送证据：**不凭模型猜测补齐，保留待解决事项；未完成时调用 `verified_resolution` 会抛出 `ValueError`。
- **超出示例期限：**生成需要人工复核的处理方案，不替消费者作法律判断。

直接运行 `python -m resimind demo --domain customer-support`。源码目录中加 `--live` 可让 DeepSeek 提出候选；默认演示完全离线。这里核验的是处理方案，**不会实际退款、发消息或承诺到账时间**。示例的 14 天和运费规则是虚构商家政策，接业务时须换成你自己的适用规则与可信数据来源。
