# Find, install and evaluate ResiMind

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

**Publication status:** pending the first successful workflow and public API
read-back. A checked-in manifest alone is not a Registry listing.

## Measure discovery separately from correctness

The [28-task challenge](catch-the-wrong-step.md) measures correctness on public
development fixtures. It does not measure discovery, adoption or organic traffic.

For a separate discovery study, use several natural task prompts without the
project name, a clean Agent context and a recorded search/tool budget. Record
whether it finds ResiMind, selects it, installs it and completes a first verified
call. Compare the same protocol across revisions. Mark those visits as tests;
do not present them as natural users or directory traffic.
