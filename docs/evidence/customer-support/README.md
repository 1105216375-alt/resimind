# Live customer-support smoke run — 2026-10-08

One live `deepseek-flash` run was made while preparing v0.6.0, using the repository's synthetic `refund` case. [The complete candidate and verification audit](deepseek-flash-refund.json) is retained, including rejected proposals. No customer records, credentials, provider reasoning traces, or payment actions are included.

| Attempt | Decision | Reason |
| --- | --- | --- |
| 1 | Accept | Return eligibility checked against the supplied order and fictional policy |
| 2 | Reject | Missing required snapshot references |
| 3 | Reject | Missing required snapshot references |
| 4 | Accept | Refund amount checked independently |
| 5 | Accept | Structured recommendation checked against committed facts |

The final recommendation is **CNY 249.00**, with CNY 10.00 shipping excluded and `payment_executed=false`. The model received real verifier feedback after each attempt. No scripted mistake or offline fallback was used in this live run. This is one smoke test, not an accuracy or safety benchmark; subsequent runs can remain unresolved.

The configured model was `deepseek-flash`; provider response model IDs were not separately recorded. Reported usage was **16,987 input tokens, 5,884 output tokens, 22,871 total**, over five API calls. Output usage can include provider reasoning tokens not present in the audit. The request budget was eight calls, with a per-response output limit of 4,096 tokens and a 90-second SDK network timeout. No temperature or thinking-mode override was supplied.

From the checkout, install `.[deepseek]`, set `DEEPSEEK_API_KEY` locally, and run:

```bash
python -m examples.customer_support --live --model deepseek-flash \
  --scenario refund --max-calls 8 --max-output-tokens 4096 --timeout 90 --json
```

This makes billable requests and does not replay the saved answers. To recheck the archived candidates without network access instead:

```bash
python tools/replay_live_audits.py
```

The offline replay compares the complete run result against a fresh verification of the same candidates. It validates the recorded certificate/rule decisions, not the origin of the model output. Later checks rejecting extra forged state facts do not change this valid fresh-state trace. The audit is a developer-generated record, not a third-party attestation.
