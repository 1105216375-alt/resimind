# ChinaTravel original-task pilot v1

This cohort was frozen before its first live model request: **12 original
Human-Val tasks × 3 arms = 36 runs**, one sample per condition. The complete
154-task dataset was prepared; only the listed 12 tasks are formal observations.

- [Frozen manifest](manifest.json) and [SHA-256](manifest.sha256)
- [Protocol and development amendments](../../../experiments/chinatravel/PROTOCOL.md)
- [Reproduction instructions](../../../experiments/chinatravel/README.md)
- [Both excluded development rounds and original source snapshots](../chinatravel-development/README.md)

Arms: official ReAct, official model-guided NeSy, and the same official ReAct
wrapped by ResiMind's whole-plan gate and feedback. All use `deepseek-flash`,
temperature 0, non-thinking mode, the same external ceilings, no gold input,
and the pinned official sandbox/evaluator. A local gate acceptance is not a
substitute for the separate official gold score.

The source/manifest commit is the [preregistration record](https://github.com/1105216375-alt/resimind/commit/f3f0dc5e73cd50d89349e2eb43cb99e23ab76600), published before formal requests.
The original manifest remains unchanged in this completed result archive.

## Results

| Arm | Official final success | Invalid deliveries | API attempts |
| --- | ---: | ---: | ---: |
| Official ReAct | 0/12 | 11/12 | 874 |
| Official NeSy | 5/12 | 0/12 | 896 |
| ReAct + ResiMind | 1/12 | 0/12 | 1,123 |

ResiMind's integration repaired one task after two feedback rounds and delivered
no invalid plan in this cohort. It left 11 tasks unfinished and completed fewer
tasks than NeSy. Abstention is not success. This small single-run experiment
does not establish a general ranking or isolate a residual-mechanism effect.
NeSy hit the shared 128-call ceiling on six tasks. One ReAct and one NeSy run
ended on truncated model responses; neither was retried.

Post-hoc candidate review found 28 real ResiMind candidates: 20 rejected and
7 deferred candidates failed gold evaluation; the single accepted candidate
passed. With only one valid candidate, this does not establish a general
false-rejection rate. Local checks share official schema/environment code;
gold DSL scoring is separate from generation.

- [Full report and limitations (中文)](../../chinatravel-evaluation.zh-CN.md)
- [Scores and per-task diagnoses](scored-results.json)
- [Official evaluator output](official-evaluation.json)
- [Independent accounting audit](audit.json): all 36 terminal results, 2,899
  request records, 2,893 API attempts; six records are undispatched budget stops.
- [Post-hoc candidate analysis](candidate-analysis.json)
- [Plain terminal results](results/) and [lossless per-task archives](archives/)
- [Compact archive SHA-256 index](archive-index.json) and [lossless per-member hashes](member-hashes.json.xz)

There were no missing-usage requests. Reported usage is 53,873,109 input and
424,711 output tokens, including failed/truncated responses. Most input tokens
were cache hits; input-token volume alone is not a price estimate. Wall times
were observed with three concurrent jobs on a shared host.

## Offline inspection

Raw `runs/` directories are distributed as `.tar.xz` archives to avoid repeating
large tool and conversation records in Git. From the repository root:

```sh
CT_RESULTS=docs/evidence/chinatravel-pilot-v1
for archive in "$CT_RESULTS"/archives/*.tar.xz; do
  tar -xJf "$archive" -C "$CT_RESULTS"
done
python scripts/audit_chinatravel_records.py --root "$CT_RESULTS"
```

The audit uses only the Python standard library, verifies request/ledger
consistency, and makes no API calls. Compare archive hashes with the compact
index; decompress `member-hashes.json.xz` for every original member's digest.
`results/<uid>/<arm>.json` is an identical convenient
copy of each archived terminal result. Full frozen-source/runtime verification
and official rescoring use the experiment commands in the reproduction guide;
strict verification requires the recorded Python/dependency environment.

The optional `scripts/analyze_chinatravel_candidates.py` supports `--root`,
`--upstream` and relocated `--data` for the post-hoc candidate audit. It uses
the frozen official scorer and never feeds gold scores into generation.
Do not describe archive inspection or offline rescoring as exact replay of
NeSy's time-dependent search trajectories.

## Data attribution

ChinaTravel query data, gold constraints and sandbox-derived data are **CC BY
4.0**, attributed to Jie-Jing Shao and the ChinaTravel authors. Query text and
constraints are unmodified; preparation normalizes serialization. Generated
plans and experiment traces are new artifacts. The official code/prompts and
ResiMind experiment code are MIT.

Sources: [official code](https://github.com/LAMDA-NeSy/ChinaTravel/tree/f445e0011f42594fcc29b8c752ece06ded9a8218),
[query data](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel/tree/a7d893afb628af6ca181625b6c7cca2d60a6bccb),
[sandbox data](https://huggingface.co/datasets/LAMDA-NeSy/ChinaTravel-Sandbox/tree/885c7dad143bf040238ddca263f4788d582ee0f4),
[CC BY 4.0 license](https://creativecommons.org/licenses/by/4.0/).
