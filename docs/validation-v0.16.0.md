# v0.16.0 release validation

This release adds bounded conditional-lemma composition to the optional typed Lean
integration. The change does not alter the MCP tool surface.

## Checks run locally

- `python -m py_compile src/resimind/integrations/formal.py`
- `PYTHONPATH=src python -m pytest -q`: **1,831 passed, 23 skipped**
- `PYTHONPATH=src python -m pytest -q tests/test_formal.py`: **25 passed** with Lean 4.29.0
- MCP/API/bundle subset: **20 passed, 2 skipped**
- Deterministic MCPB rebuild: `server.json` matches; v0.16.0 MCPB SHA-256 is
  `5270994576b4a12467ce826cd7d678d4865c700df856e9b955b66f771979814d`.
- Composition pilot: strong `grind` empty memory `verified`; weak `simp` empty memory
  `unresolved`; weak `simp` with the two checked lemmas `verified`; strong `grind` with
  the composition planner `verified`.

The composition chain is a hand-authored development fixture. It demonstrates that a
complete `intro → apply → exact` tree is kernel-checked; it is not an external benchmark,
an Agent leaderboard, or evidence that ResiMind exceeds current theorem-proving systems.
The full Lean/Mathlib external protocol remains an open research task.
