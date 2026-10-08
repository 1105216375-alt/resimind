# ChinaTravel experiment

This experiment compares official ChinaTravel ReAct, official LLM-guided NeSy,
and the same official ReAct proposer with a ResiMind whole-plan acceptance and
feedback loop. It prepares all 154 Human-Val queries and the complete Chinese
sandbox, then evaluates a fixed **12-task formal cohort across three arms**.
The two separate development tasks produce six development runs.

Read [PROTOCOL.md](PROTOCOL.md) for the selected UIDs, data/source versions,
current budgets, development amendments, and scoring rules. The experiment
measures this gate-and-feedback integration. Component attribution or a full
residual-mechanism ablation would require additional experimental arms. A local
ResiMind acceptance proves only the self-translated contract; final success is
determined separately by the official gold evaluator.

## Platform and directory requirements

The live experiment supervisor requires **macOS with
`/usr/bin/sandbox-exec`** and Python 3.12. It refuses to launch workers when this
isolation mechanism is unavailable; there is no permissive Linux or unsandboxed
fallback. The ResiMind core and standalone preparation/scoring modules remain
portable, subject to their dependencies. This supervisor's platform restriction
does not apply to the core library.

Run all module commands below from the ResiMind repository root. Keep the
official checkout, downloaded files, prepared data, virtual environment, and run
outputs in a separate local work directory. In particular, output directories
and files containing gold/raw queries must not be inside worker-readable trees:

- this repository's `src/` or `experiments/`;
- the official checkout's `chinatravel/`;
- the prepared sandbox's `database/`;
- the Python runtime or virtual environment's readable library directories.

The worker can read its own fresh per-job output directory, public code, and
sandbox. Put no unrelated secrets or gold labels there. The parent owns API
credentials, budget accounting, and post-generation scoring. A run folder
contains gold and raw traces; retain ChinaTravel's CC BY 4.0 attribution when
publishing benchmark-derived artifacts.

## Prepare the pinned data

