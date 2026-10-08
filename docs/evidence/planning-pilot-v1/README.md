# Paired planning pilot v1 — frozen before live evaluation

**Observed result:** all three arms completed 8/8 feasible cases. Direct generation delivered one uncertifiable plan; self-review and ResiMind delivered none. Logical calls were 12 / 30 / 13. Self-review uses two fixed review rounds while ResiMind stops on acceptance, so the call difference reflects these policies. This small synthetic pilot does not establish general superiority.

[中文评价与下一步建议](../../evaluation-summary.zh-CN.md) · [完整统计](REPORT.zh-CN.md) · [Frozen protocol](../../evaluation-protocol.md)

The twelve cases, implementation, oracle, prompts and settings were frozen in [manifest.json](manifest.json) before any live request. Model outputs did not determine case selection or the scoring rules. The [digest](manifest.sha256) covers the manifest's canonical JSON; it is not the byte hash of its indented display file.

- [scored-results.json](scored-results.json): complete outcomes, independent scoring, logical and physical accounting.
- `requests/`: all 31 real requests, final response text, usage, timing and prompt hashes; no credentials or hidden reasoning fields.
- `runs/`: all twelve paired runs and the ResiMind state/verification traces.
- [execution-time.json](execution-time.json): timestamps and wall time for the live execution phase.

The first response for each case is physically requested once and attributed to each arm. Reported physical tokens are **144,466 input + 73,479 output**; logical arm totals intentionally count their shared first request separately. All requests completed without transport, truncation or format failure. Pricing is a peak-list estimate with all input treated as cache misses, not an invoice.

In case 12, the first response gave a definite total despite unknown return-route fare. Its independent score is `unknown`, not proof that a real-world fare is nonzero. ResiMind deferred the candidate without committing facts; the next model response was `null`. Self-review returned `null` on its second review. Withholding is not a completed itinerary or a proof of infeasibility.

## Recheck offline

From the repository checkout with ResiMind installed:

```bash
PYTHONPATH=src:. python -m benchmarks.run_planning_eval replay --output docs/evidence/planning-pilot-v1
```

Replay uses archived responses, sends no requests, and leaves recorded observations unchanged. It checks frozen source hashes and matches the Agent's complete traces, final outputs and request identities for all twelve cases. Recompute statistics with the following command; it writes the generated score/report files and sends no requests:

```bash
PYTHONPATH=src:. python -m benchmarks.run_planning_eval summarize --output docs/evidence/planning-pilot-v1
```

For a **new** live replication, use a fresh output directory and the optional DeepSeek dependencies. Keep `DEEPSEEK_API_KEY` in the local environment. Inspect [the protocol](../../evaluation-protocol.md) and its request ceiling before opting into billable requests:

```bash
python -m pip install -e '.[deepseek]'
PYTHONPATH=src:. python -m benchmarks.run_planning_eval freeze --output /tmp/resimind-new-pilot
PYTHONPATH=src:. python -m benchmarks.run_planning_eval run --live --output /tmp/resimind-new-pilot
```

A fresh replication may produce different answers. The model alias and provider defaults are not immutable model weights or a deterministic seed. Preserve this pilot's observations when running a subsequent experiment.
