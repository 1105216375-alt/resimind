# ChinaTravel repair development evidence

This collection retains both development rounds, including the discovery and
repair of an overnight-state gap. It is not a new formal leaderboard result.
Read the [full interpretation](../../repair-development.zh-CN.md) and
[reproduction instructions](../../../experiments/chinatravel_repair/README.md).

| Artifact | Scope |
| --- | --- |
| [Initial paired replay](replay/report.json) | All 28 recorded v1 candidates; no new model calls; before the overnight guard. |
| [Initial live development scores](live-development/scored-results.json) | Two original development tasks, 209 DeepSeek calls; before the overnight guard. |
| [Initial audit](audit-before-overnight-guard.json) | Hashes, transactions, request accounting, raw-versus-repaired candidate scoring. |
| [Overnight regression](overnight-regression.json) | The earlier officially passing plan is withheld by the new guard. |
| [Guarded paired replay](guarded-replay/report.json) | All same 28 candidates, with the new overnight requirement. |
| [Guarded live development scores](guarded-live-development/scored-results.json) | A new run of the same two development tasks; failures stay in the denominator. |
| [Guarded audit](audit-after-overnight-guard.json) | Full source/data/record verification and independent post-run scoring. |
| [Final schema refinement](schema-refinement/report.json) | Current checker on all five guarded-round candidates; 0 new API calls, schema 0→3/5, full success 0→0/5. |

## Complete records

The lossless archives contain inputs, model requests/responses, recorded tools,
raw candidates, each proposed/committed/rejected patch, terminal records, scoring,
manifests and a `source-at-freeze.zip`. Reopen the source snapshot to recover the
exact runtime used in that round; current code includes later amendments.

- [Initial replay archive](replay.tar.xz) and [member hashes](replay/member-hashes.json).
- [Initial live archive](live-development.tar.xz) and [member hashes](live-development/member-hashes.json).
- [Guarded replay archive](guarded-replay.tar.xz) and [member hashes](guarded-replay/member-hashes.json).
- [Guarded live archive](guarded-live-development.tar.xz) and [member hashes](guarded-live-development/member-hashes.json).
- [Schema refinement archive](schema-refinement.tar.xz) and [member hashes](schema-refinement/member-hashes.json).
- [Archive sizes and SHA-256 hashes](archive-index.json).

Each archive uses its label as the top-level directory. Extract into a separate
work directory with `tar -xJf ARCHIVE.tar.xz -C WORKDIR`. No API credential is
included. The source snapshots additionally retain `experiments/__init__.py`,
which was not in the initial round's source inventory; the guarded round hashes
it directly. Hashes bind records to bytes; they do not prove correctness of the
data, verifier, or natural-language interpretation.

After installing the pinned official runtime/data described in the experiment
README, audit the guarded archives with:

```sh
PYTHONPATH=src:. python scripts/audit_repair_development.py \
  --replay WORKDIR/guarded-replay --live WORKDIR/guarded-live-development \
  --upstream "$CHINATRAVEL_UPSTREAM" --use-archived-sources --output WORKDIR/audit.json
```

Use `--use-archived-sources` explicitly when checking either live round with the
current working tree, which contains the later schema-check amendment. It verifies every snapshot source against its frozen
manifest and reports working-tree differences; it does not silently disable
source checks or execute archived code. The optional `--historical` argument
can point to the earlier ChinaTravel development-v2 directory. Gold is used
only in this post-run audit/scoring stage, never in either solving process.

All tasks and sandbox-derived artifacts retain **CC BY 4.0** attribution to
Jie-Jing Shao and the ChinaTravel authors. Official code is MIT, pinned to
`f445e0011f42594fcc29b8c752ece06ded9a8218`. See
[the original protocol](../../../experiments/chinatravel/PROTOCOL.md) for source
URLs, data revisions and licenses. These are sandbox itineraries, not actual
travel bookings or a proof that every real-world requirement was modeled.
