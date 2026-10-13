# ResiMind v0.13.0 release validation — 2026-10-13

This release connects verified rule memory, compact exact polynomial output,
residual-guided search and empirical route selection. It remains experimental.
The new adapters are opt-in; existing adaptive defaults remain unchanged.

## Measured behavior

| Check | Result |
| --- | --- |
| Full source regression suite | 1,769 passed; 21 optional skips |
| Installed Lean checks | Included in the local source regression run |
| Source archive and wheel build | Both built successfully for 0.13.0 |
| Installed wheel outside the checkout | Version metadata, `lifelong` and `search-learning` demos passed without optional packages |
| Generated polynomial development tasks, three seeds | New adapter 240/240; prior frozen search 220/240; zero model calls |
| Compact tool without memory | 240/240: explains the completion-rate gain |
| Fixed recall-first memory | 48 direct rule completions; no extra completion gain |
| Earlier difficult expansion cases | 8/8, independently checked by a degree-complete exact-grid oracle |
| Synthetic bridge input-update sequence | 20 case checks versus 32 fresh checks; equal independently scored results |

The polynomial scorer uses SymPy coefficients. SymPy also completes 240/240;
this is not a claim of superiority over CAS or other Agent frameworks.
Reported latency is a descriptive local measurement, with different work
units between rewrite search and exact computation.

The compact certificate is an exact algebra result, not a Lean proof.
When a caller requires Lean, the exact-only compact route is unavailable;
recall/search must pass the configured proof gate.

The offline `lifelong` demo learns a rule, saves and reloads frozen memory,
transfers the rule to a new problem and verifies a long expansion. It runs
without a model API key or optional package.

## Reproduction and records

- [Compact computation, memory and policy ablations](lifelong-math.md)
- [Search, schema learning and incremental bridge checks](search-learning.md)
- [Implementation and research boundaries](frontier-program.zh-CN.md)
- Run `python -m pytest -q` with the development dependencies installed.
- Run `python -m resimind demo --domain lifelong` after installation.

Only a compact validation summary and reproducible benchmark code are published.
Raw model requests, local test logs and saved evaluation memory stay outside
the repository. Optional dependency/toolchain availability affects skipped tests;
the GitHub workflow checks Python 3.10–3.13 and package builds separately.
