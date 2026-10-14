# Working on ResiMind

ResiMind is a neuro-symbolic Agent architecture, not a foundation model.
Read README.md for its scope and docs/agent-tools.md for callable tools.

- Source: `src/resimind`; tests: `tests`; reproducible experiments: `benchmarks`.
- Core install/test: `python -m pip install -e '.[dev]'`, then `python -m pytest -q`.
- MCP integration tests require Python 3.11+ and `python -m pip install -e '.[dev,mcp]'`.
- Offline smoke: `python -m resimind demo --domain lifelong --json`.
- Keep the core dependency-free. Optional integrations must not import their SDKs on core execution paths.
- Verification gates authorize facts; models, cached matches and routing heuristics do not.
- Preserve bounded work, explicit learning, revalidation on reload and rechecking on reuse.
- Distinguish exact algebra certificates, Lean proofs and empirical benchmark results.
- Use synthetic test data. Keep credentials, private application data and raw local evaluation logs out of the public repository.
- Summarize reproducible results and limitations; do not claim frontier performance from development fixtures.
