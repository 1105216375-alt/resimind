# Knowledge-growth development validation — 2026-10-09

This validates the development branch after v0.7.0, not a new tagged release.
The package retains its existing version until the next release.

| Check | Result |
| --- | --- |
| Complete source suite, including optional integrations | 994 passed |
| Built wheel installed into an isolated package directory; source import path removed | Same 994 passed |
| Existing ChinaTravel, repair and search integration regressions | 219 passed; 3 skipped because optional pinned upstream/data fixtures were not configured |
| Installed-package knowledge-growth CLI, from outside the repository | Completed discovery, persistence/reload and both transfer arms |
| Controlled knowledge-growth study | Both arms 10/10 independently checked; transfer proposals 84→24; discovery cost 13 proposals |

The existing macOS sandbox regression initially could not bind its loopback
test socket under the outer execution sandbox. It passed when rerun with that
local permission. This was an environment restriction; no test or protection
was disabled to produce the result.

The new tests cover full symbolic proof chains, explicit nonzero conditions,
unknown outcomes, false coefficients, fixed-sample overfitting, unsupported
expressions, provenance binding, mutation isolation, persistence tampering,
reload verification, revocation, actual Agent reuse, failed-macro fallback,
and an independently implemented exact degree-grid scorer.

Reproduce the checks in an appropriate development environment:

```bash
python -m pip install -e '.[dev,deepseek,langgraph]'
python -m pytest -q
python -m benchmarks.knowledge_growth --output /tmp/knowledge-growth-summary.json
python -m resimind demo --domain knowledge-growth --json
```

The full study summary and method are linked from the
[knowledge-growth guide](knowledge-growth.md). No new model calls were used in
this development evaluation. The source Desktop project was not modified or
uploaded; this implementation adds bounded, independently verified components
to the public repository.
