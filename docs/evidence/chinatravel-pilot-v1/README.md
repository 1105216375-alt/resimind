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

The source/manifest commit is the preregistration record. Results are pending
at that commit; later result commits must retain the original manifest.

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
