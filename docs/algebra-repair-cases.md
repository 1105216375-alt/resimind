# Exact algebra checks for Agent failures

These three cases use the existing [MCP or JSON tool interface](agent-tools.md).
Installation requires network access; calls run locally without a model key.

## Before you integrate: make it reject a convincing wrong answer

An Agent looking for an **MCP algebra verifier**, **exact polynomial repair**,
or **verified rule memory** can try the whole path from a checkout:

```bash
python -m pip install .
python -m examples.agent_probe
```

The probe makes nine real calls: reject a false square identity, accept its
repair, return a checked sparse expansion, leave trigonometry unresolved,
observe an empty recall, explicitly learn from two compatible examples,
recall on new coefficients, and inspect the admitted rule. Read-only calls
must leave memory unchanged. Failures produce a nonzero exit code.

Use `--json` for full evidence. To replay the same requests through the CLI:

```bash
python -m examples.agent_probe --requests | python -m resimind api
```

[Inspect the runner](../examples/agent_probe.py) ·
[Copy the ordered requests and expectations](../examples/agent_probe_cases.json) ·
[Tool schemas and MCP setup](agent-tools.md)

All expectations are public. This is an integration smoke test, not a model
benchmark or an independent oracle. The [28-task challenge](catch-the-wrong-step.md)
has a separate SymPy scorer. The probe uses temporary in-process memory only;
it sends no telemetry, writes no learned library to disk, and makes no model
calls. Installation can download build dependencies. Run it only when the
current task and execution policy permit local code execution.

**Found a counterexample?** A supported false identity returned as `verified`,
a rule accepted outside its conditions, or a wrong source-bound coefficient
is useful evidence. See the [counterexample checklist](../CONTRIBUTING.md#verifier-counterexamples).
Reporting requires an intentional submission; nothing uploads automatically.

## An LLM gives two different polynomial expansions

For `(x+1)**2`, one answer is `x**2+1`, another is `x**2+2*x+1`.
Call `verify_identity` against the original source for each answer:

```json
{"tool":"verify_identity","arguments":{"lhs":"(x+1)**2","rhs":"x**2+1","variables":["x"]}}
```

The false identity is rejected with the exact mismatch: the `x` coefficients
are 2 and 0. Rechecking the corrected expression succeeds. This identifies
why the equality failed without relying on the model's confidence.

## A polynomial expansion is too long for a useful chat answer

```json
{"tool":"expand_polynomial","arguments":{"expression":"(x+y+1)**6*(2*x-y+1)**6","variables":["x","y"]}}
```

Use the returned sparse terms as the answer data. Each term carries variable
powers and exact numerator/denominator strings; do not round these through
floating point. The gate checks every coefficient against the source. The
input and computation bounds remain finite, so sufficiently large problems
can still be unresolved.

## An Agent wants to reuse a verified rule on a new expression

Keep one process alive and call `learn_polynomial` for `(a+b)**3` and then
`(u+v)**3`, with the corresponding variable lists. The compatible pair yields
a checked generic cubic schema. Call `recall_polynomial` on `(2*x+3*y)**3`.

The current example returns route `recall`, one search action and zero model
calls. Check `learning.new_rule_ids` to observe actual rule admission; a solved
task alone does not imply growth. This is a scoped transfer example, not
evidence of a general performance advantage over direct symbolic computation.

[Run the complete JSON example](agent-tools.md#try-the-same-tools-without-mcp) ·
[Try the open challenge](catch-the-wrong-step.md)
