# Open-ended planning: choose freely, check the constraints

“Plan a day out with art and a meal. Stay within my budget, leave enough time at each stop, and return to the starting station on time.”

There is no single correct itinerary. The Agent can choose different venues, orders, start times, and transport options from a fictional catalog. ResiMind checks whether a proposed plan satisfies the supplied constraints before committing it as a fact. A convincing description alone cannot make a late arrival or an invented journey valid.

This is a bounded example of **open-ended planning**: several plans can work, while the constraints are explicit enough to check independently. It does not prove which trip is most enjoyable or discover every fact about the outside world.

![Actual offline planning trace: an excessive walking total is rejected, then corrected transport yields a checked itinerary](assets/planning-demo.svg)

## Try the three scenarios

After installing ResiMind:

```bash
python -m resimind demo --domain planning
python -m resimind demo --domain planning --scenario rain
python -m resimind demo --domain planning --scenario missing-travel
```

From a source checkout, use the dedicated example for the complete JSON audit:

```bash
python -m examples.open_planning --scenario day-out --json
python -m examples.open_planning --scenario rain --json
python -m examples.open_planning --scenario missing-travel --json
```

These commands use deterministic **offline fixtures**, with no model requests. Deliberately flawed candidates make the verifier's behavior visible; they are not measured LLM mistakes. The `missing-travel` scenario is intentionally unfinished and exits with a nonzero status.

| Scenario | Choices and constraints | What the example demonstrates |
| --- | --- | --- |
| `day-out` | Choose art and meal stops, their timing, and transport under the declared budget and return time | Reject an all-walking plan at 50 minutes against a 45-minute limit; changed transport reduces walking to 22 minutes |
| `rain` | Make a plan using indoor stops while preserving the other requirements | Reject a garden visit, then accept an itinerary with indoor venues |
| `missing-travel` | The supplied return-route records lack the measurements needed to check the journey | Missing time and cost stay unresolved; the Agent cannot assume a free, instantaneous return |

The catalog, opening times, prices, weather flag, and transport records are all **fictional supplied data**. Running this example makes no bookings and does not fetch live travel information.

The supplied request allows **09:00–18:00**, a **¥300** budget, and **45 minutes** of walking, including access walks for transit. Costs are for **one person**, counting each venue charge and route fare once. It requires at least **three distinct stops**, with art and a meal represented. In `rain`, every visited venue must be indoors; the declared rule still allows walking and transit between venues.

Two independently accepted plans illustrate the choice space:

| Feasible choice | Visits | Cost | Walking | Return to station |
| --- | --- | --- | --- | --- |
| Gallery, noodles, reading room | 10:00–11:00; 11:30–12:15; 12:30–13:30 | ¥61 | 22 min | 13:38 |
| Cafe, pottery workshop, reading room | 10:00–10:45; 11:00–12:30; 12:50–13:35 | ¥95 | 8 min | 13:43 |

The first is the corrected offline demo, using transit for its initial and return legs and walking between stops. The second uses transit for all four legs. Both satisfy the same configured request; there is no rule that prefers one of these experiences.

## Where the neural and symbolic parts meet

With a live completion callback, the model selects an itinerary and can explain why it likes that combination. The symbolic verifier independently recalculates the checkable parts from the supplied snapshot:

| Model's proposal | Independent check |
| --- | --- |
| Venue choices and their order | Every venue exists in the catalog; required art and meal categories are covered |
| Start and end times | Opening windows, minimum visit duration, and non-overlapping travel and visits |
| Transport between stops and back home | Each route matches the adjacent locations; its supplied duration fits the schedule |
| Cost and walking totals | Recompute from the venue and transport records; compare with the declared limits |
| A rainy-day itinerary | Check the indoor requirement against the venue records |
| “This will be a delightful, relaxing day” | Subjective rationale stays outside the verified facts |

The loop is **propose → verify → return specific feedback → revise → commit**. Rejected or deferred candidates cannot add a plan fact. Remaining obligations stay visible until a whole itinerary passes. This gives the model room to choose while keeping the conditions for acceptance outside the model.

That is the advantage demonstrated here: an auditable boundary between a plausible suggestion and a checked feasible plan, plus actionable feedback for revision. The example does not establish a success-rate advantage over another Agent or claim that an accepted plan is optimal.

## Use ResiMind directly

No workflow framework is needed:

```python
from resimind import Task
from resimind.domains.planning import (
    DOMAIN, build_agent, demo_problem, run_demo, verified_plan,
)

result = build_agent(demo_problem("day-out")).run(
    Task("day-out", "Plan a day with art and a meal under the supplied constraints.", DOMAIN)
)
run = result.run_result
if run.status == "solved" and run.residual.solved:
    print(verified_plan(result))
else:
    print("Still unresolved:", run.residual.pending)

pending = run_demo("missing-travel")
assert not pending.run_result.residual.solved
print(pending.run_result.residual.pending)
```

