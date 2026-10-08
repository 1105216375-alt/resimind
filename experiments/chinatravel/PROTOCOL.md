# ChinaTravel comparison protocol

Status: **formal protocol, finalized after two development rounds**. Before the
first formal model request, freeze this protocol, the actual runner settings,
the selected UIDs, and all generation/scoring source hashes in the run manifest.
This document records an experiment design, not completed benchmark results.

Development amendment (before any formal request): development v1's 2 MB
cumulative input ceiling stopped both ReAct-based arms before their first plan
on both development tasks. One NeSy run reached the 64-call ceiling. For v2,
all arms receive 128 calls, 320 KB per request and 16 MB cumulative input;
other model, search and wall-time settings remain fixed. These ceilings allow
the native 50-step ReAct loop (two calls per step plus plan/translation calls)
more room without changing prompts or search logic. An IPC serialization fix
converts native NumPy scalars to ordinary JSON numbers before sending results.
One v1 parent-save failure was recovered from the worker's already saved JSON,
without rerunning a model request. Both development cohorts and the v1 frozen
source snapshot are retained; neither development UID enters formal results.

Development v2 exposed an instrumentation defect: repeated tool-result logging
filled the 64 MiB single-file limit and stopped one NeSy development run. Before
formal execution, replace this logger with bounded, deduplicated auditing for
all arms: at most 10,000 events / 8 MiB of event records, individual results up
to 1 MiB and all result files up to 16 MiB, content-addressed result files, and
explicit omitted-event counters.
Logging truncation does not stop or change tool execution. The two development
rounds retain their original outcomes, including this recording failure and one
8192-token model-response truncation. The formal generator settings remain the
development-v2 values; there is no third development model run or selective
rerun. Test-fixture portability and reporting field names were also clarified.

## Question and scope

Compare the official ChinaTravel ReAct and LLM-guided NeSy implementations with
the same official ReAct proposer wrapped by ResiMind's acceptance and feedback
loop, under one model and the same external resource ceilings. This evaluates
a particular ResiMind integration; ResiMind is not an independent travel solver.

The complete Chinese sandbox and all 154 Human-Val queries are available
locally. The formal experiment uses **12 preselected, unmodified tasks**, with
three arms and one run per arm/task (36 formal runs). Development acceptance uses
the two reserved cases across all three arms (6 separate development runs).
Downloading and auditing
154 queries does not mean that all 154 were evaluated. The remaining cases are
not treated as successes, failures, or additional evidence. The small cohort
and single run per condition do not support broad performance claims.

## Pinned official resources

| Resource | Version | Integrity |
| --- | --- | --- |
| Official code and evaluator | `f445e0011f42594fcc29b8c752ece06ded9a8218` | Freeze the checkout commit, dirty status, and relevant source SHA-256 values before formal execution. |
| Query repository | `a7d893afb628af6ca181625b6c7cca2d60a6bccb` | `human.csv`: 171,906 bytes; SHA-256 `2f0d61e37665a345bd428732ace6e1ce9d6de2af8d292047bcf5cc1c4f9cfc78`. |
| Sandbox repository | `885c7dad143bf040238ddca263f4788d582ee0f4`, release `2026.08.2` | Chinese ZIP: 1,027,737 bytes; SHA-256 `42d95ff7b8f96574d0ee972ca77599258e4854a219617868caeb462b79c715bd`. |
| Local preparation manifest | Schema version 1 | `dataset_manifest.json`: SHA-256 `4c3d57c60092c5740316bb0e1850a5dc39f051037b319c63abf8be4f8ee31dbf`. |

All 132 extracted Chinese sandbox files match the official per-file checksum
list; their total uncompressed size is 5,125,256 bytes. All 154 query UIDs match
the official `human.txt` split, with 683 retained gold DSL constraints.

CSV normalization changes serialization only: integer and boolean fields are
decoded, and Python list literals in `hard_logic_py` become JSON arrays. Query
text, constraints, task duration, and sandbox contents are not shortened or
rewritten. Public generation files contain exactly `uid` and `nature_language`;
all other dataset fields remain in a separate gold directory.

These are maintained official releases with 2026.08 evaluator and data repairs.
This experiment is not an exact reproduction of historical paper scores.
The sandbox release manifest's older README checksum differs from the later
dataset-card revision; the Chinese ZIP and all 132 database-file checksums
match. Preserve both upstream metadata and the local audit record.

