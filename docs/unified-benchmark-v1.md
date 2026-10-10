# Unified offline verification benchmark v1

This benchmark measures executable verification, bounded task completion and
rule reuse in ResiMind **v0.12.1**. It is an offline development study, with no
neural model or Lean gate enabled. It does not rank ResiMind against other Agent
frameworks. Results below were reproduced on 2026-10-10.

## Reproduce from the source checkout

```bash
python -m pip install -e .
python -m benchmarks.run_unified_verification_benchmark --output /tmp/resimind-unified-v1
```

The runner writes the complete synthetic input manifest, compact summary and
per-case measurements to the requested directory. It makes no network requests.
It leaves the production defaults unchanged. The manifest hash is:

```text
69912ad87314ef55347d9bb3a9b2aa0cf7929bb71078d5d24a53c90a1e8619c1
```

`--cases-per-domain N` runs a smaller prefix (1–50); its manifest hash differs.
Discovery, stress and missing-evidence tracks retain their fixed sizes. The
manifest is generated deterministically from the public protocol. These cases
are development fixtures, not a prospectively held-out benchmark.

## Completion and verification

| Domain | Synthetic parameter variants | Independently scored completions | Mean attempts / accepted steps | Specified mutations rejected |
| --- | ---: | ---: | ---: | ---: |
| Rational polynomial expansion | 50 | 50/50 | 3.62 / 3.62 | 150/150 |
| Two-span continuous line beam | 50 | 50/50 | 12 / 11 | 150/150 |
| Fictional return policy | 50 | 50/50 | 3.10 / 2.60 | 150/150 |

All 150 unmodified positive controls were accepted. No mutated candidate was
accepted in this track. Separately, **20/20** missing-evidence cases (10 bridge,
10 support) retained unresolved obligations and committed no facts. All tracks
made **zero model calls**.

The 50 mathematics inputs cover five expression families; the 50 bridge inputs
vary one physical model, each with two combinations and four live-load patterns;
the 50 support inputs vary ten configurations including window boundaries,
identity mismatch, nonreturnable items and prior refunds. They are not 150
independent task families.

Final answers are scored separately from the production verifier:

- Mathematics uses exact evaluation on a complete degree-bounded grid, with an
  independent syntax check for expansion. An exhausted scorer returns unknown.
- Bridge scoring solves a three-rotation stiffness system with exact rational
  elimination, then checks all case certificates, envelopes and comparisons.
  This cross-checks the implementation of the same declared physics, not the
  validity of a real bridge model or compliance with design standards.
- Support scoring calculates the fictional policy independently and checks the
  entire committed recommendation, including execution flags and requested IDs.

Each completed case receives three declared mutations: a nonzero polynomial
error; altered bridge moment, reaction or displacement coefficient; or altered
support execution flag, refund amount or customer ID. These results concern
those mutations, not a general attack resistance rate. Mutations skipped due to
an unfinished task are not counted as rejected. Positive controls prevent an
always-rejecting checker from looking successful.

The bridge and support proposers are scripted solvers that first make a
specified error, then respond to its rejection. Their results demonstrate the
implemented checking and feedback path, not spontaneous model self-correction.

## Paired rule reuse

Five separate discovery tasks expand `(u+v)**n` for n=2…6. They admit five
verified rules using **12 accepted steps** and no model calls. The library is
saved, reloaded with verification, and frozen during all 50 transfer tasks.
Its serialized snapshot is unchanged after the transfer run.

| Same 50 mathematics inputs | Empty library | Discovered library |
| --- | ---: | ---: |
| Correct completions | 50/50 | 50/50 |
| Total accepted steps | 181 | 121 |
| Mean accepted steps | 3.62 | 2.42 |
| p95 attempts (nearest rank) | 8 | 5 |
| Production identity checks | 1,072 | 512 |
| Recorded rule matching attempts | 0 | 22,737 |
| Model calls | 0 | 0 |

**24 cases use fewer steps, 26 tie, none use more.** There are 26 accepted rule
applications. The mean step reduction is **33.1%**, excluding the explicitly
reported discovery cost. This is not a 33.1% latency or total-compute saving:
rule lookup and matching add work, and step counts do not measure that cost.
The measurements also retain scheduling preview work and execution counters.
The discovery rules were chosen for these development families; this is a
controlled transfer demonstration, not evidence of unseen-domain learning.

## Complex-expression pressure track

The same eight fixed expressions run with an empty library and a normal local
work bound of 4,096, first without a reserve and then with a cumulative reserve
of 4,096. Both arms allow 64 controller steps. The reserve is a dimensionless
syntax-work estimate, not a token or wall-clock limit.

| Expression | Reserve 0 | Reserve 4,096 | Reserve actually spent |
| --- | --- | --- | ---: |
| `(x+y)**12` | Unfinished | Complete | 3 |
| `(x+y)**8*(2*x-3*y+1)**4` | Unfinished | Complete | 1,089 |
| `((x+y)**4+(x-y)**4)**2` | Complete | Complete | 0 |
| `(x+y+1)**10` | Unfinished | Unfinished | 0 |
| `(x+y)**8*(x-y)**8` | Unfinished | Complete | 2,395 |
| `((x+y)**3+(2*x-y)**3)**3` | Complete | Complete | 0 |
| `(x+y+1)**6*(2*x-y+1)**6` | Unfinished | Unfinished | 3,415 |
| `(x+y)**16` | Unfinished | Unfinished | 0 |

Completion increases from **2/8 (25%) to 5/8 (62.5%)**, a gain of 37.5 percentage
points on these fixtures. Three tasks remain unfinished; none is reported as
solved. All completed results passed the independent scorer. Increasing the
reserve helps some expressions but does not resolve the remaining growth and
scheduling limits.

The three remaining stops are resource-policy decisions: no available local
candidate fits the estimated work allowance, so the controller leaves the task
unfinished. They are not disproved identities or measured hardware timeouts.
Two stop before committing a step; the mixed trinomial product spends 3,415
units of reserve before its next candidate is denied. This points to more
granular decomposition and better-calibrated work estimates as the next target.

## What this says about other Agent systems

No LangGraph, AgentScope, Dify or other external framework is executed by this
benchmark. There is consequently **no measured international or domestic
ranking** here. These deterministic results cannot be compared to a different
system's live-model accuracy on unrelated tasks. Test counts and GitHub stars
are not substitutes for such a comparison either.

The useful current evidence is narrower: ResiMind's three adapters execute
their checking contracts, learned mathematical rules reduce derivation steps
on these transfer cases, and the overflow ablation exposes both progress and
remaining failures.

The next performance comparison should freeze unseen cases and run the same
model, tools, rule library and budgets across a verifier-retry baseline and
ResiMind. A same-controller ablation of goal-directed feedback or scheduling
would isolate those contributions. Framework comparisons should host the same
task policy and examine recovery, auditability and execution overhead; a
framework's lack of a bundled mathematical verifier is not a failed math test.

This page contains a compact public summary. Detailed local measurements and
existing raw model evaluation records are not added to the repository.
