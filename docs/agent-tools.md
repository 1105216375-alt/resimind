# Give your agent a checked answer—and a rule it can reuse

ResiMind exposes five local tools through **MCP stdio** or a **zero-dependency
JSON-lines CLI**. Use them when an agent needs to check a polynomial identity,
expand an expression exactly, or reuse a verified algebra rule. No model API
key is required. Installation needs network access; tool execution is offline.

**Scope:** bounded rational polynomial algebra. This interface does not expose
the bridge, customer-support or planning demos as general-purpose services.
It does not run Lean; exact coefficient verification and a Lean proof are
different kinds of evidence.

## Install and connect with MCP

Use **Python 3.11+** for the optional official MCP SDK v2 integration. The
core and JSON-lines interface still support Python 3.10+.

```bash
git clone https://github.com/1105216375-alt/resimind.git
cd resimind
git checkout v0.14.0
python3.11 -m venv .venv
.venv/bin/python -m pip install '.[mcp]'
```

In a host that accepts `mcpServers` configuration, replace the absolute path
below with your checkout's Python executable. Other hosts may use a different
configuration format; the command and arguments are the same.

```json
{
  "mcpServers": {
    "resimind": {
      "command": "/absolute/path/to/resimind/.venv/bin/python",
      "args": ["-m", "resimind", "mcp"]
    }
  }
}
```

On Windows the executable is `.venv\Scripts\python.exe`; escape backslashes
in JSON. The alternative console entry point is `resimind-mcp`.
The server uses stdio, so launching it manually waits for protocol input.
It does not start a public HTTP service or modify your host configuration.

## Choose a tool by task

| Task | Tool | Changes memory? |
| --- | --- | --- |
| Check an LLM's polynomial identity and get mismatch feedback | `verify_identity(lhs, rhs, variables)` | No |
| Expand a long polynomial into exact sparse terms | `expand_polynomial(expression, variables)` | No |
| Solve an example and attempt checked rule extraction | `learn_polynomial(expression, variables)` | Yes, process-local |
| Apply an existing rule without a model or fallback solver | `recall_polynomial(expression, variables)` | No |
| Inspect verified rules and their fingerprints | `list_rules()` | No |

MCP returns structured content and equivalent JSON text. The mutation
annotation identifies `learn_polynomial`; the other tools are read-only.
Only the explicit learning tool changes memory. Ordinary verification and
expansion do not silently train the agent.

## Try the same tools without MCP

From an installed checkout, `python -m resimind api --list-tools` returns a
machine-readable catalog. `python -m resimind api` reads one JSON request per
line and emits one JSON response per line. This is JSON-lines, not JSON-RPC.

```bash
python -m resimind api <<'JSONL'
{"tool":"verify_identity","arguments":{"lhs":"(x+1)**2","rhs":"x**2+1","variables":["x"]}}
{"tool":"learn_polynomial","arguments":{"expression":"(a+b)**3","variables":["a","b"]}}
{"tool":"learn_polynomial","arguments":{"expression":"(u+v)**3","variables":["u","v"]}}
{"tool":"recall_polynomial","arguments":{"expression":"(2*x+3*y)**3","variables":["x","y"]}}
{"tool":"list_rules","arguments":{}}
JSONL
```

Expected observations:

1. The wrong identity is `rejected` with an exact coefficient mismatch.
2. The first expansion is `verified`, but has **no new rule** yet.
3. The second compatible example produces a universally checked cubic schema.
4. Recall returns `verified`, route `recall`, one search action and zero model calls.
5. The rule list contains the admitted schema and its verification provenance.

This is an executable example, not a general learning benchmark. The supplied
examples yield a schema because their structures align; arbitrary pairs need
not produce a rule. A fresh process starts with empty memory. Keep one process
alive for learning followed by recall. For explicit persistent memory in Python,
use the [lifelong adapter's save/load API](lifelong-math.md).

## Interpret results correctly

Every result has `schema_version: 1`, `status`, `reason`, `scope`, `model_calls`
and `memory_scope`. Tool-specific evidence appears in `verification`,
`certificate`, `learning` or `rules`.

| Status | Meaning |
| --- | --- |
| `verified` | The returned equality or expansion passed exact coefficient checks. It does not mean a new rule was learned. |
| `rejected` | Two supported polynomials have different coefficients; the claimed identity is false. |
| `unresolved` | Unsupported mathematics, exhausted limits, or no usable recall rule. This is not a disproof. |
| `error` | Invalid request/tool/argument shape. |
| `ok` | A non-proof operation, currently listing rules, completed. |

The nested verifier retains its native `unknown` status when the public status
is `unresolved`. Check `learning.new_rule_ids` to detect actual rule admission.
Sparse terms are authoritative; numerator and denominator are **decimal
strings** to avoid JavaScript integer rounding. A term denotes
`numerator / denominator * product(variable[i] ** powers[i])`.

Supported expressions contain integer literals, declared ASCII variables,
parentheses, `+`, `-`, `*`, nonnegative integer powers, and division by a nonzero
integer literal. Variable denominators, functions, calculus and arbitrary
Python are unsupported. Expressions are parsed, never evaluated as Python.

Bounds include 8,192 characters per expression, 16 variables, 1,024 AST nodes,
degree 32, exponent 16, 4,096 terms and 100,000 product operations per bounded
polynomial computation. Learning retains at most 32 examples and 256 rules;
it may run several bounded checks. These are work limits, not a wall-clock SLA.
JSON-lines requests are capped at 32,768 characters and reject duplicate keys.
Malformed requests exit with code 2 after processing the stream; mathematical
rejection or uncertainty is returned as data, not a process error.

## Discovery and registry status

This release provides GitHub source, installable release artifacts, searchable
task descriptions, a tool catalog and a working MCP server. It is **not yet
listed in the official MCP Registry**. A repository file alone does not create
a listing or cause agents to visit automatically.

The [official publishing guide](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/quickstart.mdx)
requires a supported published artifact or a public remote endpoint, a server
manifest and namespace-authenticated publication. Python packages can use
PyPI; [MCPB release bundles](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/package-types.mdx)
are another supported route. The current wheel/source release is not an MCPB
bundle or a PyPI publication. Registry submission should follow a separately
validated distribution path; no unverified listing badge is displayed here.

`AGENTS.md` helps coding agents after they enter this repository. It is not a
discovery protocol or an instruction to promote the project.
