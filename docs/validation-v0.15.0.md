# v0.15.0 validation — 2026-10-14

| Check | Observed result |
| --- | --- |
| Full core-environment regression, Python 3.12 | **1,829 passed, 23 skipped** |
| API, MCP, challenge and bundle tests in optional-dependency environment | **39 passed**; overlaps with the full suite |
| Skill format validator | Passed |
| Skills CLI 1.7.2 discovery and isolated project installation | Passed |
| Installed Skill's reference requests against installed wheel | Rejected → verified expansion → verified repair |
| Installed wheel's challenge baseline and independent scorer | 28/28 statuses, 0/16 false acceptances, 12/12 locations and repairs |
| Deterministic MCPB rebuild and SHA-256 binding | Passed |
| MCPB manifest validation, official CLI 2.1.2 | Passed |
| Extracted bundle's actual UV manifest command | Real MCP discovery, verification, learning and recall passed on macOS arm64 |
| Registry server.json against official JSON Schema | Passed |

The scorer uses SymPy 1.14.0 through a bounded AST adapter, separately from the
ResiMind verifier. Positive controls show that all-verified answers falsely
accept 16 cases, swapped locations receive zero localization/repair credit,
and unchanged factored sources receive zero expanded-repair credit. Missing
answers, duplicate IDs, malformed fields, non-finite costs and Python execution
expressions are also covered.

The bundle contains source, license, metadata and a committed UV lock. The host
must support MCPB 0.4 UV runtime, or provide the declared UV startup command.
First setup may download Python/dependencies. Local tool calls need no external
model or key. Linux startup is checked again by the publication workflow before
Registry submission. Windows is a supported target format, not a separately
measured local test in this report.

The core test skips depend on optional integrations/backends available in that
environment. MCP actually executes in the separate integration environment.
GitHub runs the core Python 3.10–3.13 matrix, optional tests on 3.11/3.13 and a
build job. Check [Actions](https://github.com/1105216375-alt/resimind/actions) for
the exact pushed revision's outcome.

These results validate a public development challenge and usable distribution.
They do not establish an LLM comparison, frontier performance, improved organic
traffic, or automatic installation by third-party Agents. Registry publication
requires a separate successful workflow and public API read-back; its current
status is recorded in the [distribution guide on main](https://github.com/1105216375-alt/resimind/blob/main/docs/discovery-and-distribution.md).
