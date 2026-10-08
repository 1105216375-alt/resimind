"""Synchronous propose -> verify -> commit -> rebuild reference runtime.

The verifier and domain adapter are trusted application code. This module
checks data integrity; it cannot make an unsound verifier sound or sandbox
extensions. Budgets count attempts, not execution time or model tokens.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .core import (Binding, Candidate, Decision, Evidence, Fact, Residual,
                   RunResult, State, TraceEvent, Verdict, canonical_json)


class Proposer(Protocol):
    """Proposal plugin, optionally also implementing ``observe(event) -> None``.

    The engine sends only this proposer's completed attempt to its observer,
    including rejection, deferral and empty proposals. Observation is feedback,
    not another opportunity to commit facts. Both methods are synchronous.
    """

    def propose(self, state: State, residual: Residual) -> Iterable[Candidate]:
        """Return proposals; the runtime takes the first candidate this turn."""
        ...


class Verifier(Protocol):
    def verify(self, candidate: Candidate, state: State, residual: Residual,
               evidence: tuple[Evidence, ...]) -> Verdict:
        """Check domain semantics and return a bound verdict and proposed facts."""
        ...


class Domain(Protocol):
    def rebuild(self, state: State) -> Residual:
        """Independently recompute all remaining goals, unknowns and constraints."""
        ...


class _InvalidCommit(ValueError):
    pass


def _same_observation(left: Fact, right: Fact) -> bool:
    # JSON distinguishes booleans from numbers; Python equality alone does not.
    return left.unit == right.unit and canonical_json(left.value) == canonical_json(right.value)


class Engine:
    """Run a bounded, deterministic round-robin schedule over proposer plugins.

    Every selected proposer consumes one attempt, including empty proposals,
    rejection, deferral and failure. Only its first candidate is considered.
    An optional ``observe(TraceEvent)`` hook receives that attempt before the
    next turn or termination. Observer failure stops the run with an explicit
    diagnostic; it does not undo an already completed fact transaction.
    Progress means a strict decrease in the number of residual obligations.
    Newly added facts alone do not reset the no-progress budget.

    The engine owns its evidence registry. Initial facts are caller-supplied
    trusted state and receive integrity checks, not independent proof checks.
    """

    def __init__(self, domain: Domain, verifier: Verifier,
                 proposers: Iterable[Proposer], *, evidence: tuple[Evidence, ...] = (),
                 max_steps: int = 50, max_no_progress: int = 5) -> None:
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if type(max_no_progress) is not int or max_no_progress < 1:
            raise ValueError("max_no_progress must be a positive integer")
        if type(evidence) is not tuple or any(type(item) is not Evidence for item in evidence):
            raise ValueError("evidence must be an immutable tuple of Evidence")
        if len({item.id for item in evidence}) != len(evidence):
            raise ValueError("evidence ids must be unique")
        self.domain = domain
        self.verifier = verifier
        self.proposers = tuple(proposers)
        if not self.proposers:
            raise ValueError("at least one proposer is required")
        self.evidence = evidence
        self.max_steps = max_steps
        self.max_no_progress = max_no_progress
        self._evidence_ids = frozenset(item.id for item in evidence)

    def _validate_state(self, state: State) -> None:
        if type(state) is not State:
            raise ValueError("initial_state must be a State")
        slots: dict[tuple[str, str, str], Fact] = {}
        for fact in state.facts:
            if fact.id in self._evidence_ids:
                raise ValueError("evidence and fact ids must not overlap")
            if not set(fact.evidence_refs) <= self._evidence_ids:
                raise ValueError("initial fact references unknown evidence")
            previous = slots.get(fact.key)
            if previous is not None and not _same_observation(previous, fact):
                raise ValueError("initial state contains conflicting facts")
            slots[fact.key] = fact

    def _rebuild(self, state: State) -> Residual:
        result = self.domain.rebuild(state)
        if type(result) is not Residual:
            raise TypeError("domain.rebuild must return a Residual")
        return result

    def _candidate_error(self, candidate: Candidate, state: State,
                         residual: Residual) -> str | None:
        if candidate.target not in residual.pending:
            return "unknown_target"
        known_refs = self._evidence_ids | {fact.id for fact in state.facts}
        if not set(candidate.refs) <= known_refs:
            return "unknown_reference"
        return None

    def _commit(self, candidate: Candidate, verdict: Verdict, state: State) -> State:
        if verdict.binding != Binding.for_candidate(candidate, state, self.evidence):
            raise _InvalidCommit("verdict_binding_mismatch")
        if verdict.decision is not Decision.ACCEPT:
            if verdict.facts:
                raise _InvalidCommit("nonaccept_verdict_contains_facts")
            return state

        by_id = {fact.id: fact for fact in state.facts}
        by_key = {fact.key: fact for fact in state.facts}
        reachable_evidence = set(candidate.refs) & self._evidence_ids
        for reference in candidate.refs:
            if reference in by_id:
                reachable_evidence.update(by_id[reference].evidence_refs)

        additions: list[Fact] = []
        for fact in verdict.facts:
            if not set(fact.evidence_refs) <= self._evidence_ids:
                raise _InvalidCommit("unknown_evidence_reference")
            if not set(fact.evidence_refs) <= reachable_evidence:
                raise _InvalidCommit("evidence_not_bound_to_candidate")
            if fact.id in self._evidence_ids:
                raise _InvalidCommit("fact_id_overlaps_evidence")
            previous_id = by_id.get(fact.id)
            if previous_id is not None:
                if canonical_json(previous_id) != canonical_json(fact):
                    raise _InvalidCommit("fact_id_conflict")
                continue
            previous_key = by_key.get(fact.key)
            if previous_key is not None and not _same_observation(previous_key, fact):
                raise _InvalidCommit("fact_value_or_unit_conflict")
            by_id[fact.id] = fact
            by_key[fact.key] = fact
            additions.append(fact)

        if not additions:
            return state
        return State(revision=state.revision + 1, facts=state.facts + tuple(additions))

    def run(self, initial_state: State | None = None) -> RunResult:
        state = State() if initial_state is None else initial_state
        self._validate_state(state)
        trace: list[TraceEvent] = []
        # Never use an empty residual after adapter failure: empty means solved.
        try:
            residual = self._rebuild(state)
        except Exception as exc:
            residual = Residual(hard_constraints=("domain_rebuild_failed",))
            trace.append(TraceEvent(0, "domain", Decision.INTERRUPT,
                                    ("domain_error", type(exc).__name__), state, state,
                                    residual, residual))
            return RunResult(state, residual, "error", "domain_error", tuple(trace), self.evidence)
        if residual.solved:
            return RunResult(state, residual, "solved", "residual_empty", evidence=self.evidence)

        no_progress = 0
        for step in range(1, self.max_steps + 1):
            before, residual_before = state, residual
            proposer = self.proposers[(step - 1) % len(self.proposers)]
            name = type(proposer).__name__
            candidate = None
            verdict = None
            decision = Decision.DEFER
            reasons = ("no_candidate",)
            error_reason = None

            try:
                proposals = proposer.propose(state, residual)
                candidate = next(iter(proposals), None)
                if candidate is not None and type(candidate) is not Candidate:
                    candidate = None
                    raise TypeError("proposers must return Candidate objects")
            except Exception as exc:
                decision = Decision.INTERRUPT
                reasons = ("proposer_error", type(exc).__name__)
                error_reason = "proposer_error"

            if candidate is not None and error_reason is None:
                candidate_error = self._candidate_error(candidate, state, residual)
                if candidate_error is not None:
                    decision, reasons = Decision.REJECT, (candidate_error,)
                else:
                    try:
                        verdict = self.verifier.verify(candidate, state, residual, self.evidence)
                        if type(verdict) is not Verdict:
                            verdict = None
                            raise TypeError("verifier.verify must return a Verdict")
                    except Exception as exc:
                        decision = Decision.INTERRUPT
                        reasons = ("verifier_error", type(exc).__name__)
                        error_reason = "verifier_error"
                    else:
                        decision, reasons = verdict.decision, verdict.reasons
                        try:
                            prospective_state = self._commit(candidate, verdict, state)
                        except _InvalidCommit as exc:
                            decision, reasons = Decision.REJECT, (str(exc),)
                        except Exception as exc:
                            decision = Decision.INTERRUPT
                            reasons = ("commit_error", type(exc).__name__)
                            error_reason = "commit_error"
                        else:
                            if decision is Decision.ACCEPT:
                                try:
                                    prospective_residual = self._rebuild(prospective_state)
                                except Exception as exc:
                                    # Commit and residual reconstruction are one transaction.
                                    decision = Decision.INTERRUPT
                                    reasons = ("domain_error", type(exc).__name__)
                                    error_reason = "domain_error"
                                else:
                                    state, residual = prospective_state, prospective_residual

            event = TraceEvent(step, name, decision, reasons, before, state,
                               residual_before, residual, candidate, verdict)
            trace.append(event)
            try:
                observer = getattr(proposer, "observe", None)
                if observer is not None:
                    if not callable(observer):
                        raise TypeError("proposer.observe must be callable")
                    observer(event)
            except Exception as exc:
                # Step zero denotes a diagnostic, not a second proposal attempt.
                # Retain the original accept/reject event and any completed commit.
                trace.append(TraceEvent(
                    0, f"{name}.observe", Decision.INTERRUPT,
                    tuple(dict.fromkeys(("observer_error", type(exc).__name__))),
                    state, state, residual, residual, candidate,
                ))
                return RunResult(state, residual, "error", "observer_error",
                                 tuple(trace), self.evidence)
            if error_reason is not None:
                return RunResult(state, residual, "error", error_reason, tuple(trace), self.evidence)
            if decision is Decision.INTERRUPT:
                return RunResult(state, residual, "interrupted", "verifier_interrupt", tuple(trace), self.evidence)
            if residual.solved:
                return RunResult(state, residual, "solved", "residual_empty", tuple(trace), self.evidence)

            no_progress = 0 if residual.measure < residual_before.measure else no_progress + 1
            if no_progress >= self.max_no_progress:
                return RunResult(state, residual, "stalled", "max_no_progress", tuple(trace), self.evidence)

        return RunResult(state, residual, "budget_exhausted", "max_steps", tuple(trace), self.evidence)
