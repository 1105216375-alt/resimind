"""Whole-plan ResiMind adapter; no dataset, model API, or scoring-oracle access.

The caller supplies an oracle-free query, sandbox fingerprint, plan generator,
and checker. Acceptance establishes only that local contract, not complete
natural-language satisfaction. API recording and resource budgets are external.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
import json

from resimind.core import (Candidate, Decision, Evidence, Fact, Residual, RunResult,
                           State, TraceEvent, Verdict, canonical_json)
from resimind.runtime import Engine


@dataclass(frozen=True)
class CheckResult:
    accepted: bool
    reasons: tuple[str, ...] = ()
    diagnostics: dict | None = None
    deferred: bool = False

    def __post_init__(self) -> None:
        if type(self.accepted) is not bool or type(self.deferred) is not bool:
            raise TypeError("accepted and deferred must be bools")
        if self.accepted and self.deferred:
            raise ValueError("an accepted check cannot be deferred")
        if type(self.reasons) is not tuple or any(
            type(reason) is not str or not reason.strip() for reason in self.reasons
        ):
            raise TypeError("reasons must be a tuple of nonempty strings")
        if not self.accepted and not self.reasons:
            raise ValueError("a failed/deferred check requires explicit reasons")
        if self.diagnostics is not None and type(self.diagnostics) is not dict:
            raise TypeError("diagnostics must be a JSON object or None")
        canonical_json(self.diagnostics)


@dataclass(frozen=True)
class AdapterResult:
    run_result: RunResult
    accepted_plan: dict | None
    checks: tuple[dict, ...]

    def to_dict(self) -> dict:
        return json.loads(canonical_json({
            "run_result": self.run_result.to_dict(),
            "accepted_plan": self.accepted_plan,
            "checks": self.checks,
            "acceptance_scope": "caller_supplied_local_contract_only",
        }))


_GOAL = "plan_passes_local_contract"
_REFS = ("input_query", "sandbox_fingerprint")


def _bounded_feedback(value, *, max_bytes: int = 24_000):
    """Keep model feedback bounded without changing the checked candidate.

    Gate diagnostics can contain large tables and a rejected itinerary can
    contain many repeated activity objects.  Passing both verbatim into the
    next model request makes the revision budget depend on the size of a
    failure.  The complete values remain in ``checks``/the trace; only the
    untrusted prompt feedback is reduced.  A digest makes truncation auditable.
    """
    if type(max_bytes) is not int or max_bytes < 256:
        raise ValueError("max_feedback_bytes must be an integer >= 256")
    encoded = canonical_json(value).encode("utf-8")
    if len(encoded) <= max_bytes:
        return json.loads(encoded.decode("utf-8"))
    digest = hashlib.sha256(encoded).hexdigest()
    # A short UTF-8-safe prefix is useful to a reviser while the digest and
    # original byte count make clear that this is not a complete plan.
    budget = max(0, max_bytes - 180)
    preview = encoded[:budget].decode("utf-8", errors="ignore")
    return {
        "_truncated": True,
        "sha256": digest,
        "original_bytes": len(encoded),
        "preview": preview,
    }


class _PlanDomain:
    def rebuild(self, state: State) -> Residual:
        if any(fact.id == "accepted_plan" for fact in state.facts):
            return Residual()
        return Residual(goals=(_GOAL,))


class _PlanProposer:
    def __init__(self, callback: Callable[[dict | None], dict | None], *,
                 max_attempts: int, max_feedback_bytes: int):
        self.callback = callback
        self.max_attempts = max_attempts
        self.max_feedback_bytes = max_feedback_bytes
        self.feedback: dict | None = None
        self.attempts = 0
        self.empty = False
        self.diagnostics: dict | None = None

    def propose(self, state: State, residual: Residual) -> tuple[Candidate, ...]:
        self.attempts += 1
        feedback = json.loads(canonical_json(self.feedback))
        plan = self.callback(feedback)
        if plan is None or (type(plan) is dict and not plan):
            self.empty = True
            return ()
        if type(plan) is not dict:
            raise TypeError("proposal must be a native plan dict or None")
        return (Candidate(f"plan_{self.attempts}", "submit_whole_plan", _GOAL,
                          canonical_json(plan), _REFS),)

    def observe(self, event: TraceEvent) -> None:
        self.feedback = {
            "previous_plan": (_bounded_feedback(
                json.loads(event.candidate.claim), max_bytes=self.max_feedback_bytes
            ) if event.candidate else None),
            "decision": event.decision.value,
            "reasons": list(event.reasons),
            "diagnostics": _bounded_feedback(
                self.diagnostics, max_bytes=self.max_feedback_bytes
            ),
            "budget": {
                "attempt": self.attempts,
                "max_attempts": self.max_attempts,
                "remaining_proposals": max(0, self.max_attempts - self.attempts),
            },
        }


class _PlanVerifier:
    def __init__(self, callback: Callable[[dict], CheckResult], proposer: _PlanProposer,
                 *, max_plan_bytes: int):
        self.callback = callback
        self.proposer = proposer
        self.max_plan_bytes = max_plan_bytes
        self.checks: list[dict] = []

    def verify(self, candidate: Candidate, state: State, residual: Residual,
               evidence: tuple[Evidence, ...]) -> Verdict:
        if len(candidate.claim.encode("utf-8")) > self.max_plan_bytes:
            checked = CheckResult(
                False,
                ("candidate_size_limit",),
                {"max_plan_bytes": self.max_plan_bytes,
                 "candidate_bytes": len(candidate.claim.encode("utf-8"))},
                deferred=False,
            )
            self.proposer.diagnostics = checked.diagnostics
            self.checks.append({"candidate_id": candidate.id, "accepted": False,
                                "deferred": False, "reasons": list(checked.reasons),
                                "diagnostics": checked.diagnostics})
            return Verdict.for_candidate(candidate, state, Decision.REJECT,
                                         evidence=evidence, reasons=checked.reasons)
        plan = json.loads(candidate.claim)
        checked = self.callback(plan)
        if type(checked) is not CheckResult:
            raise TypeError("check must return CheckResult")
        if canonical_json(plan) != candidate.claim:
            raise ValueError("check must not mutate its candidate plan")
        reasons = tuple(dict.fromkeys(checked.reasons)) or ("local_contract_passed",)
        diagnostics = json.loads(canonical_json(checked.diagnostics))
        self.proposer.diagnostics = diagnostics
        self.checks.append({"candidate_id": candidate.id, "accepted": checked.accepted,
                            "deferred": checked.deferred, "reasons": list(reasons),
                            "diagnostics": diagnostics})
        decision = (Decision.ACCEPT if checked.accepted else
                    Decision.DEFER if checked.deferred else Decision.REJECT)
        facts = (Fact("accepted_plan", "chinatravel_plan", "local_contract_accepted_plan",
                      candidate.claim, "json", _REFS),) if checked.accepted else ()
        return Verdict.for_candidate(candidate, state, decision, evidence=evidence,
                                     facts=facts, reasons=reasons)


def run_resimind(query: dict, sandbox_digest: str,
                 propose: Callable[[dict | None], dict | None],
                 check: Callable[[dict], CheckResult], max_proposals: int = 3,
                 *, max_feedback_bytes: int = 24_000,
                 max_plan_bytes: int = 512_000) -> AdapterResult:
    """Run one task, committing an entire plan only after local acceptance.

    ``propose(None)`` starts the run. Subsequent calls receive ``previous_plan``,
    ``decision``, ``reasons``, and ``diagnostics`` from the preceding attempt.
    None or an empty dict ends generation with a deferred, unresolved result.
    Exceptions stop the run as errors; no unaccepted plan is returned as output.
    The caller must remove gold labels/oracle fields before passing ``query``.
    """
    if type(query) is not dict:
        raise TypeError("query must be an oracle-free JSON object")
    if type(sandbox_digest) is not str or not sandbox_digest.strip():
        raise ValueError("sandbox_digest must be a fixed nonempty fingerprint")
    if type(max_proposals) is not int or max_proposals < 1:
        raise ValueError("max_proposals must be a positive integer")
    if type(max_feedback_bytes) is not int or max_feedback_bytes < 256:
        raise ValueError("max_feedback_bytes must be an integer >= 256")
    if type(max_plan_bytes) is not int or max_plan_bytes < 256:
        raise ValueError("max_plan_bytes must be an integer >= 256")
    if not callable(propose) or not callable(check):
        raise TypeError("propose and check must be callable")
    evidence = (
        Evidence(_REFS[0], "chinatravel_task", "raw_query", canonical_json(query),
                 "json", "caller_supplied_oracle_free_query"),
        Evidence(_REFS[1], "chinatravel_sandbox", "fingerprint", sandbox_digest,
                 "", "caller_supplied_sandbox_fingerprint"),
    )
    proposer = _PlanProposer(propose, max_attempts=max_proposals,
                             max_feedback_bytes=max_feedback_bytes)
    verifier = _PlanVerifier(check, proposer, max_plan_bytes=max_plan_bytes)
    engine = Engine(_PlanDomain(), verifier, (proposer,), evidence=evidence,
                    max_steps=1, max_no_progress=2)
    state, trace = State(), []
    for attempt in range(1, max_proposals + 1):
        result = engine.run(state)
        trace.extend(replace(event, step=attempt if event.step else 0) for event in result.trace)
        state = result.state
        if result.status in ("solved", "error", "interrupted"):
            break
        if proposer.empty:
            result = replace(result, status="deferred", stop_reason="no_candidate")
            break
    result = replace(result, trace=tuple(trace))
    accepted = next((json.loads(fact.value) for fact in state.facts
                     if fact.id == "accepted_plan"), None)
    return AdapterResult(result, accepted, tuple(verifier.checks))
