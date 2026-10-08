# Live DeepSeek smoke runs — 2026-10-08

The v0.7.0 [open-ended planning and preference revision](open-planning/README.md) records are documented separately. This page records the v0.5.0 optimization runs. The v0.6.0 [customer-support run](customer-support/README.md) is documented separately and is also covered by the offline replay command below.

These are the three live generation attempts made while preparing v0.5.0, in order. They use only the repository's synthetic three-variable quadratic program. Each JSON contains the actual candidate/verification audit and provider-reported usage. They do not contain API keys, private project data, provider reasoning traces, or full HTTP envelopes. These developer-recorded logs are not a third-party attestation or an accuracy benchmark.

| Attempt | Requested model | Calls | Reported input / output tokens | Result | Audit |
| --- | --- | ---: | ---: | --- | --- |
| Before prompt clarification | `deepseek-chat` | 4 | 5,808 / 633 | Stalled, all 3 obligations remain | [JSON](deepseek-chat-before-prompt-clarification.json) |
| After prompt clarification | `deepseek-chat` | 6 | 10,510 / 800 | `needs_review`, 2 obligations remain | [JSON](deepseek-chat-needs-review.json) |
| Same clarified prompt, different model | `deepseek-flash` | 4 | 7,233 / 10,329 | `verified_report`, no obligations remain | [JSON](deepseek-flash-verified.json) |

The first run submitted malformed certificate strings and skipped prerequisites. The subsequent edit clarified that the claim must contain only certificate JSON, that rejected steps create no facts, and that LDL factors require unit diagonal. It did not supply a solution or change any verifier condition. The second run verified positive definiteness but then produced incorrect slack/objective values and an unsupported action. LangGraph withheld the completed report.

The final run initially omitted original-problem references. The verifier rejected it; the model then submitted accepted positive-definiteness, primal/dual KKT, and global-gap certificates. The resulting minimum is `-73/8` at `(1, 3/4, 5/4)`. LangGraph returned only the four committed facts in its completed report.

All four responses in the final run identified their model as `deepseek-flash`. Response model IDs were not separately recorded for the earlier `deepseek-chat` runs. The first audit predates the LangGraph validation harness and does not contain a branch field. Usage counts are provider reports, not a price estimate; output may include provider reasoning tokens that are not stored in these files.

## Reproduce the live path

From the checkout, install `.[deepseek,langgraph]` and set `DEEPSEEK_API_KEY` locally. The final run used `DeepSeekCompletion("deepseek-flash", timeout=90, max_output_tokens=8192, max_calls=8)` with `build_verification_graph(build_agent(demo_problem(), complete=complete))`, task ID `deepseek-langgraph-validation`, and instruction `Prove the unique global minimum of the configured QP.`. No temperature or thinking-mode override was supplied. The CLI uses the same path and its own task ID:

```bash
python -m examples.langgraph_optimization --live --model deepseek-flash \
  --max-output-tokens 8192 --timeout 90 --max-calls 8 --json
```

This makes billable requests and may produce a different outcome. It does not replay these saved answers. Model availability and behavior can change. Three attempts on one problem cannot establish success rates or compare model quality.

## Recheck the archived certificates offline

```bash
python tools/replay_live_audits.py
```

This feeds the archived candidates to a fresh Agent and compares the full run result: trace, verdicts, committed state, residual, stop reason, and evidence. All three archives reproduce under the release verifier. It checks verification behavior; it cannot independently prove who generated the candidates or reproduce the original network requests.

Recorded files are kept byte-for-byte from the preparation runs. Later credential-routing safeguards for injected clients and CLI report formatting do not change the verification rules or these replay results.
