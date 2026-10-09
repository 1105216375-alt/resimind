# Changelog

## 0.12.0 — 2026-10-09

- Add an opt-in cumulative local-work overflow reserve for unusually large symbolic steps. Each accepted candidate pays only its estimated excess over the normal bound; the reserve is finite, never refunded by rollback, and remains behind exact identity, goal-progress, and optional Lean gates.
- Keep the default local-work budget and zero-overflow behavior unchanged for existing callers. Record bounded overflow grants in the adaptive audit without treating scheduling estimates as mathematical evidence.
- Add regression coverage for multi-step overflow, strict option validation, and the complex mixed-product expansion path. Detailed model evaluations remain local.

## 0.11.0 — 2026-10-09

- Separate verified identity from goal progress in the adaptive polynomial loop. Assess progress after exact verification and before optional Lean checks; defer cosmetic rewrites with feedback about remaining expansion work.
- Preserve useful local distribution and power decomposition even when expressions grow. Give uncertain intermediate steps a finite per-task allowance that cannot be refunded by changing state or rolling back.
- Add explicit goal feedback and bounded progress observations, with an opt-out for earlier behavior. Progress heuristics never replace identity verification, Lean or the actual task-completion condition.
- Keep Lean proof chains working when cancellation removes a task variable. Retain all original bindings and scope the unused-variable linter option to the generated theorem; emitted warnings and errors still fail verification.
- Add the offline `progress` demonstration for cosmetic recovery and useful decomposition. See [goal progress](docs/goal-progress.md). New quantitative evaluation records remain local.

## 0.10.0 — 2026-10-09

- Compare bounded rule, compaction and local-expansion candidates before requesting a model. Prefer a candidate that completes the goal, then estimate remaining work and growth; a matching rule need not be selected. Exact verification and optional Lean checks remain mandatory for selected steps.
- Add explicit local-work and rule-preview limits, with an opt-out preserving the earlier scheduling policy. Keep model/action/proof budgets and failed-candidate protections; historical evaluation runners explicitly retain their original policy.
- Record candidate selection reasons and bounded per-rule outcome/complexity observations in the JSON audit. Observations accumulate through a supplied statistics object and never authorize a rule or claim measured savings.
- Clarify the model expression grammar in prompts and rejection feedback: use `**` for powers and `*` for multiplication. Unsupported notation remains rejected without automatic answer rewriting.
- Add an offline `scheduling` demo showing configured-but-unused model callbacks, learned-rule reuse, and a separately labeled scripted fallback. See [the guide](docs/cost-aware-scheduling.md) and [release validation](docs/validation-v0.10.0.md). New quantitative evaluation records remain private.

## 0.9.0 — 2026-10-09

- Control expression growth with exact local compaction and bounded distributive batches. Preserve the existing AST, depth, source-length and action limits; independently verify every candidate. The adaptive builder enables these tools by default, with an explicit switch preserving the original primitive path.
- Add an optional real Lean 4.29.0 backend for the supported rational-polynomial grammar. Translate ASTs to fixed `Rat` equalities, allow only `rfl` and built-in `grind`, and require compiler success, actual target feedback, approved theorem axioms and a compiled proof artifact.
- Feed incomplete Lean goals and diagnostics into a new `proof_model` proposal, or use a bounded host proof retry. Keep action, model and Lean-check budgets separate. An enabled Lean gate cannot silently fall back to exact-only acceptance; recheck rule revocation after external proof work.
- Add offline `growth-control` and actual-local-Lean `lean` CLI demos. The Lean demo uses scripted proposal selection, real compiler checks and verified rule reuse; it makes no live-model performance claim.
- Preserve the historical v5 evaluation treatment by explicitly disabling the new tools there. Private evaluation records remain local. See [the integration guide](docs/expression-growth-and-lean.md) and [release validation](docs/validation-v0.9.0.md).

## 0.8.0 — 2026-10-09

