# Can your Agent catch the wrong step?

**Find the first wrong equality. Repair it. Show the check.**

An open, runnable development challenge for any Agent or symbolic tool:
28 short polynomial derivations, structured answers and an independent SymPy
scorer. Run locally; no sign-up, API key or automatic result upload.

This is a small public challenge designed to test integration and correctness.
It is not a hidden benchmark, an external Agent leaderboard, a knowledge-growth
experiment or evidence that ResiMind beats a strong computer algebra system.

## Run the tool baseline in one minute

From the repository checkout:

```bash
python -m pip install '.[challenge]'
python -m resimind challenge export > tasks.json
python -m resimind challenge baseline > baseline-answers.json
python -m resimind challenge score baseline-answers.json
```

`export` and `baseline` use the dependency-free core. Only `score` requires
SymPy. The scorer constructs exact SymPy polynomials through an allow-listed,
bounded AST parser; it never evaluates or passes untrusted text to `sympify`.
It does not import ResiMind's exact verifier to decide correctness.

## Bring another Agent

Give it `tasks.json` and the following task, without the baseline answers:

> For every derivation, determine whether every adjacent equality is valid.
> Report the first incorrect transition, using 1 for steps[0] → steps[1].
> If a transition exceeds the supported rational-polynomial scope before a
> disproof is established, return unresolved. For rejected derivations, supply
> an expanded polynomial equal to the original steps[0]. Return a JSON array
> in the schema below. Use the same declared tool access and budget on all tasks.

```json
[
  {
    "id": "mutation-00",
    "status": "rejected",
    "first_error": 1,
    "repair": "x**2+2*x+1",
    "model_calls": null,
    "elapsed_seconds": null
  }
]
```

Save its complete array to `my-answers.json` and run:

```bash
python -m resimind challenge score my-answers.json
```

`status` is `verified`, `rejected` or `unresolved`. For verified or unresolved
answers, `first_error` and `repair` must be null. `model_calls` and
`elapsed_seconds` are optional, self-reported costs; the scorer neither trusts
nor aggregates them. Missing answers receive no credit. Duplicate/unknown IDs
or malformed answer shapes fail validation instead of silently improving a score.

## What is scored?

| Measure | Denominator | Purpose |
| --- | ---: | --- |
| Correct status | 28 | Distinguish valid, false and unsupported work |
| False acceptance | 16 non-verified derivations | Detect an Agent certifying a false or unsupported chain |
| Correct first-error location | 12 rejected derivations | Catch the earliest error, even if a later line repairs it |
| Verified expanded repair | 12 rejected derivations | Require correct location and an expanded repair equivalent to the original source |

The suite contains 12 correct derivations, 12 coefficient mutations and 4
unsupported-scope controls. Families include shifted squares, bivariate cubes
and rational differences of squares. The public task generator contains the
construction; there is no claim that its answers are hidden from a coding Agent.

The scorer's deliberately small grammar accepts integer coefficients, declared
variables, `+`, `-`, `*`, powers 0–6 and literal integer denominators; total
degree is at most 6. Repairs are limited to 2,048 characters and 256 AST nodes.
Functions and variable denominators are out of scope. A factored copy of the
original is not an expanded repair. These grading bounds are narrower than
the main ResiMind tool bounds and are part of the challenge protocol.

## Recorded tool baseline

On 2026-10-14, the deterministic ResiMind tools scored **28/28 statuses,
0/16 false acceptances, 12/12 error locations and 12/12 expanded repairs**,
with zero model calls. This confirms that the fixtures, tool path and separate
scorer agree. It does not measure how an LLM behaves or claim a benefit over SymPy.

Positive-control tests intentionally submit all-verified answers, reverse the
reported error locations, copy factored sources as repairs, omit answers and
submit malformed expressions. The scorer rejects or penalizes each case.

For a meaningful external comparison, freeze task and code revisions, disclose
model/version, prompts, tool access, retries, cost and full failures, and keep
the same conditions for each system. A blind discovery test is separate: it
must not name ResiMind in the discovery prompt or count its test visits as
organic traffic. No such external comparison or natural-traffic result is
claimed here yet.

[Use the repair Skill](../skills/verify-and-repair-algebra/SKILL.md) ·
[Connect the MCP tools](agent-tools.md) · [Practical cases](algebra-repair-cases.md)
