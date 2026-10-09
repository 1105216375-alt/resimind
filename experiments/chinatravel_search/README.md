# ChinaTravel bounded-search development adapter

This separate adapter constructs untrusted schedule alternatives and verifies
the complete plan. It uses `resimind.search`; the generic core has no dependency
on ChinaTravel, a model provider, or LangGraph. This experiment has **not** been
wired into a new live-model run.

- `preflight.py`: non-executing AST recognition of a narrow self-translation
  contradiction. Unknown syntax does not prove feasibility; original DSL stays
  unchanged. A detected contradiction requests retranslation.
- `schedule.py`: bounded exact entity/ID lookup, routes and opening windows,
  minimum visit durations, meal windows, and explicit hotel returns. It keeps
  selected attractions/intercity IDs and cannot certify guest capacity from bed
  count. Unknown entities or impossible windows return an unfinished result.
- `solve.py`: complete verification after each alternative, preserving task
  metadata, attractions, intercity IDs, user-named entities and chosen hotels.
  Searches fastest/preserved/cheapest route policies and at most four nearby
  restaurant alternatives per currently infeasible meal. This is not exhaustive
  planning or global optimization.
- `worker.py`: an isolated offline worker, without gold or a model client.

Default search limits: 16 expansions, 96 generated candidates, depth 4, frontier
32, 90-second cooperative deadline, plus worker CPU/file limits and a 150-second
parent wall timeout. Every schedule construction separately caps route calls
at 256 and total tool calls/cache entries at 512. Acceptance requires a full
check; no unfinished draft is returned as a solution.

## Reproduce the paired development regression

Prepare the pinned official runtime/database following the
[original setup](../chinatravel/README.md). Set `CHINATRAVEL_UPSTREAM` and
`CHINATRAVEL_DATA_DIR`. The offline supervisor requires macOS `sandbox-exec` and
refuses to run without isolation; the generic search core is platform-independent.

From the repository root, choose fresh local directories **outside this public
repository**. Existing published archives supply all five input candidates:

```sh
export PYTHONPATH=src:.
export SEARCH_WORK=/tmp/resimind-search-reproduction
mkdir -p "$SEARCH_WORK"
tar -xJf docs/evidence/chinatravel-repair-v1/guarded-live-development.tar.xz -C "$SEARCH_WORK"
tar -xJf docs/evidence/chinatravel-repair-v1/schema-refinement.tar.xz -C "$SEARCH_WORK"

python scripts/replay_search_development.py \
  --source "$SEARCH_WORK/guarded-live-development" \
  --baseline "$SEARCH_WORK/schema-refinement" \
  --output "$SEARCH_WORK/default" \
  --upstream "$CHINATRAVEL_UPSTREAM" --data "$CHINATRAVEL_DATA_DIR"
```

The default deliberately preserves the zero-room hotel visits whose semantics
the schema cannot represent. To reproduce the **separately disclosed manually
annotated condition**, recover its explicit allowlist from the public summary:

```sh
python - <<'PY'
import json, os
from pathlib import Path
summary = json.loads(Path('docs/evidence/chinatravel-search-v1/summary.json').read_text())
Path(os.environ['SEARCH_WORK'], 'optional-visits.json').write_text(
    json.dumps(summary['runs']['annotated']['optional_visit_policy'], ensure_ascii=False))
PY

python scripts/replay_search_development.py \
  --source "$SEARCH_WORK/guarded-live-development" \
  --baseline "$SEARCH_WORK/schema-refinement" \
  --output "$SEARCH_WORK/annotated" \
  --optional-visits "$SEARCH_WORK/optional-visits.json" \
  --upstream "$CHINATRAVEL_UPSTREAM" --data "$CHINATRAVEL_DATA_DIR"
```

An allowlist is an application-owned semantic decision, never an LLM-generated
permission. Positions are zero-based original `[day, activity]` pairs and bind
to unique exact activity content. Default is none; duplicate content is
ambiguous and refused. Omitting the two annotated hotel visits does not mean
luggage storage was performed. Do not reuse this annotation for new requests.

Every run freezes source/input hashes and the annotation before launching
workers. All workers finish and output hashes are checked before the parent
loads gold. Selection uses local acceptance in original candidate order,
never gold. Output includes a source snapshot and complete local diagnostics;
the script refuses a raw-record destination inside the public repo.

## Tests and observed scope

```sh
PYTHONPATH=src:. python -m pytest tests/test_search.py experiments/chinatravel_search -q
PYTHONPATH=src:. python -m examples.bounded_search
```

Current verification: 847 core/domain tests and 178 combined repair/search
integration tests passed in their prepared environments; no model calls.
Default replay: 0/5 candidates, 0/2 source tasks. Annotated replay: 3/5
candidates from **one** source task converge to the same valid sandbox plan;
1/2 source tasks pass. These are seen development cases with manual semantic
input, not autonomous end-to-end or held-out results. See the
[full interpretation](../../docs/search-development.zh-CN.md) and
[compact summary](../../docs/evidence/chinatravel-search-v1/summary.json).

Publish test code, methods, compact results and selected examples. Keep routine
raw requests/responses, full tool dumps and intermediate debug logs local.
Previously published evidence remains available; this run adds no raw archive.

ChinaTravel official code remains pinned to
`f445e0011f42594fcc29b8c752ece06ded9a8218` (MIT). Tasks, data and derived content
retain **CC BY 4.0** attribution to Jie-Jing Shao and the ChinaTravel authors;
source links and revisions are in the [original protocol](../chinatravel/PROTOCOL.md).
