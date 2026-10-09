# ChinaTravel repair development

This additive experiment investigates the failures in the frozen v1 pilot.
The original `experiments/chinatravel/` sources and evidence stay unchanged.
The new `resimind.repair` module changes the current source inventory: to verify
the original v1 manifest, check out commit
`ed6d03f718df86400626c3d4e38cfb821eec6560` in a separate directory. Do not rewrite
old manifest hashes to match newer code.

## What changed

- Exact sandbox entity/transport-ID lookup, quantity-based costs, and grounded
  routes using the already selected mode. Unknown entities, ambiguous rows,
  missing mode/quantity intent, or impossible time windows stay unresolved.
- Atomic local repairs with registered tool evidence, base-plan and old-value
  checks, complete revalidation, and no regression of previously passed checks.
- Traveller coverage by tickets and taxis; positive room/bed quantities and
  exact hotel binding. Bed count cannot establish guest capacity; actual hotel
  occupancy limits are unavailable in this dataset.
- A schema-only final-plan prompt with the original request at the end, explicit
  total-cost arithmetic, and public task metadata checks. The native exploration
  prompt remains, with corrected task/cost instructions appended.
- A bad/empty self-translation stops generation as unfinished. Automatic
  retranslation, replacement entity selection, missing-mode decisions and
  schedule search are not implemented in this repair adapter. The separate
  [search adapter](../chinatravel_search/README.md) adds limited schedule/entity
  alternatives and contradiction detection, but no automatic retranslation.
- An explicit overnight guard: the last listed activity of each nonfinal day
  must be an accommodation ending at `24:00` before the next day's route may
  be bound. A morning hotel check-in followed by dinner elsewhere does not
  establish an overnight return. This is a conservative local requirement
  beyond the official scorer; the generator receives the same requirement.

This changes both the proposer and repair process. It is an enhanced agent,
not an unchanged official ReAct baseline or a residual-only ablation. The
original ReAct instance keeps its total 50-step budget across up to three model
candidates. Up to three successful local repair rounds are allowed per candidate.
The neural model still chooses activities and revises unresolved decisions;
deterministic repairs propose only changes supported by the sandbox.

## Reproduce the offline paired replay

Prepare the pinned official checkout, complete sandbox and isolated Python
environment as in [the original setup](../chinatravel/README.md). Set
`CHINATRAVEL_UPSTREAM` and `CHINATRAVEL_DATA_DIR`. Commands run from the repo root:

```sh
export PYTHONPATH=src:.
python -m pytest tests/test_repair.py experiments/chinatravel_repair -q -ra

python -m experiments.chinatravel_repair.replay repair \
  --source docs/evidence/chinatravel-pilot-v1 \
  --output /tmp/resimind-repair-replay \
  --data "$CHINATRAVEL_DATA_DIR" --upstream "$CHINATRAVEL_UPSTREAM"

python -m experiments.chinatravel_repair.replay score \
  --source docs/evidence/chinatravel-pilot-v1 \
  --output /tmp/resimind-repair-replay \
  --data "$CHINATRAVEL_DATA_DIR" --upstream "$CHINATRAVEL_UPSTREAM"
```

Choose a fresh output directory: the command refuses to overwrite an earlier
run. It reads all 28 nonempty candidates from the published, hash-checked v1
archives. Each repair worker receives only its candidate, public query, recorded
self-translation, and sandbox. The worker cannot read gold or contact a model.
All workers finish and output hashes are checked before the separate `score`
phase reads gold. The original plan, repaired draft and accepted delivery are
scored separately. The report also separates the shared 12 initial candidates
from the correlated later candidates.

This is a **seen-case development replay**, not a new held-out success rate.
There are no new model API calls. Source and input hashes are frozen before
execution. Replay uses macOS `sandbox-exec`, the same strict read/write isolation
as the original supervisor, up to three concurrent processes and a 150-second
wall limit per candidate. The reusable core repair API has no macOS dependency.

## Reproduce the two-task live development smoke test

The live command only selects the two original development UIDs, never the
12 formal tasks. It reuses the original parent broker, runtime verification,
resource limits and sandbox unchanged, selecting the new `resimind_repair`
worker. Default model and limits match v1: DeepSeek Flash, temperature 0,
non-thinking, 128 calls and 600 seconds per task, 8192 tokens per response,
65,536 cumulative output tokens, no SDK retry. See the saved manifest for all
settings. Only the parent reads the locally configured API key.

```sh
python -m experiments.chinatravel_repair.live freeze \
  --output /tmp/resimind-repair-live-dev \
  --data "$CHINATRAVEL_DATA_DIR" --upstream "$CHINATRAVEL_UPSTREAM"
python -m experiments.chinatravel_repair.live verify \
  --output /tmp/resimind-repair-live-dev \
  --data "$CHINATRAVEL_DATA_DIR" --upstream "$CHINATRAVEL_UPSTREAM"
# Configure DEEPSEEK_API_KEY locally. This command incurs provider usage.
python -m experiments.chinatravel_repair.live run --live \
  --output /tmp/resimind-repair-live-dev \
  --data "$CHINATRAVEL_DATA_DIR" --upstream "$CHINATRAVEL_UPSTREAM"
python -m experiments.chinatravel_repair.live score \
  --output /tmp/resimind-repair-live-dev \
  --data "$CHINATRAVEL_DATA_DIR" --upstream "$CHINATRAVEL_UPSTREAM"
```

Do not change frozen code, runtime or data between freeze, execution and scoring.
The selected UIDs are the first two sorted UIDs. The initial pre-guard
development manifest retained the original supervisor's general formal
`selection` description; its explicit `development` phase and selected UID list
were authoritative. The current freezer records the development rule directly.
These runs are development diagnostics,
not a preregistered formal leaderboard comparison. Historical development runs
used independently sampled candidates; their difference is not causal evidence
for one component.

## Final schema-repair regression

The guarded live round exposed a checker integration bug: filling missing
train endpoints made schema pass, which introduced new binding check names and
therefore correctly triggered the transaction's changed-contract refusal.
The current checker now keeps check names stable across this transition and
reports the missing field names. The core same-contract requirement stays intact.

No third model round was run. The final code was checked against **all five**
nonempty candidates from the guarded live run, using the same isolated worker:

```sh
python scripts/replay_repair_refinement.py \
  --source WORKDIR/guarded-live-development \
  --output /tmp/resimind-schema-refinement \
  --upstream "$CHINATRAVEL_UPSTREAM" --data "$CHINATRAVEL_DATA_DIR"
```

Extract `guarded-live-development.tar.xz` from the evidence collection into
`WORKDIR` first. The script records sources and inputs before repairing, then
scores only after all five outputs are complete. Schema improves from 0/5 to
3/5; full completion stays 0/5. It reports the five correlated candidates and
their two source tasks separately, never selecting a candidate by gold score.

[Results and limitations (中文)](../../docs/repair-development.zh-CN.md) ·
[Recorded artifacts](../../docs/evidence/chinatravel-repair-v1/README.md) ·
[Generic repair API](../../docs/local-repair.md)
