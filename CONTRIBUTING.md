# Contributing

Use Python 3.10+ and install `python -m pip install -e '.[dev]'`. Run `python -m pytest -q` and the six examples before submitting a change.

Keep runtime dependencies minimal. Add domain adapters as self-contained examples with synthetic data. Document which code and evidence the adapter trusts. A proposer must never bypass a verifier, and a verifier's accepted facts must remain bound to the exact candidate and state being committed.

Regression tests should exercise incorrect claims, missing or mismatched evidence, failed checks, and incomplete residuals, as well as successful runs. Do not submit credentials, production traces, private records, or licensed third-party documents as fixtures.

Describe the behavior changed and the checks run in your pull request. Submissions are contributed under the repository's MIT license.

## Verifier counterexamples

Useful contributions include supported false claims accepted as verified,
source/certificate binding errors, rule applications that lose conditions,
and resource-bound violations. An unsupported expression returning unresolved
is expected behavior. There is no reward or bounty promised by this invitation.

For a reproducible report, include the installed version or commit, Python
version, smallest synthetic JSON request (or ordered requests for memory),
actual status/evidence, expected behavior and an independent calculation or
proof of the discrepancy. Include a failing regression test in a proposed fix.
Do not include private prompts, production inputs, keys or tokens.

The public [integration probe](examples/agent_probe.py) provides a starting
format. Report only after reproducing; retain unresolved cases in results.
Follow [SECURITY.md](SECURITY.md) for security-sensitive disclosures.
