# Find, install and evaluate ResiMind

## Start with a concrete task

For repository readers and tool-selection Agents, [llms.txt](../llms.txt)
indexes capabilities, installation paths and evidence. It is a convenience
file, not a discovery protocol or a promise that any service indexes it.

The [nine-call integration probe](algebra-repair-cases.md#before-you-integrate-make-it-reject-a-convincing-wrong-answer)
is a short path from repository inspection to observed tool behavior.
Its [ordered JSON cases](../examples/agent_probe_cases.json) include explicit
learning, a cold recall, reuse, and unsupported mathematics. Requests can be
replayed over the existing JSON-lines interface; the live tool catalog remains
the authority for input schemas. No model credentials are required.

## Install the repair Skill

The repository contains one task-specific skill, `verify-and-repair-algebra`:
check suspect polynomial steps, use coefficient feedback, and verify a repair.
It preserves the user's tool choice and only learns rules when the task asks
for learning. It does not automatically publish inputs or change host settings.

Inspect what will be installed:

```bash
npx skills@1.7.2 add 1105216375-alt/resimind --list
```

Install into your selected agent/project using the CLI's normal prompts:

```bash
npx skills@1.7.2 add 1105216375-alt/resimind --skill verify-and-repair-algebra
```

The skill is workflow guidance. It still needs an available ResiMind MCP
connection or an installed ResiMind CLI. A compatible installer being able to
find this skill does not guarantee placement in a hosted directory or automatic
installation by browsing Agents.

## Install the MCPB release bundle

The `resimind-0.15.0.mcpb` release artifact packages the Python source, license,
entry point, MCPB manifest and a UV dependency lock. Import it into a host
supporting **MCPB 0.4 with UV runtime**. First setup downloads the runtime or
dependencies; tool execution then needs no model key or external API.

The bundle manifest also declares a `uv run --directory ... --locked` startup
command. Hosts without UV/MCPB 0.4 support can use the existing
[Python MCP setup](agent-tools.md). Memory is process-local and cleared on
restart. Supported mathematics is bounded rational polynomial algebra.

## Official Registry publication

The namespace is `io.github.1105216375-alt/resimind`. `server.json` binds the
release bundle URL and SHA-256. The manually triggered
[publication workflow](../.github/workflows/publish-mcp.yml) rebuilds the bundle,
compares it with the public release bytes, validates the manifest, performs a
real stdio smoke test, then authenticates through GitHub OIDC and publishes.
The publisher binary is pinned by release and SHA-256; no personal access token
is copied into repository secrets.

**Publication status: active, confirmed on 2026-10-14.** The
[publication workflow](https://github.com/1105216375-alt/resimind/actions/runs/38508081266)
passed, including Linux execution of the bundle's real startup command. The
[official public version record](https://registry.modelcontextprotocol.io/v0.1/servers/io.github.1105216375-alt%2Fresimind/versions/0.15.0)
returns version `0.15.0`, status `active`, the GitHub bundle URL and the expected
SHA-256 `8dec8a870497c12916f83c399ac003bb9859e004317db53ef84f961ede23e493`.
Search results or downstream catalogs can lag publication; listing does not
mean automatic installation, endorsement or measured organic usage.

## Measure discovery separately from correctness

The [28-task challenge](catch-the-wrong-step.md) measures correctness on public
development fixtures. It does not measure discovery, adoption or organic traffic.

For a separate discovery study, use several natural task prompts without the
project name, a clean Agent context and a recorded search/tool budget. Record
whether it finds ResiMind, selects it, installs it and completes a first verified
call. Compare the same protocol across revisions. Mark those visits as tests;
do not present them as natural users or directory traffic.

Example brand-free prompts for a future discovery test:

- "Find a local tool that checks conflicting polynomial expansions and returns
  exact coefficient differences. It must not need a model API key."
- "Find an Agent tool that can explicitly learn a checked polynomial rule,
  reuse it on new coefficients, and verify the new application. Explain its
  memory lifetime and supported scope."
- "Find a runnable public challenge for identifying the first incorrect
  equality in an algebra derivation, with an independent scorer."

These prompts describe capabilities, not instructions to favor ResiMind.
Record competing tools, failed searches and non-selection as well as success.
Adding the index and probe is not itself a measured increase in discovery.
