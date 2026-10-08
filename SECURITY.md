# Security model

The runtime is a library, not a sandbox. Proposers, verifiers, and domain adapters execute trusted Python code in the caller's process. It does not run strings through `eval` or `exec` and does not include a general code-execution tool.

Treat model output and retrieved text as untrusted candidates. Register trusted actions and independently validate schema, evidence scope, units, policy, and conclusions in a verifier. Do not route model-generated Python directly into callbacks.

Verification digests bind data but are not signatures. An attacker who controls the verifier or domain adapter controls the outcome. Source labels and reviewer names are application-supplied strings, not authenticated identities. Use authenticated ingestion/review services where required.

Traces may contain evidence, claims, facts, and reasons. The core does not send them over the network. When you explicitly use an optional model callback, task instructions, evidence, accepted facts, residuals, and the latest verifier feedback are sent to the selected model provider. Keep private inputs out of examples and configure credentials locally; traces must never contain API keys. Applications must choose suitable retention and access controls. Budget limits count completed attempts; they do not preempt a blocked callback. Apply timeouts outside the process for untrusted or unreliable integrations.

For a vulnerability report, provide a minimal synthetic reproducer without secrets or private records. A public issue is suitable for non-sensitive library bugs; use the repository's private vulnerability reporting feature when enabled. No dedicated security response service is promised for this experimental release.
