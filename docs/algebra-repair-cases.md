# Exact algebra checks for Agent failures

These three cases use the existing [MCP or JSON tool interface](agent-tools.md).
Installation requires network access; calls run locally without a model key.

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
