# v0.14.0 validation — 2026-10-14

This is a reproducibility summary, not a frontier benchmark or a measure of
GitHub discovery traffic. Raw local evaluation logs are not published.

## Local checks

| Check | Result |
| --- | --- |
| Full regression, Python 3.12 core environment | **1,811 passed, 23 skipped** |
| JSON tools and optional MCP SDK 2.3.0 tests | **21 passed** (19 overlap with core tests; 2 exercise MCP) |
| Wheel and source distribution build | Passed |
| Wheel installed outside the source checkout | Passed |
| Installed `resimind-mcp`: discovery → learning → recall over real stdio | Passed |
| Installed offline lifelong demo | Passed, frozen memory unchanged |
| `git diff --check` | Passed |

The core environment skips optional/backend-dependent cases, including the two
MCP tests. The separate MCP environment actually launches a server subprocess,
lists all five tools, checks true/false/unsupported identities, reads an exact
expansion, learns a schema and verifies its application. It also checks malformed
requests, advertised input bounds and fresh-server memory isolation.

The JSON API tests cover coefficient mismatch feedback, unsupported expressions,
exact large integer serialization, explicit learning, read-only operations,
process isolation, duplicate fields, oversized lines and recovery after invalid
requests. The installed-wheel smoke test does not rely on `PYTHONPATH`.

## Automation and scope

The [test workflow](../.github/workflows/tests.yml) runs core tests on Python
3.10–3.13, MCP integration tests on Python 3.11 and 3.13, and a distribution
build. See [GitHub Actions](https://github.com/1105216375-alt/resimind/actions/workflows/tests.yml)
for the outcome of each pushed revision.

The MCP surface is bounded exact polynomial algebra and process-local verified
memory. It is not a general theorem prover, does not call a model and does not
expose the experimental formal-memory pilot. Unsupported mathematics returns
`unresolved`; it must not be reported as false or as proved.

The separately included [Lean formal-memory pilot](formal-memory-pilot.zh-CN.md)
has its own recorded experiment: increased direct recall coverage, **no measured
success-rate or cost advantage** over the strong fixed tactic on that small
development suite. No new external-agent ranking is claimed by this release.

GitHub release artifacts and the [connection guide](agent-tools.md) are the
distribution paths. Official MCP Registry listing and PyPI publication have not
been completed, and no increase in external-agent usage has yet been measured.
