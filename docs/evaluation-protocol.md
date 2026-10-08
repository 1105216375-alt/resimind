# Frozen protocol: paired open-planning pilot

This pilot asks whether ResiMind's independent checks and feedback change the validity of delivered plans, compared with direct generation and model self-review. It measures a bounded planning task using fictional data. It is not a general Agent benchmark or evidence of state-of-the-art performance.

The protocol, case data, independent oracle, and implementation are frozen **before any evaluation model requests**. All outcomes, including malformed output, refusals, timeouts, and unresolved tasks, remain in the results. Tests of the evaluation machinery use fixtures; fixture results are never reported as model performance.

## Cases and independent ground truth

There are exactly **12 manually constructed cases**: eight with independently established feasible plans, two with fully observed constraints that are infeasible, and two for which missing measurements prevent certification of a plan. Each case receives one shared initial model sample. There are no repeated seeds or selected best runs.

The independent oracle checks venue identity, distinct stops, category coverage, indoor requirements, opening and visit windows, route endpoints, departure and arrival times, return deadline, costs, and walking totals directly from the supplied snapshot. It does not call the production verifier. It establishes each case's expected status before requests begin, with a feasible witness where applicable. Missing evidence is distinguished from a proof of infeasibility; a case with a fully observed valid alternative cannot be labeled evidence-insufficient merely because some other route is unknown.

Ground-truth labels, witnesses, oracle diagnostics, and case descriptions used for reporting are withheld from model generation. Only the problem's supplied evidence and a common task instruction are available. The oracle is never used to select a candidate, stop an arm early, or generate repair feedback. Production ResiMind verifier feedback is available only to the ResiMind arm.

## Three paired arms

All arms receive the same initial response, common system instruction, candidate schema, supplied evidence, and task. A response is either one structured candidate or JSON `null`. The same `ModelProposer` format parser applies to every arm; this shared parsing checks the candidate format, not semantic validity.

| Arm | Proposal policy | Maximum logical model calls per case | Delivered output |
| --- | --- | --- | --- |
| Direct generation | Use the shared first response | 1 | That response, if it is a well-formed candidate |
| Model self-review | Review the previous response against the evidence twice; no external checker result | 3, including the shared first response | The last response, with no oracle-based selection of an earlier answer |
| ResiMind | Check the shared first response, then allow up to two revisions guided by production verifier feedback | 3, including the shared first response | Only a whole plan committed by the Agent and returned through its completed-output gate |

Self-review performs both reviews even if the earlier answer would pass the oracle. It stops early only for `null` or a technical failure. ResiMind uses a maximum of three proposal steps and three no-progress steps; it can stop when its production verifier accepts, a technical failure occurs, or its step limit is reached. The shared initial response consumes its first proposal step. A `null` response ends further API requests for the affected arm; any remaining ResiMind runtime steps can reuse the empty proposal without another request. An initial `null` therefore ends API requests for all three arms. No arm can recover a discarded earlier answer by consulting the scoring oracle.

At most three cases run concurrently. Within a case, self-review and ResiMind execution order alternates by case index. This reduces a simple ordering bias but does not eliminate provider variation or contention. The direct arm is a one-call reference, while self-review and ResiMind share a **three-call ceiling**. They do not have equal actual token consumption or elapsed time. Self-review's fixed review policy and ResiMind's acceptance-based stopping can produce different costs even when the shared initial answer is valid. Lower observed ResiMind cost alone therefore cannot establish an intrinsic cost advantage.

## Fixed request settings and failure handling

- Requested model: `deepseek-flash`, using DeepSeek's official API. Archive the model identifier returned by each response; an alias alone does not guarantee an immutable underlying model version.
- Output limit: 8,192 tokens per request. Network timeout: 90 seconds per request. SDK retries: zero. Other model settings use provider defaults, consistently across arms; these defaults are not a deterministic seed.
- Prompt ceiling: 40,000 UTF-8 bytes for the combined system and user strings, checked before the request. This excludes transport wrappers and is not a token count.
- Global ceiling: 60 physical API attempts across the 12 cases: at most 12 shared initial requests and 24 additional requests for each revision arm.
- Requests are archived before dispatch and after completion. A cached completed request can be reused only when its prompt/settings hash matches. Failed or uncertain in-flight requests are never reissued. Preflight-blocked records have `api_attempted=false`.
- A failed, truncated, refused, malformed, or otherwise unsuccessful provider response is a technical failure. It does not receive extra attempts or count as successful abstention. Raw provider exceptions and credentials are not recorded.

There are no automatic model retests, replacement cases, adjusted token limits, prompt rewrites, or parameter reselection after inspecting model results. A future revised experiment needs a separately identified protocol and must preserve this pilot's observations. Resuming a process may finish never-issued requests and reuse completed archived requests; it cannot retry failed or ambiguous requests.

## Outcomes and denominators

