# ResiMind interface

Prefer an existing MCP connection exposing `verify_identity`,
`expand_polynomial`, `learn_polynomial`, `recall_polynomial` and `list_rules`.
Otherwise use an installed `resimind` command. Its core runs on Python 3.10+;
MCP requires Python 3.11+ and the optional SDK.

If installation is part of the user's task, install into an appropriate isolated
environment from the versioned release source, not an assumed PyPI package:

```bash
python -m pip install 'https://github.com/1105216375-alt/resimind/archive/refs/tags/v0.16.0.tar.gz'
```

Call the CLI with one JSON object per line on stdin:

```bash
python -m resimind api <<'JSONL'
{"tool":"verify_identity","arguments":{"lhs":"(x+1)**2","rhs":"x**2+1","variables":["x"]}}
{"tool":"expand_polynomial","arguments":{"expression":"(x+1)**2","variables":["x"]}}
{"tool":"verify_identity","arguments":{"lhs":"(x+1)**2","rhs":"x**2+2*x+1","variables":["x"]}}
JSONL
```

The first result is `rejected`, with the missing `x` coefficient. The expansion
returns exact sparse terms. The third result is `verified`. An external MCP
host passes the same `arguments` object to the corresponding named tool.

Each response includes `schema_version`, `status`, `reason`, `scope` and
`model_calls`. `error` is a malformed request, `ok` is a non-proof result, and
`unresolved` must not be interpreted as false. The nested verifier may use
`unknown` for the same uncertainty.

Use `python -m resimind api --list-tools` for the current JSON schema. Full
[connection instructions](https://github.com/1105216375-alt/resimind/blob/v0.16.0/docs/agent-tools.md)
are available if MCP setup is actually needed.