- Add opt-in adaptive polynomial reasoning: failed checks can switch between whole-expression model proposals, local subexpression proposals, verified rules, primitive symbolic rewrites, and checked checkpoint returns. Preserve finite budgets across retries, strategy changes and recovery; unfinished runs remain unfinished.
- Add the reusable `StrategyController` with classified failures, semantic-state retry accounting, duplicate suppression, and bounded audit events. Strategy choices never authorize a fact without domain verification.
- Bind polynomial actions to the current expression, revision and fingerprint. Execute retrieved, currently verified rules as bounded substitutions and independently recheck every application.
- Add structured unresolved-subexpression guidance and bounded coefficient-discrepancy feedback to guide corrections without exposing oracle-generated answers.
- Add `resimind demo --domain adaptive`: an explicitly scripted offline mistake, checked local repair, symbolic completion, verified rule admission, save/reload, and reuse on a new variable. Add reproducible evaluation runners; private experiments and their raw model traces are not included.
- Release validation and package checks are recorded in [v0.8.0 validation](docs/validation-v0.8.0.md). The adaptive adapter currently supports bounded rational polynomial expansion; Lean integration is future work.

- Add a prospectively frozen, same-DeepSeek four-controller algebra pilot, an independent exact-grid scorer and bounded, replayable request accounting. Publish compact results including failures and discovery costs; retain full new requests locally.
- Match learned-rule applications structurally, allowing whitespace and redundant parentheses while rejecting different substitutions that falsely claim rule provenance.
- Return one exact coefficient discrepancy on rejected polynomial identities, preserving rejection codes and verification boundaries. Publish a separate seen-case development rerun: richer feedback improves the retry baseline, while residual and growth completion totals stay unchanged.
- Refresh English and Chinese project pages around checked reasoning and cross-task knowledge growth, with an executable quickstart and explicit evidence for quantitative claims.

- Add verified knowledge growth: completed Agent proof chains become candidate rules, independently verified before admission, persisted with conditions/provenance, rechecked on reload, and reverified on use in new tasks. Unknown or rejected checks never authorize promotion.
- Add a bounded, exact rational-polynomial certificate kernel and a learning Agent with primitive AST derivations, learned-rule matching, and optional model proposals. Keep nonzero preconditions explicit for cancellation; unsupported symbolic division and numerical samples cannot prove a universal rule.
- Add `resimind demo --domain knowledge-growth` and a frozen-library comparison with an independent exact degree-grid scorer. Publish compact development results; distinguish proposal-count savings from compute savings or model superiority.

- Add zero-dependency `resimind.search`: bounded alternatives, temporary draft regressions, full verification before acceptance, callback integrity checks and finite traces.
- Add an oracle-blind ChinaTravel schedule constructor, restaurant alternatives and narrow AST checks for contradictory self-translations. Preserve default refusal for ambiguous zero-room visits; explicitly annotated replay completes one seen task, while the default remains 0/2. Publish a compact summary; retain new raw records locally.
- Correct hotel allocation checks: the dataset's bed count cannot prove guest capacity. Keep positive room/bed quantities and exact room-type binding; disclose the unavailable capacity check.
- Add the zero-dependency `resimind.repair` API for evidence-referenced JSON edits, stale-write protection, atomic revalidation, and strict progress without checked-constraint regressions.
- Add a separate ChinaTravel development integration with exact tool-data binding, conservative route repairs, task-anchored prompts, corrected total-cost instructions and traveller-coverage checks.
- Add an explicit overnight-state guard after a first live development round exposed a gap in the official scorer. Keep both two-task live rounds and both full 28-candidate replays: 14 drafts improve, but full replay completions stay unchanged and the guarded live round finishes neither task. These are development diagnostics, not held-out or component-level superiority claims.
- Keep adapter check names stable during schema repair and return actionable missing-field feedback. A final offline replay repairs schema for 3 of all 5 recorded candidates, with no new model calls or full completions.

## 0.7.0 — 2026-10-08

