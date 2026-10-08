# Contributing

Use Python 3.10+ and install `python -m pip install -e '.[dev]'`. Run `python -m pytest -q` and the four examples before submitting a change.

Keep runtime dependencies minimal. Add domain adapters as self-contained examples with synthetic data. Document which code and evidence the adapter trusts. A proposer must never bypass a verifier, and a verifier's accepted facts must remain bound to the exact candidate and state being committed.

Regression tests should exercise incorrect claims, missing or mismatched evidence, failed checks, and incomplete residuals, as well as successful runs. Do not submit credentials, production traces, private records, or licensed third-party documents as fixtures.

Describe the behavior changed and the checks run in your pull request. Submissions are contributed under the repository's MIT license.
