# Live open-ended planning — 2026-10-08

Three live `deepseek-flash` planning experiments were made while preparing v0.7.0. All use fictional venues and transport data. The ordinary and rainy-day runs each passed on their **first proposal**; they did not exhibit a model error or correction. The separate preference-change run demonstrates rejection of an older plan after a new requirement, followed by model revision.

| Request | Recorded decisions | New API calls | Checked outcome | Audit |
| --- | --- | ---: | --- | --- |
| Ordinary day out | Accept | 1 | Gallery → noodles → reading room; ¥58; walking 32 min; return 13:05 | [JSON](deepseek-flash-day-out.json) |
| Rain, indoor venues | Accept | 1 | Same venue order, different times and transport; ¥65; walking 8 min; return 12:59 | [JSON](deepseek-flash-rain.json) |
| Coffee becomes mandatory | Reject reused prior plan, then accept newly generated revision | 1 | Gallery → reading room → cafe; ¥73; walking 35 min; return 13:04 | [JSON](deepseek-flash-coffee-required.json) |

All three respect the supplied ¥300 budget, 45-minute walking limit, 18:00 return deadline, minimum three distinct stops, opening windows, minimum durations, and required categories. The rainy rule applies to visited venues; it does not forbid outdoor travel. The cafe is declared to cover both coffee and a meal in this fictional catalog.

## What changed in the preference experiment

The initial brief treated coffee as a soft preference. We changed `required_categories` from `("art", "meal")` to `("art", "meal", "coffee")`. No verifier code or venue data changed. We then submitted the **actual previously generated day-out candidate** as the first proposal in a fresh Agent run.

That older plan contained no coffee stop, so the new check rejected it with `required_categories_missing`, leaving the state empty. Its original acceptance was correct under the previous requirements. The next proposal came from a real model request that received the actual rejection feedback and updated input snapshot; it changed the venue selection and order and passed. Subjective rationale remained outside the committed plan.

The first candidate in this experiment was deliberately reused from [the previous live audit](deepseek-flash-day-out.json). It was **not a new API response or an invented model mistake**. The coffee audit records two proposal evaluations but only one new model call. Both the provenance and call count are explicit in its metadata.

This demonstrates a concrete neuro-symbolic interaction: neural generation selects among possible plans, while independently enforced requirements determine whether the current plan is still acceptable. These three smoke experiments do not establish comparative accuracy, optimality, user satisfaction, or a success rate.

## Request accounting

Every new API response identified its model as `deepseek-flash`. The wrapper used `max_calls=8`, `max_output_tokens=8192`, `timeout=90`, and no temperature or thinking-mode override. Reported usage:

| Run | Input tokens | Output tokens | Total |
| --- | ---: | ---: | ---: |
| Ordinary | 4,486 | 5,087 | 9,573 |
| Rain | 4,489 | 3,725 | 8,214 |
| Coffee revision | 4,777 | 2,974 | 7,751 |

Output usage can include provider reasoning tokens that are not stored in these files. The audits retain proposals, decisions, facts, and evidence, not credentials, reasoning traces, or full HTTP envelopes. They are developer-generated records, not third-party attestations.

## Reproduce

For ordinary and rainy-day requests, install `.[deepseek]` from the checkout, set `DEEPSEEK_API_KEY` locally, and run:

```bash
python -m examples.open_planning --live --model deepseek-flash --scenario day-out --json
python -m examples.open_planning --live --model deepseek-flash --scenario rain --json
```

To reproduce the preference-change mechanism using the archived prior candidate:

```python
import json
from dataclasses import replace
from pathlib import Path
from resimind import Task
from resimind.domains.planning import DOMAIN, build_agent, demo_problem, verified_plan
from resimind.integrations.deepseek import DeepSeekCompletion

prior = json.loads(Path("docs/evidence/open-planning/deepseek-flash-day-out.json").read_text())
old_candidate = prior["agent"]["run_result"]["trace"][-1]["candidate"]
first = iter([old_candidate])
problem = replace(demo_problem(), required_categories=("art", "meal", "coffee"))

with DeepSeekCompletion("deepseek-flash", max_calls=8, max_output_tokens=8192, timeout=90) as model:
    def propose(prompt):
        reused = next(first, None)
        return json.dumps(reused) if reused is not None else model(prompt)

    result = build_agent(problem, complete=propose).run(Task(
        "planning-coffee-revision",
        "Coffee is now mandatory, in addition to art and a meal. Reconsider the previous day plan against the new explicit requirements.",
        DOMAIN,
    ))
    print(result.to_json())
    print("New API calls:", model.calls)

if result.run_result.status == "solved" and result.run_result.residual.solved:
    print(verified_plan(result))
```

These commands make billable calls and can have different outcomes. To recheck all archived candidates entirely offline:

```bash
python tools/replay_live_audits.py
```

The full recorded traces, states, residuals, and evidence reproduce under the release verifier. Replay checks those decisions; it cannot independently attest to the origin of model output.
