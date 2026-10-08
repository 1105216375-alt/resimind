"""Whole-plan ResiMind adapter; no dataset, model API, or scoring-oracle access.

The caller supplies an oracle-free query, sandbox fingerprint, plan generator,
and checker. Acceptance establishes only that local contract, not complete
natural-language satisfaction. API recording and resource budgets are external.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
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


class _PlanDomain:
    def rebuild(self, state: State) -> Residual:
        if any(fact.id == "accepted_plan" for fact in state.facts):
            return Residual()
        return Residual(goals=(_GOAL,))


class _PlanProposer:
    def __init__(self, callback: Callable[[dict | None], dict | None]):
        self.callback = callback
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
            "previous_plan": json.loads(event.candidate.claim) if event.candidate else None,
            "decision": event.decision.value,
            "reasons": list(event.reasons),
            "diagnostics": self.diagnostics,
        }


class _PlanVerifier:
    def __init__(self, callback: Callable[[dict], CheckResult], proposer: _PlanProposer):
        self.callback = callback
        self.proposer = proposer
        self.checks: list[dict] = []

    def verify(self, candidate: Candidate, state: State, residual: Residual,
               evidence: tuple[Evidence, ...]) -> Verdict:
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
                 check: Callable[[dict], CheckResult], max_proposals: int = 3) -> AdapterResult:
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
    if not callable(propose) or not callable(check):
        raise TypeError("propose and check must be callable")
    evidence = (
        Evidence(_REFS[0], "chinatravel_task", "raw_query", canonical_json(query),
                 "json", "caller_supplied_oracle_free_query"),
        Evidence(_REFS[1], "chinatravel_sandbox", "fingerprint", sandbox_digest,
                 "", "caller_supplied_sandbox_fingerprint"),
    )
    proposer = _PlanProposer(propose)
    verifier = _PlanVerifier(check, proposer)
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
