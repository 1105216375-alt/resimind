# Same model, four Agent controllers

This evaluates **Agent architecture using an existing DeepSeek model**. ResiMind does not train a foundation model. On 2026-10-09 we froze the tasks, prompts, production source hashes, budgets and scorer before making any model calls. This is one prospective, handcrafted pilot, not an established benchmark or a comparison against international framework implementations.

The [frozen manifest](evidence/algebra-live-v1/manifest.json) and [compact results](evidence/algebra-live-v1/summary.json) are public. Full prompts, responses and intermediate traces remain local. Run the public [runner](../benchmarks/run_algebra_live_eval.py) with your own credentials to reproduce the protocol; model service outputs may change.

The original v1 evaluated source is preserved in Git commit `37bbf13`; the manifest contains exact per-file hashes. Later main-branch improvements do not replace that implementation or its results.

## Result, including costs and failures

All four arms used `deepseek-flash`, temperature 0, thinking disabled, up to eight calls per task, and 4,096 output tokens per call. The provider returned `deepseek-flash` on every request; this is a service identifier, not proof of an immutable model snapshot.

| Controller | Independently correct / 8 | Incorrect answers delivered | Transfer model calls | Input tokens | Output tokens |
|---|---:|---:|---:|---:|---:|
| ReAct-style action/observation loop | 5 | 3 | 8 | 1,703 | 431 |
| Whole-answer verification + retry | 6 | 0 | 22 | 13,901 | 2,588 |
| ResiMind residual controller | 6 | 0 | 22 | 20,205 | 2,748 |
| Same controller + verified knowledge | 7 | 0 | 20 | 21,694 | 2,257 |

The growth configuration also spent **three discovery calls** to admit three identities, followed by three admission-verifier calls on disk reload. Count discovery + transfer together: **23 calls**, compared with 22 for the residual-only arm. The growth arm did not demonstrate lower total cost. All requests completed; no transport or schema failures were excluded.

Three transfer tasks contained actual committed, fingerprinted cross-task rule applications. Merely retrieving a rule or naming it in a rejected proposal does not count. **Those three tasks also succeeded without the library.** The additional successful nested-square task used no accepted library rule: an attempted rule citation was rejected and the model then produced its own checked derivation. The result therefore does not establish that rule application caused the extra success; rule context and different trajectories remain possible explanations.

## What each controller actually does

- **ReAct-style:** the model chooses `check_identity` or `final`, sees real checker observations after tool calls, and controls final submission. In this run it chose immediate final submission on every transfer task, making zero checker calls. This is an available-tool baseline, not a claim to reproduce the strongest ReAct implementation.
- **Verification + retry:** proposes a whole expansion. An exact production checker must accept both identity and expansion before delivery. On rejection it retries the original problem with accumulated checker feedback.
- **Residual:** uses the shipped `LearningAgent` / `Agent` / `ModelProposer` path with an empty knowledge library. Accepted intermediate rewrites become checked state; rejected rewrites cannot modify it. The adapter has one binary expansion obligation, not a structured residual for every unresolved subterm.
- **Residual + knowledge:** the same production path receives only independently admitted discovery identities. The store is saved, reverified on reload and frozen during transfer; there are zero transfer admissions. Every recalled substitution is checked again for applicability and identity.

All controllers can propose a complete expansion in one call. No baseline is forced to use primitive rewrites. All receive the same task, grammar and goal. JSON `null` is normalized by the harness to immediate abstention; malformed/schema responses and technical failures terminate the task and consume the attempted call. A budget exhaustion is a failure to complete, not a correct refusal.

Schemas, state visibility, mandatory checking and stopping on checked completion differ intentionally. Thus this compares controller configurations; it is not a clean causal estimate of residual scheduling alone. Temperature 0 also does not guarantee reproducible hosted-model outputs.

## Tasks, scoring and audit

Two smoke tasks are separate from the reported denominator. Three discovery tasks cover the square, cube and difference-of-squares identities, which are known mathematics, not novel theorem discoveries. Eight fresh input expressions cover rational coefficients, affine and trinomial cubes, nested squares, matched difference products, an unmatched product and a sixth power. Six are deliberately chosen for rule applicability and two are controls. They were frozen before calls, but are not random or unseen by a pretrained model.

