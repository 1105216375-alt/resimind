# Optional Lean polynomial checks

Install **Lean 4.29.0** separately using the [official Lean installation guide](https://lean-lang.org/install/).
ResiMind does not install a toolchain, change the global Lean default, download
Mathlib, or send these checks to a model provider. The Python package remains
usable without Lean. A requested Lean check fails closed if Lean is missing,
the version differs, a tactic leaves goals, or a resource limit is reached.

```python
from resimind.integrations.lean import LeanPolynomialBackend

lean = LeanPolynomialBackend()  # or lean_path="/absolute/path/to/lean"
first = lean.check("(x+1)*(x+2)", "x*x+3*x+2", ("x",), tactic="rfl")
assert first.status == "unresolved"
print(first.goals)  # actual Lean goal, available to the next Agent proposal

second = lean.check("(x+1)*(x+2)", "x*x+3*x+2", ("x",), tactic="grind")
assert second.status == "verified"
assert first.binding_digest == second.binding_digest
```

The supported language is ResiMind's existing bounded rational polynomial
grammar, translated to Lean's `Rat` type (the rational numbers, ℚ). All
variables are renamed to generated identifiers. Only `rfl` and Lean's built-in
`grind` tactic are accepted. The latter includes a ring solver; this is **not**
Mathlib's `ring` or `ring_nf`. See the [official grind documentation](https://lean-lang.org/doc/reference/latest/The--grind--tactic/).

Each call generates a fixed theorem, captures its actual Lean goal, and checks
the exact theorem's axiom report. Only the standard `propext`,
`Classical.choice`, and `Quot.sound` axioms are approved. Incomplete Lean
declarations may contain `sorryAx` during compiler error recovery; they never
count as verified proofs. A successful result also includes a SHA-256 hash of
the compiled `.olean` artifact and of the generated source. These hashes bind
an audit record; the temporary artifact itself is not retained.

Default bounds are 10 seconds per compilation, 200,000 heartbeats, 2,048 MiB of
Lean memory, and 64 KiB of diagnostic output. Compiler output truncation,
missing proof artifacts, unexpected warnings, and unapproved axioms all fail
closed. The installed compiler and its bundled libraries are trusted.

The optional real-kernel tests run when Lean is installed:

```sh
python -m pytest tests/test_lean.py
```

This integration validates the generated formal equality. It does not verify
arbitrary natural-language translations, provide arbitrary theorem search,
or accept model-written Lean programs.