Official references and attribution:

- [ChinaTravel source at the pinned revision](https://github.com/LAMDA-NeSy/ChinaTravel/tree/f445e0011f42594fcc29b8c752ece06ded9a8218), code and documentation under MIT.
- [Query data at the pinned revision](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel/tree/a7d893afb628af6ca181625b6c7cca2d60a6bccb), official data under CC BY 4.0.
- [Sandbox at the pinned revision](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox/tree/885c7dad143bf040238ddca263f4788d582ee0f4), official data under CC BY 4.0.
- [ChinaTravel paper, ICLR 2026](https://openreview.net/forum?id=0YRVlxY9BH), Jie-Jing Shao and colleagues, *ChinaTravel: An Open-Ended Travel Planning Benchmark with Compositional Constraint Validation for Language Agents*.

## Selection before model results

Sort all 154 UIDs lexicographically. Reserve the first two solely for interface,
isolation, budget, and development acceptance checks:

- `h20241029143447759844`
- `h20241029143450090032`

Exclude those two UIDs. For every remaining UID, compute
`sha256(('resimind-chinatravel-v1:' + uid).encode('utf-8')).hexdigest()`.
Sort by the resulting hexadecimal digest (UID as a deterministic tie breaker)
and select the first 12. No model output, score, difficulty judgment, or task
content enters this selection rule.

| Order | Formal UID | Selection SHA-256 |
| --- | --- | --- |
| 1 | `h20241029143620980391` | `012db1f6db5e9e610530aeba600a8fb1e6ab4d5f246fab62afb608268458f19c` |
| 2 | `h20241029143506461809` | `016cac31cee3f091be842b366d3ab8a01e855e9e3db7c1b0b4f2477528bb1acb` |
| 3 | `h20241029143659983667` | `028408b0005635a6ecb1ec3547fb8f57124e6f15f661454782e52cbe6df3e335` |
| 4 | `h20241029143727719855` | `03a89801c23cbcf5ef932e81d2860789ccc1f59575968f429862a15d24b04091` |
| 5 | `h20241029143546424651` | `03d9436f514875c4d3097657ac63732b3fa9f2a8c491cf9b3051f8eb19481ffd` |
| 6 | `h20241029143457494209` | `04a0ed459e737f6e638709cfb75aaed0503f1bca243d45d79530434dfb893501` |
| 7 | `h20241029143514054328` | `04bfd201cc4cca5c6bdf41974ef5db01ca1334af3eb1e1445afe060dcbd2b049` |
| 8 | `h20241029143729369677` | `04f549399fe60ae7964dcf122323a932e5c30e624c77ff33e95622d52ea1325a` |
| 9 | `h20241029143518931601` | `050c5bb0467a9f66d54336d1257f2a375f568956a3e6a0f7217b52f44ae9bb45` |
| 10 | `h20241029143521646393` | `06d15653277807c54a93caddd10f204779fb04a59fd707c44dc2e22fafc527de` |
| 11 | `h20241029143743586105` | `083d1627d5bd84deda4d97e4b7571b0849908aa4b4294a17041c9096acb469ba` |
| 12 | `h20241029143455115600` | `0a03502d2d05234f8f28276151098ed8a58036daa51f09e94314abfbb6ca9d51` |

Development outputs and formal outputs must have separate namespaces. Arm
execution order must be recorded before formal calls; arm order must not depend
on scores. Do not replace a failed formal UID or regenerate a case selectively.

## Compared arms

| Arm identifier | Generation and delivered output |
| --- | --- |
| `official_react` | Official Chinese `ReAct` from `create_agent_runtime`, including its original prompts, environment, notebook, native step limit, and final-plan generation. Return its first final artifact. |
| `official_nesy` | Official `LLMNeSy` / `LLMDrivenAgent`, with model-predicted translation, `oracle_translation=False`, `oralce_translation=False`, `load_cache=False`, and `preference_search=False`. Preserve its native search and fallback-plan behavior. |
| `resimind_react` | The same official ReAct implementation used above, followed by the ResiMind whole-plan gate. Rejected/deferred candidates receive local diagnostics and may be revised, with at most three whole-plan proposals. Continue the same notebook, scratchpad, and native step counter; do not reset or grant a fresh ReAct step budget. Deliver only a locally accepted plan; abstention remains an unresolved formal output. |

The ResiMind gate checks the public output schema, sandbox-grounded common-sense
conditions, and DSL generated by the official translator from the public
natural-language query. Its translation and reflection calls consume the same
run budget as planning and revision. The translation is cached only within that
case/arm run. Gold translation, gold logical constraints, official hidden score
feedback, other arms' outputs, and other cases' caches are unavailable to
generation. Native upstream in-memory parsing/JSON repair and translator
reflection remain part of the algorithms; they are distinct from transport
retries.

A locally accepted ResiMind plan establishes only the **self-translated local
contract**. It does not prove that translation captured every natural-language
requirement or that the official gold evaluator will accept the plan. Translation
failures, unresolved checks, and checker exceptions must not become positive
proofs. Save rejection/defer reasons and every candidate separately from the
delivered output.

## Shared resource ceilings

All quantities below apply independently to each case/arm run. Equal ceilings
do not imply equal realized spend; report actual usage for every arm.

| Setting | Frozen value |
| --- | --- |
| Model identifier | `deepseek-flash` |
| Temperature | `0` |
| Reasoning effort | `none` |
| Maximum output tokens per model request | `8192` |
| Request timeout | `90` seconds |
| Whole-run wall-clock deadline | `600` seconds |
| Maximum dispatched model requests | `128` |
| Cumulative prompt bytes | `16,000,000` |
| Prompt bytes per request | `320,000` |
| Cumulative output tokens | `65,536` |
| Provider SDK retries | `0`; no automatic transport retry or case restart |
| NeSy native search setting | `TIME_CUT = 300` seconds |
| ResiMind whole-plan proposal limit | `3`, including the initial proposal |
| Worker CPU limit | `420` CPU seconds |
| Maximum individual worker file size | `67,108,864` bytes |
| Concurrent supervised jobs | `3` |

The parent broker is authoritative: it enforces model settings, dispatch counts,
serialized UTF-8 message byte accounting, output allowances, timeouts, and the
wall deadline. Prompt bytes are the length of the UTF-8 encoding of
`json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(',', ':'))`.
Every model request counts, including translation, reflection, search guidance,
plan formatting, and revision. Retried requests cannot be hidden in SDK defaults.
Preserve provider-reported token usage and explicit missing-usage/error states;
do not report missing usage as zero cost. Reserve remaining output allowance
before dispatch so a request cannot silently exceed the cumulative cap.

The 600-second wall deadline includes initialization, model waits, translation,
tool use, search, and gate work. NeSy keeps the upstream timing implementation:
its main DFS cutoff adds measured recommendation-LLM time to the native search
allowance, while some native outer-loop checks use elapsed time directly. Thus
`TIME_CUT=300` is the official parameter, not a claim that every code path receives
exactly 300 seconds of pure computation. The external wall deadline still applies.

The supervisor uses an independent worker-kill watchdog and a total local
deadline for each API response. A timed-out remote request may still be billed;
discard its late response, do not retry, and mark usage unknown if no usage was
received. Token totals in reports are reported usage, not an assertion that
unobserved requests cost zero. No hard memory limit is claimed.

## Isolation, freeze, and audit artifacts

Run one isolated worker per arm/case. The worker receives the two-field public
query and the complete sandbox, with separate output/cache directories. Provider
credentials and the API client remain in the parent broker. The launcher must
enforce filesystem isolation from gold/raw-query files and other run outputs,
and deny direct external worker network access; the worker module alone is not
an operating-system isolation boundary.

Before formal generation, finish offline tests and development acceptance, then
freeze a manifest containing the selected UIDs, execution order, settings,
source-file SHA-256 values, upstream revision and dirty status, dependency
versions, and data hashes. Verify the frozen values before running and scoring.
Do not tune prompts, gate logic, budgets, or sample selection after observing
formal outputs. If an infrastructure defect requires an amendment, retain the
original records and create a separately versioned protocol/cohort; do not
silently substitute favorable reruns.

Retain model requests/responses, request parameters and reported model IDs,
usage ledgers, terminal status, native logs, translations, candidates, gate
decisions, accepted plans, and offline score tables. Do not publish credentials.
The current `WorldEnv.__call__` trace captures command-interface calls; native
NeSy may invoke data APIs directly. Do not treat that trace alone as comparable
total tool-call counts across arms.

## Independent official scoring and reporting

Use `official_scoring.score_batch` with the complete frozen 12-UID cohort and
separate gold annotations. It directly calls the unchanged official schema,
common-sense, and `evaluate_hard_constraints_v2` functions with `lang='zh'`.
The adapter avoids the current `eval_exp.py` query-loader path that strips gold
DSL fields; it does not replace the official constraint semantics.

The primary outcome is official final success: the intersection of schema,
common-sense, and gold logical pass UID sets, divided by **12 for each arm**.
Report the numerator as well as the percentage. Preserve official schema,
common-sense macro/micro, logical macro/micro, and conditional logical metrics
as secondary outcomes. Compute corpus metrics in one batch; do not average
per-case micro percentages. The native `success` flag and the ResiMind local
acceptance flag are separate diagnostic outcomes, not substitutes for this score.

Missing, malformed, budget-exhausted, timed-out, technically failed, or abstained
cases remain in the denominator. Score the actual delivered artifact; represent
missing artifacts as `{}` as the official loader does. Retain native fallback
plans if the official algorithm delivered one within the run. Do not promote an
intermediate or locally rejected candidate to a delivered success after a stop.
Record infrastructure failures separately from model failures while retaining
their case slots. A broken scoring setup must stop reporting rather than be
silently converted into an accuracy result.

Report a per-UID paired table for all three arms, terminal-state counts, local
acceptance versus official final success, requests/tokens/prompt bytes, and wall
time. Show absolute paired differences and describe this as a 12-task pilot with
bounded resources. Use neither the complete-data download nor a local ResiMind
proof to imply full-benchmark completion or complete natural-language correctness.

## Reproducing data preparation

Preparation uses the Python standard library and performs no network requests.
Download the three fixed official files first; keep TLS verification enabled.
The following shell commands use a new local directory and do not contain
credentials:

```sh
CT_WORKDIR="$(mktemp -d)"
curl --fail --location --output "$CT_WORKDIR/human.csv" 'https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel/resolve/a7d893afb628af6ca181625b6c7cca2d60a6bccb/human.csv'
curl --fail --location --output "$CT_WORKDIR/ChinaTravel_sandbox_zh.zip" 'https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox/resolve/885c7dad143bf040238ddca263f4788d582ee0f4/raw/ChinaTravel_sandbox_zh.zip'
curl --fail --location --output "$CT_WORKDIR/SHA256SUMS.zh" 'https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox/resolve/885c7dad143bf040238ddca263f4788d582ee0f4/manifests/SHA256SUMS.zh'
git clone https://github.com/LAMDA-NeSy/ChinaTravel.git "$CT_WORKDIR/upstream"
git -C "$CT_WORKDIR/upstream" checkout f445e0011f42594fcc29b8c752ece06ded9a8218
python -m experiments.chinatravel.prepare_data \
  --human-csv "$CT_WORKDIR/human.csv" \
  --sandbox-zip "$CT_WORKDIR/ChinaTravel_sandbox_zh.zip" \
  --sandbox-checksums "$CT_WORKDIR/SHA256SUMS.zh" \
  --upstream "$CT_WORKDIR/upstream" \
  --output "$CT_WORKDIR/prepared" \
  --link-sandbox
```

Run the preparation command from the ResiMind repository root. The output
directory must not already exist. The optional `--link-sandbox` creates the
official checkout's `chinatravel/environment/database` symlink and refuses to
overwrite an existing different database. Both workers and scoring must use this
same prepared sandbox. Keep downloaded and prepared datasets outside the public
repository, or in explicitly ignored local directories.

The script validates the exact CSV, ZIP, and checksum-file SHA-256 values,
verifies all 132 official source-file hashes, checks all 154 UIDs against the
official split, and regenerates all 308 public/gold JSON files. Their byte hashes
match the original preparation. The reproducible manifest includes compatible
`normalized_files`, `all_uids`, `dev_uids`, `upstream`, and sandbox metadata for
the runner; its overall hash may differ from the initial audit snapshot because
local paths and provenance metadata differ. Freeze the newly generated manifest
for a new experiment instead of claiming an identical initial-manifest hash.