The runtime verifier compares exact rational polynomial coefficients. A separate final scorer evaluates the input and delivered output using exact fractions over a **complete degree-bounded Cartesian grid**, with independently computed degree bounds and expansion structure. The full grid gives a bounded polynomial-identity certificate, not merely a few passing samples. Scoring points and answers never enter a model prompt. Unsupported grammar or scorer limits produce `unknown`, not success.

For each task the result records completion, incorrect and otherwise unsuccessful delivery, API/schema failures, model calls, token usage, actual rule applications and checker work. Missing usage stays unknown. Discovery, reload and transfer are charged separately. Prompt and request hashes, physical-call reservations and a frozen library hash are checked; unsuccessful calls cannot silently reopen a budget. Completed studies cannot overwrite their original timings. For resumed studies, controller wall time describes the final execution session; the immutable recorded `model_seconds` is the API-time measure and concurrency prevents adding it up as end-to-end latency.

One run over eight tasks is descriptive evidence only. It does not establish statistical significance, broad mathematical superiority, superiority over LangGraph/AgentScope, or a general reduction in model cost. The latest model options follow the [official DeepSeek API documentation](https://api-docs.deepseek.com/api/create-chat-completion/).

## Failure analysis and next implementation step

The unmatched product was delivered incorrectly by the immediate-final baseline and remained unsolved by all three checked controllers. The residual-only nested-square trajectory accepted an intermediate step, then repeated an identical wrong rewrite seven times. On the unmatched product, each checked controller repeated its own wrong expression eight times. Candidate IDs changed, mathematics did not.

The initial rejection feedback said only that coefficients differed. This motivates a general improvement: expose a bounded, exact coefficient discrepancy from the production checker, while retaining the same acceptance criteria. It does not prove generic feedback caused every failure. Any post-result improvement and rerun must be labelled development on seen cases, with this first result retained.

## v2: development after inspecting the same tasks

We implemented that diagnostic and froze a **separate, seen-case development rerun**. The acceptance test remains exact equality of every coefficient; a rejection now names one mismatching monomial and the two exact coefficients. The model receives one discrepancy, not a generated complete answer. The final independent grid scorer is still isolated. Both the retry baseline and the production Agent receive the richer checker information.

| Controller | v1 correct / 8 | v2 correct / 8 | v2 incorrect deliveries | v2 transfer calls |
|---|---:|---:|---:|---:|
| ReAct-style | 5 | 5 | 3 | 8 |
| Verification + retry | 6 | 7 | 0 | 17 |
| Residual | 6 | 6 | 0 | 22 |
| Residual + verified knowledge | 7 | 7 | 0 | 20 |

The retry baseline corrected the unmatched product in three calls, using two successive coefficient discrepancies. This is a concrete improvement in that recorded trajectory. Residual and growth completion totals did not improve: some proposals ignored the named discrepancy or changed a different monomial. The growth configuration now ties the retry baseline on completion while using more calls, plus its three discovery calls. No general architecture superiority or causal effect is established by this rerun.

The original experiment has 83 physical calls and 72,493 reported tokens; v2 has 78 physical calls and 70,606 tokens, each including its eight smoke and three discovery runs. All 43 task trajectories in each experiment were independently replayed offline against recorded requests, including re-admission and frozen-library checks. No mathematical miss was selectively removed or overwritten.

[v2 frozen manifest](evidence/algebra-feedback-v2/manifest.json) · [v2 compact results](evidence/algebra-feedback-v2/summary.json) · [中文解读](algebra-live-evaluation.zh-CN.md) · [Implementation validation](validation-algebra-live.md)

## Run locally

```bash
python -m pip install -e '.[deepseek]'
# Configure DEEPSEEK_API_KEY locally; never commit it.
PYTHONPATH=src:. python -m benchmarks.run_algebra_live_eval freeze --folder ../local-algebra-study
PYTHONPATH=src:. python -m benchmarks.run_algebra_live_eval run --folder ../local-algebra-study --live
```

Freezing makes no model calls. Running requires explicit `--live`; the recorder has global physical-call, prompt-size and worst-case output-token limits. Keep the study folder outside Git. Source or protocol changes after freeze require a separately labelled study. Add `--development` to `freeze` when running after inspecting the tasks/results, as we did for v2; do not present such a rerun as fresh validation.