Follow [Reproducing data preparation](PROTOCOL.md#reproducing-data-preparation).
Those commands download the fixed official CSV, Chinese ZIP, and checksum list,
check out the pinned official code, and run `prepare_data.py --link-sandbox`.
Preparation is offline after those downloads and uses only the Python standard
library. It checks all 154 UIDs, all 132 sandbox files, and the source hashes;
it writes separate `public/` and `gold/` query files.

The protocol's example creates `CT_WORKDIR` using `mktemp -d`. Keep that shell
session, or set `CT_WORKDIR` to the actual directory you created. With the example
layout, configure:

```sh
export CHINATRAVEL_UPSTREAM="$CT_WORKDIR/upstream"
export CHINATRAVEL_DATA_DIR="$CT_WORKDIR/prepared"
CT_RUNS="$CT_WORKDIR/runs"
```

The official checkout's `chinatravel/environment/database` must resolve to
`$CHINATRAVEL_DATA_DIR/database`. `--link-sandbox` creates this link and refuses
to replace a different existing database. The runner verifies that generation
and scoring use the same sandbox. A newly prepared manifest may have a different
overall hash because local paths and audit metadata differ; normalized query
bytes and sandbox hashes must match the pinned release.

## Install an isolated Python 3.12 environment

```sh
python3.12 -m venv "$CT_WORKDIR/venv"
source "$CT_WORKDIR/venv/bin/activate"
python -m pip install -r "$CHINATRAVEL_UPSTREAM/requirements.txt" pytest==8.4.2
export PYTHONPATH=src:.
```

The experiment imports ResiMind from this checkout through `PYTHONPATH`; an
editable package installation is unnecessary. Complete dependency installation
before freezing any run. Each manifest records the Python version and
`pip freeze` output, and subsequent verification rejects runtime changes.

## Run offline checks

Both environment variables above are required for the official-scoring
integration tests. With the prepared sandbox linked, run:

```sh
python -m pytest experiments/chinatravel -q -ra
```

These tests use local fixtures or stub model responses and make no model API
calls. Inspect the skipped-test report: a skip does not establish a successful
integration check. Worker and scorer tests read `CHINATRAVEL_UPSTREAM`;
macOS sandbox tests also require the platform described above.

The existing core suite can be checked separately:

```sh
python -m pytest tests -q
```

## Freeze development before any live request

Use a fresh output directory for each cohort. The `freeze` command creates it;
do not create that exact directory beforehand. Freeze and verify do not call a
model:

```sh
python -m experiments.chinatravel.run freeze \
  --phase development \
  --output "$CT_RUNS/development-v1" \
  --data "$CHINATRAVEL_DATA_DIR" \
  --upstream "$CHINATRAVEL_UPSTREAM"

python -m experiments.chinatravel.run verify \
  --output "$CT_RUNS/development-v1" \
  --data "$CHINATRAVEL_DATA_DIR" \
  --upstream "$CHINATRAVEL_UPSTREAM"
```

Development always uses the two lexicographically first UIDs, across all three
arms. It is separate from the 12 formal UIDs. An output directory name such as
`development-v1` labels your local cohort; the actual settings and source hashes
in its manifest identify the implementation being run.

The freeze records source, protocol, data, dependency, and settings hashes plus
the execution schedule. **Do not modify frozen experiment source, the protocol,
settings, data, or installed dependencies while executing or scoring that
cohort.** Verification rejects such changes. If a development finding requires
an amendment, retain the old output and matching source snapshot, make the
change before formal evaluation, and freeze a new development directory. Do not
edit hashes to make an old cohort appear compatible.

## Run with a locally configured API key

Configure `DEEPSEEK_API_KEY` in the local shell environment using your normal
secret-management method. The parent reads it; the worker receives a scrubbed
environment and uses the parent broker. Do not paste a key into chat, source
files, this README, or run manifests.

Only `run --live` dispatches paid model requests:

```sh
python -m experiments.chinatravel.run run --live \
  --output "$CT_RUNS/development-v1" \
  --data "$CHINATRAVEL_DATA_DIR" \
  --upstream "$CHINATRAVEL_UPSTREAM"
```

This executes six development runs with the frozen settings. Requests, elapsed
time, usage, stop reasons, candidates, and decisions are recorded. SDK retries
are disabled. Completed job records are reused when the command is invoked
again; a partial job without a terminal result requires an explicit audit and
is not silently retried. Do not delete failed jobs to obtain replacement runs.

## Score and inspect the development cohort

After all six runs have terminal records, scoring is offline:

```sh
python -m experiments.chinatravel.run score \
  --output "$CT_RUNS/development-v1" \
  --data "$CHINATRAVEL_DATA_DIR" \
  --upstream "$CHINATRAVEL_UPSTREAM"
```

The command verifies the frozen inputs/runtime again and writes:

- `scored-results.json`: per-arm summaries and paired per-UID results;
- `official-evaluation.json`: official schema, common-sense, and logical
  diagnostics plus corpus metrics;
- `runs/<uid>/<arm>/result.json`: terminal status, delivered plan, and usage;
- `runs/<uid>/<arm>/requests/`: the parent request ledger;
- `runs/<uid>/<arm>/worker/`: native logs, candidates, translations, and gate
  traces where applicable.

Gold annotations are supplied only to this separate scorer. Failed, timed-out,
missing, or abstained outputs remain in the denominator. Native success and
local ResiMind acceptance are reported separately from official final success.

## Freeze and run the formal cohort

Finish development review first, then finalize the protocol and settings before
formal generation. Freeze a new folder; the formal UID list is selected by the
published SHA-256 rule and excludes both development UIDs:

```sh
python -m experiments.chinatravel.run freeze \
  --phase formal \
  --output "$CT_RUNS/formal-v1" \
  --data "$CHINATRAVEL_DATA_DIR" \
  --upstream "$CHINATRAVEL_UPSTREAM"

python -m experiments.chinatravel.run verify \
  --output "$CT_RUNS/formal-v1" \
  --data "$CHINATRAVEL_DATA_DIR" \
  --upstream "$CHINATRAVEL_UPSTREAM"

python -m experiments.chinatravel.run run --live \
  --output "$CT_RUNS/formal-v1" \
  --data "$CHINATRAVEL_DATA_DIR" \
  --upstream "$CHINATRAVEL_UPSTREAM"

python -m experiments.chinatravel.run score \
  --output "$CT_RUNS/formal-v1" \
  --data "$CHINATRAVEL_DATA_DIR" \
  --upstream "$CHINATRAVEL_UPSTREAM"
```

The formal cohort contains 36 runs: 12 tasks for each arm. Report final successes
out of 12, paired differences, failures/abstentions, and realized costs/time.
Call it a bounded 12-task pilot on original ChinaTravel tasks. Preparing the
154-query dataset does not establish full-154 evaluation, and results from this
maintained official release should not be presented as exact historical paper
reproduction. Do not change the frozen implementation or select replacement
UIDs after seeing formal outcomes.
