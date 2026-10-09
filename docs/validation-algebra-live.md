# Algebra feedback and live-study validation — 2026-10-09

This records the main-branch development update, not a new version tag or PyPI release. Package metadata remains `0.7.0` pending a versioned release.

| Check | Result |
|---|---|
| Complete source test suite, including installed optional integration dependencies | **1,048 passed** |
| Built wheel installed into a separate package directory, source import path removed | **1,048 passed** |
| Offline examples: linear mathematics, axial engineering, constrained optimization, continuous bridge, customer support, open planning and knowledge growth | **7 passed** |
| v1 live study | 83 unique completed requests; 43 task runs; 72,493 reported tokens |
| v2 development rerun | 78 unique completed requests; 43 task runs; 70,606 reported tokens |
| Independent offline replay of archived requests | All 43 trajectories in each round reproduced, including rule admission and frozen-library hashes |
| Source/request hashes, physical request budgets, final independent scoring and token accounting | Matched both rounds |
| Git diff whitespace checks, local documentation links, SVG rendering | Passed |

The new tests cover whitespace-tolerant structural rule matching without false provenance, exact coefficient diagnostics and preserved rejection codes, invalid certificates, stuck false proposals, matching controller stop policies, independent scoring boundaries, token-usage unknowns, concurrent request budgets, failed/in-flight replay protection and frozen-manifest checks.

The first wheel build attempt without build isolation could not find `setuptools` in the temporary integration environment. The normal isolated build then downloaded its declared build tools, built successfully and passed the installed-package checks. No runtime dependency was added.

The v1 implementation is preserved in commit `37bbf13`; its 29 evaluated source files match the published manifest hashes. Both rounds return the service identifier `deepseek-flash`; no immutable provider snapshot is asserted. The v2 experiment is labelled development on already inspected tasks. Full new prompts, responses, intermediate traces and credentials are not published.

See [the complete method and both results](algebra-live-evaluation.md), [中文解读](algebra-live-evaluation.zh-CN.md), and the [earlier knowledge-growth validation](validation-knowledge-growth.md) for the preceding package and ChinaTravel checks.
