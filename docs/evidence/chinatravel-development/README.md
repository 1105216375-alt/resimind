# Development records, excluded from the formal cohort

These archives contain two rounds on the **same two development UIDs**, six
case/arm runs per round. Neither UID is in the 12-task formal cohort. These are
integration/debugging records, not additional formal benchmark samples.

| Round | Official ReAct | Official NeSy | ReAct + ResiMind | Development finding |
| --- | --- | --- | --- | --- |
| v1 | 0/2 final success; both input-budget stops | 1/2; one call-budget stop | 0/2; both input-budget stops | 2 MB cumulative prompts prevented first ReAct plans. Native NumPy integers also exposed an IPC serialization defect. |
| v2 | 0/2; two invalid delivered plans | 1/2; one instrumentation failure | 0/2; one withheld plan and one truncated model response | The gate ran and rejected invalid candidates. Repeated tool-result logging reached a file-size cap during NeSy search. |

Before formal generation, the shared budgets changed from 64 calls / 160 KB
per prompt / 2 MB cumulative prompts to **128 calls / 320 KB / 16 MB**.
Temperature, model, output-token ceiling, search setting, wall deadline and
algorithm prompts were unchanged. Native numeric values were normalized to
JSON without loss of integer precision. Tool auditing was bounded and
deduplicated so logging truncation cannot stop the algorithm. See the final
[protocol](../../../experiments/chinatravel/PROTOCOL.md) for exact limits.

One v1 NeSy parent record was recovered from the identical JSON already written
by its worker after the parent could not serialize a NumPy scalar. No model was
rerun for that recovery; its elapsed time is explicitly marked as the worker's
timer because the supervisor's total was unavailable. The v2 log-limit failure
was retained as a failure. There were no selective replacements of formal data.

Each archive includes its manifest, all query inputs and gold annotations,
requests/responses and reported usage, native logs, scored results, and a
`source-at-freeze.zip` whose source bytes match that round's manifest. Extract
the matching source snapshot to inspect or reproduce old offline scoring; the
current source deliberately rejects an old incompatible freeze.

- [Development v1](development-v1.tar.xz)
- [Development v2](development-v2.tar.xz)
- [Archive and per-member SHA-256 index](archive-index.json)

Model credentials are excluded. Gold annotations are public benchmark data but
were withheld from solving processes. ChinaTravel code/prompts are MIT; the
original query and sandbox-derived data are **CC BY 4.0**, attributed to Jie-Jing
Shao and the ChinaTravel authors. ResiMind's experiment code is MIT.

Sources: [official code](https://github.com/LAMDA-NeSy/ChinaTravel/tree/f445e0011f42594fcc29b8c752ece06ded9a8218),
[query data](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel/tree/a7d893afb628af6ca181625b6c7cca2d60a6bccb),
[sandbox data](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox/tree/885c7dad143bf040238ddca263f4788d582ee0f4),
[CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/).
Normalization changes serialization only; generated plans and logs are new
experimental artifacts, not gold reference answers.