- Add open-ended day planning: freely compose venues, order, times and transport, then independently check complete route feasibility, budget, walking, opening windows, category coverage, and indoor requirements.
- Accept different valid itineraries instead of matching a stored answer. Keep subjective rationale outside checked facts and unknown transport data unresolved.
- Add installed planning scenarios, a live DeepSeek entry point, and a trace-derived SVG that clearly labels its offline fixture.
- Lead the home pages with direct use of the standalone ResiMind Agent. Move LangGraph into the optional-integrations section; the core and domains remain independent of it.
- Publish actual live planning and preference-change records separately from scripted demonstrations; see the validation record for precise scope.

## 0.6.0 — 2026-10-08

- Add a customer-support Agent using synthetic ecommerce orders and a fictional merchant return policy, with an independently checked recommendation.
- Demonstrate rejected over-refunds, corrected item-payment amounts, missing delivery evidence, and out-of-window human review. No refund or customer message is executed.
- Add installed `resimind demo --domain customer-support` scenarios and an explicit live DeepSeek example using the same verifier.
- Preserve the common Agent runtime, model callbacks, and LangGraph gate. The support adapter adds domain rules without changing the reasoning core.
- Extend the bilingual home pages and domain documentation with everyday business examples; validation details are in `docs/validation.md`.

## 0.5.0 — 2026-10-08

- Put independent verification, a three-minute quickstart, and an executable trace animation at the front of both home pages.
- Add the installed `resimind demo` / `python -m resimind demo` commands, with offline optimization, bridge, and JSON audit modes.
- Add optional DeepSeek Chat Completions and OpenAI Responses callbacks with explicit model configuration, request budgets, timeouts, usage accounting, and no offline fallback.
- Add a LangGraph verification subgraph: completed tasks expose committed facts; unresolved tasks keep their audit and residual without a completed report.
- Record three live DeepSeek smoke runs, including two unfinished attempts and one verified result, with an offline certificate replay tool.
- Preserve the domain verifiers, zero-dependency core, creator attribution, and MIT license. See `docs/validation.md` for actual checks and live-run scope.

## 0.4.0 — 2026-10-08

- Add an exact constrained-quadratic-optimization Agent with independently checked feasibility, KKT, and global-optimality certificates.
- Add a two-span continuous bridge Agent checking beam compatibility, equilibrium, complete live-load pattern envelopes, and supplied limits.
- Demonstrate rejection of an infeasible stationary point and an incompatible simply-supported-span approximation.
- Lead the bilingual home pages with the two substantive cases, reproducible plots, readable traces, and optional JSON output.
- Preserve all introductory adapters and the zero-dependency runtime.

## 0.3.0 — 2026-10-08

- Launch as **ResiMind** with the Python package and import namespace `resimind`.
- Add an English landing page, complete Chinese guide, and an original architecture banner.
- Document the implemented symbolic checks, injectable neural component, residual feedback, and current limitations.
- Preserve the shared Agent core and runnable mathematics and engineering adapters from the pre-publication versions below.

## 0.2.0 — 2026-10-08

- Add installable mathematics and engineering adapters sharing the unchanged Agent runtime.
- Add exact rational linear-equation normalization, solution, and substitution/classification checks.
- Add a synthetic axial-bar stress workflow with explicit model assumptions, dimensions, and supplied limits.
- Add reusable exact quantities and a multiplicative unit registry.
- Demonstrate that a completed analysis can conclude no solution or an exceeded limit.

## 0.1.0 — 2026-10-08

- Add task-level Agent orchestration, registered evidence tools, provider-neutral structured model proposals, and verifier feedback for correction.
- Extract the proposal, verification, atomic commit, and residual rebuild architecture into an independent reference implementation.
- Add immutable contracts, explicit evidence references, verification binding, and four decision outcomes.
- Add bounded attempts, stall detection, and structured run traces.
- Add reviewed, revocable in-memory route guidance.
- Add synthetic cross-domain examples and regression tests for verification boundaries.