**Primary completion metric:** independently valid delivered plans on the eight feasible cases, reported as `k/8`. A delivered plan must satisfy the independent oracle and the common candidate contract. Explicit `null`, withheld output, and technical failures each score zero for feasible completion.

Also report, without merging distinct outcomes:

- Delivery coverage across all 12 cases; invalid or uncertifiable delivered plans across all 12; invalid plans among delivered plans. A zero-delivery arm has an undefined conditional invalid rate, not a demonstrated perfect rate.
- Semantic invalidity and candidate-contract violations separately. A plan can satisfy itinerary arithmetic while citing incorrect evidence IDs.
- Controlled withholding on the four infeasible/evidence-insufficient cases, split into explicit model abstention and runtime withholding. Technical failures remain in the denominator but receive no withholding credit.
- Counts of valid plans, invalid plans, explicit abstentions, verifier-withheld output, and technical failures, with separate breakdowns for feasible, infeasible, and evidence-insufficient cases.

JSON `null` means the model supplied no plan. It is not a correct diagnosis of infeasibility, a proof that no plan exists, or an explanation of the missing measurements. Similarly, a runtime gate suppressing an invalid candidate demonstrates withholding, not the model's ability to recognize why the task cannot be completed. Report these behaviors separately.

The final report must include every case and every arm. It must not substitute “ever generated a valid candidate,” best-of-three scoring, or test-suite pass counts for final delivered-plan performance.

## Uncertainty and paired comparison

Report raw counts before percentages. Give descriptive 95% Wilson intervals for feasible success, with the explicit caveat that binomial intervals assume exchangeable trials: this small, handmade case set is not a random sample of real user tasks. For scale, 8/8 has a Wilson lower limit of approximately 67.6%; it does not demonstrate universal reliability.

For ResiMind versus self-review on the eight feasible cases, report paired wins, losses, ties, both-success, and both-failure counts. Report the two-sided exact McNemar result using only discordant pairs; when none are discordant, report `p=1`. This is exploratory and low-powered. Do not interpret an isolated p-value as proof of broad superiority, or a non-significant result as equivalence. Infeasible and evidence-insufficient cases are not added to the feasible completion denominator.

## Calls, tokens, latency, and cost

For each arm, count the shared initial request once in its **logical** calls, token usage, and model-request latency. Additional calls belong to their respective arm. Separately report physical unique API attempts and usage for the actual experiment, counting the shared request only once. Adding the three logical arm totals would overstate actual experiment consumption.

Report provider-reported input/output tokens, request durations, and measured arm elapsed time. The model-time sum and elapsed time have different meanings; concurrent execution, local checking, network conditions, and resuming from records can affect timings. Missing usage stays unknown, never zero; show known subtotals and missing records. Failed API attempts count toward the call ceiling even when no token usage is returned.

For a comparable list-price estimate, use **$0.30 per million input tokens and $1.20 per million output tokens**, treating all input as cache misses at peak rates. These rates were checked on the [official DeepSeek pricing page](https://api-docs.deepseek.com/quick_start/pricing/) on 2026-10-09. This is an estimate, not an invoice: it does not establish actual cache behavior, billing period, discounts, or future prices.

As a rough planning allowance only, treating each of 40,000 prompt bytes as one token and using the full output allowance gives about **$1.31 for 60 requests** at those rates. Tokenization and provider accounting can differ; this is not an enforced monetary cap. The enforceable limits are the physical call ceiling, input-byte check, and requested output-token limit.

## Reproducibility and interpretation

The freeze command records the baseline commit, protocol/source SHA-256 hashes, settings, system prompt, all case snapshots, independently established ground truth, and initial-prompt hashes in a manifest. Save its digest before dispatch. Archived request records contain prompts, final response text, returned model identifier, usage, finish status, timing, and sanitized errors. Never archive keys or authorization headers.

Offline replay must reproduce the Agent trace and delivery decisions using archived responses, without new API requests. A changed source, setting, case, or prompt hash blocks replay/run continuation rather than silently changing the experiment. Machinery tests cover parsing parity, shared-response accounting, fixed self-review policy, failure handling, independent scoring, and report aggregation. Passing these tests validates the machinery's tested behavior, not the language model's performance.

This whole-itinerary pilot evaluates the **combined verifier, feedback, and state-gate behavior**. It does not isolate residual bookkeeping's contribution: there is no ablation for that question. It also does not evaluate long, multistep mathematical proofs or engineering reasoning, subjective itinerary quality, live-world information, optimality, or external actions. Shared first responses make the comparison paired but correlated. One provider, one response sample per case, provider defaults, and a handmade case set limit generalization. Independent scoring reduces circularity but can itself contain defects; retain witnesses, oracle tests, and per-case diagnostics for inspection.

Use the results to identify the next experiment or implementation weakness. Stronger promotional claims require broader held-out tasks, repetitions, other models, and targeted ablations designed before their results are observed.
