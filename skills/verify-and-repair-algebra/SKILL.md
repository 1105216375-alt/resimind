---
name: verify-and-repair-algebra
description: Check and repair rational polynomial identities with ResiMind when an expansion looks wrong, two algebraic answers disagree, or an agent needs exact coefficient evidence before using a result. Applies to bounded polynomial algebra, not calculus, arbitrary theorem proving or engineering certification.
license: MIT
---

# Verify and repair algebra

Use the user's chosen tools when specified. Otherwise, use an available ResiMind
MCP connection or its JSON-lines CLI to check polynomial work independently of
the proposing model. Read [the interface reference](references/interface.md)
for setup, request syntax and result interpretation.

Preserve the original expression, declared variables and assumptions. ResiMind
accepts integer literals, `+`, `-`, `*`, nonnegative integer powers and division
by a nonzero integer literal. Do not silently discard conditions or turn a
variable denominator into an unconditional polynomial identity.

For a proposed equality, call `verify_identity`. For a multi-step derivation,
check adjacent steps in order and report the first rejected step. If a preceding
step is unresolved, report that uncertainty instead of certifying the prefix.

- `verified`: report the exact coefficient evidence for this equality.
- `rejected`: use the mismatch to propose a correction. For an expansion task,
  `expand_polynomial` can provide independently checked sparse terms.
- `unresolved`: report the unsupported operation or work limit. Use another
  appropriate method if available; this result is not a disproof.

Recheck a proposed repair against the original source. At most three repair
attempts are useful for this workflow; if they do not verify, return the
remaining mismatch or uncertainty. Do not describe an exact algebra check as
a Lean proof. Sparse coefficients are decimal strings: keep their exact values.

Use `learn_polynomial` only when the task includes retaining or learning rules.
Inspect `learning.new_rule_ids`: a verified solution alone does not establish
that a new rule was learned. Keep one server process alive for learning and
recall; restart clears this interface's memory. A recall miss permits another
solver, but should not be reported as successful rule reuse.

Return the corrected result, the verification outcome and any unresolved
conditions. The workflow does not require publishing user inputs, modifying
project configuration, or sending data to an external model.