`verified_plan(result)` is the completed-output gate. It raises `ValueError` for an unfinished or inconsistent result. Check both the run status and residual before calling it. Render the checked plan returned by this helper; a candidate's free-form rationale is not a verified conclusion.

Without a callback, `build_agent` uses the deterministic demonstration proposer. Injecting `complete(prompt: str) -> str` changes the proposal source while preserving the same independent verifier and fact-commit rules.

## Let DeepSeek choose the plan

In the recorded v0.7.0 experiments, ordinary and rainy-day proposals each passed on the first call. A separate revision changed coffee from a preference to a requirement: the earlier valid plan was rejected under the new constraint, and the model selected a different venue/order combination after receiving that feedback. [Inspect all three live experiments and the reused-candidate provenance →](evidence/open-planning/README.md)

From a source checkout:

```bash
python -m pip install -e '.[deepseek]'
# Set DEEPSEEK_API_KEY and DEEPSEEK_MODEL in your local environment first.
python -m examples.open_planning --live --scenario day-out --json
python -m examples.open_planning --live --scenario rain --json
```

Choose a model with `DEEPSEEK_MODEL` or `--model MODEL_ID`. Live mode sends the synthetic planning snapshot and verification feedback to DeepSeek and makes billable API calls. It does not insert the offline demonstration's mistakes, or replace an unresolved live result with a fixture.

For an embedded integration:

```python
import os
from resimind import Task
from resimind.domains.planning import DOMAIN, build_agent, demo_problem, verified_plan
from resimind.integrations.deepseek import DeepSeekCompletion

with DeepSeekCompletion(
    model=os.environ["DEEPSEEK_MODEL"],
    max_calls=8,
    max_output_tokens=8192,
    timeout=90.0,
) as complete:
    result = build_agent(demo_problem("rain"), complete=complete).run(
        Task("rainy-day", "Choose an indoor day out with art and a meal.", DOMAIN)
    )
    print(result.to_json())
    print(complete.calls, complete.usage, complete.last_error)

run = result.run_result
if run.status == "solved" and run.residual.solved:
    print(verified_plan(result))
else:
    print("More evidence or another proposal is needed:", run.residual.pending)
```

The callback reads `DEEPSEEK_API_KEY` from the local environment. See [live model integration](live-model.md) for request limits, output-token accounting, and sanitized provider errors. A limit on calls or output tokens is not a monetary budget or a deadline for the entire task.

## Proposal format and trust boundary

A model candidate uses action `propose_itinerary`, target `trip:verified_plan`, and a `claim` containing a JSON-encoded object with these fields:

| Field | Meaning |
| --- | --- |
| `stops` | Ordered objects with `place_id`, `start_minute`, and `end_minute` |
| `legs` | Ordered objects with `route_id` and `depart_minute`; one leg before each stop plus a final return leg |
| `total_cost_cents` | Claimed total in integer CNY cents, checked against the snapshot |
| `total_walking_minutes` | Claimed walking total, independently recomputed |
| `rationale` | Optional subjective explanation, excluded from the checked plan |

Times are integer minutes since local midnight. Evidence references identify the supplied request, place catalog, and transport snapshot: `trip:request`, `trip:places`, and `trip:transport`. A reference identifies the input being checked; it does not authenticate a real-world provider or prove that a price is current.

The verifier checks a proposed whole plan rather than comparing it to a stored “right answer.” It accepts multiple plans if each satisfies the constraints, and it does not search for the best plan. Missing transport measurements cause deferral rather than being replaced with zero. A real application must supply its own trusted catalog and travel records, refresh changing data, and express any additional hard requirements as checks.

The task's prose guides proposal generation. Only requirements represented in the configured problem and enforced by the verifier become checked constraints. Preferences such as “beautiful,” “fun,” or “not too rushed” need an explicit measurable rule before the verifier can certify anything about them.

## 中文速览

开放性的地方在于：**可以有很多条成立的行程，模型自己选择去哪、什么顺序、几点去、怎么走**。符号验证负责把关预算、开放时间、停留时长、交通衔接、步行上限、艺术与用餐要求，以及雨天的室内条件。

模型觉得“这样搭配很有意思”，可以作为解释；但这不是被证明的事实。真正通过验证的，是“这份行程符合所提供数据和约束”。不把选择空间写死，也不把好听的描述当成验收结果。

直接运行 `python -m resimind demo --domain planning`。加 `--scenario rain` 看约束变化，加 `--scenario missing-travel` 看证据不足如何保留待办。默认完全离线；在源码目录运行 `python -m examples.open_planning --live` 才会让 DeepSeek 提出候选。

这个示例使用虚构景点和交通数据，没有预订操作，不声称找到了“最好玩”的行程。它展示的是同一套 ResiMind Agent 如何让开放式生成接受独立核验，无需 LangGraph。
