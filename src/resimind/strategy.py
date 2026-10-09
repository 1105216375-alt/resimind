"""Bounded strategy selection and audit metadata, separate from verification.

The adapter owns semantic state keys, candidate normalization, and truth checks.
Never include a bookkeeping revision or candidate nonce in those semantic keys:
doing so would make the same failed edge appear new. This controller cannot edit
facts, restore a checkpoint, verify a claim, or call a model.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json


class FailureKind(str, Enum):
    IDENTITY_MATH = "identity_math"
    PROOF_INCOMPLETE = "proof_incomplete"
    SCHEMA_BINDING = "schema_binding"
    UNAVAILABLE_RULE = "unavailable_rule"
    NO_PROGRESS = "no_progress"
    RESOURCE = "resource"
    UNKNOWN_ERROR = "unknown_error"


def _text(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _positive(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _optional_count(value, name):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a nonnegative integer or None")


def _names(value, name, *, empty=False):
    if type(value) is not tuple or (not empty and not value):
        raise ValueError(f"{name} must be a {'possibly empty ' if empty else 'nonempty '}tuple")
    for item in value:
        _text(item, name)
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicates")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite JSON number")


def candidate_fingerprint(candidate_key: str) -> str:
    """Hash an adapter-supplied semantic key; canonicalize strict JSON keys.

    JSON object order and formatting are immaterial. For other text only outer
    whitespace is removed: this helper does not infer algebraic equivalence or
    remove identifiers. Adapters must omit nonce/revision fields themselves.
    """
    _text(candidate_key, "candidate_key")
    try:
        value = json.loads(candidate_key, object_pairs_hook=_unique, parse_constant=_nonfinite)
        normalized = "json:" + _json(value)
    except (ValueError, TypeError, RecursionError):
        normalized = "text:" + candidate_key.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _metadata(value):
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError("checkpoint must be a JSON object or None")
    visited = 0
    def validate(item, depth=0):
        nonlocal visited
        visited += 1
        if depth > 16 or visited > 512:
            raise ValueError("checkpoint exceeds its depth or item limit")
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) in (list, tuple):
            for child in item:
                validate(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child, depth + 1)
            return
        raise ValueError("checkpoint must contain JSON-compatible values")
    try:
        validate(value)
        encoded = _json(value)
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError("checkpoint exceeds its byte limit")
        return encoded
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("checkpoint must be finite JSON within 16 levels, 512 items, and 16384 bytes") from exc


@dataclass(frozen=True, slots=True)
class StrategyEvent:
    index: int
    event: str
    state_key: str
    strategy: str | None
    branch: str
    attempt: int
    accepted: bool | None = None
    progress: bool = False
    candidate_fingerprint: str | None = None
    duplicate: bool = False
    failure_kind: FailureKind | None = None
    reasons: tuple[str, ...] = ()
    rank: int | None = None
    revision: int | None = None
    checkpoint_json: str | None = None

    def to_dict(self):
        return {
            "index": self.index, "event": self.event, "state_key": self.state_key,
            "strategy": self.strategy, "branch": self.branch, "attempt": self.attempt,
            "accepted": self.accepted, "progress": self.progress,
            "candidate_fingerprint": self.candidate_fingerprint, "duplicate": self.duplicate,
            "failure_kind": None if self.failure_kind is None else self.failure_kind.value,
            "reasons": list(self.reasons), "rank": self.rank, "revision": self.revision,
            "checkpoint": None if self.checkpoint_json is None else json.loads(self.checkpoint_json),
        }


class StrategyController:
    """Reserve one attempt, then record its independently verified outcome.

    Attempt/failure limits apply per (semantic state, strategy); the global and
    optional named-branch limits never reset. Selection reserves an attempt even
    if generation later fails. At most one attempt is pending. Call ``record``
    or ``abandon`` to close it; repeating the identical ``select`` is idempotent.

    Failure or non-progress moves a strategy behind untried alternatives in the
    current semantic state. Previously failed strategies can produce different
    candidates until their limits expire; exact semantic candidate duplicates
    remain visible across strategies and branches in the same state.

    Every selected/recorded attempt is retained. Stop-only events are coalesced
    when repeated and capped at max_attempts + 1; snapshot reports suppressed
    stops and the latest stop. Total audit history is at most 3*max_attempts + 1.
    """

    def __init__(self, strategies: tuple[str, ...], *, max_attempts: int = 24,
                 max_attempts_per_strategy: int = 3, max_failures_per_strategy: int = 2,
                 branch_budgets: Mapping[str, int] | None = None,
                 reason_kinds: Mapping[str, FailureKind] | None = None):
        _names(strategies, "strategies")
        for name, value in (("max_attempts", max_attempts),
                            ("max_attempts_per_strategy", max_attempts_per_strategy),
                            ("max_failures_per_strategy", max_failures_per_strategy)):
            _positive(value, name)
        if branch_budgets is not None and not isinstance(branch_budgets, Mapping):
            raise ValueError("branch_budgets must be a mapping or None")
        branches = None if branch_budgets is None else dict(branch_budgets)
        if branches is not None:
            for branch, limit in branches.items():
                _text(branch, "branch")
                _positive(limit, "branch budget")
        if reason_kinds is not None and not isinstance(reason_kinds, Mapping):
            raise ValueError("reason_kinds must be a mapping or None")
        kinds = {} if reason_kinds is None else dict(reason_kinds)
        for reason, kind in kinds.items():
            _text(reason, "reason")
            if type(kind) is not FailureKind:
                raise ValueError("reason_kinds values must be FailureKind members")
        self._strategies = strategies
        self._max_attempts = max_attempts
        self._max_per_strategy = max_attempts_per_strategy
        self._max_failures = max_failures_per_strategy
        self._branch_budgets = branches
        self._reason_kinds = kinds
        self._attempts = 0
        self._progress = 0
        self._branches = {}
        self._counts = {}
        self._seen = {}
        self._failed_order = {}
        self._best_rank = {}
        self._events = []
        self._retained_stops = 0
        self._suppressed_stops = 0
        self._last_stop = None
        self._pending = None

    @property
    def events(self) -> tuple[StrategyEvent, ...]:
        return tuple(self._events)

    def is_eligible(self, state_key: str, strategy: str, *, branch: str = "main") -> bool:
        """Inspect remaining budgets without reserving an attempt or adding events.

        A pending attempt is already charged. This query does not finish that
        attempt or authorize another selection: callers must still use the
        normal ``select`` / ``record`` lifecycle. Candidate validity and
        duplicate detection remain the adapter's separate responsibilities.
        """
        _text(state_key, "state_key")
        _text(strategy, "strategy")
        _text(branch, "branch")
        if strategy not in self._strategies:
            raise ValueError("strategy is not registered")
        if self._branch_budgets is not None and branch not in self._branch_budgets:
            raise ValueError("branch is not registered in branch_budgets")
        counts = self._counts.get((state_key, strategy), {})
        return (self._attempts < self._max_attempts
                and (self._branch_budgets is None
                     or self._branches.get(branch, 0) < self._branch_budgets[branch])
                and counts.get("attempts", 0) < self._max_per_strategy
                and counts.get("failures", 0) < self._max_failures)

    def _event(self, **values):
        event = StrategyEvent(index=len(self._events), attempt=self._attempts, **values)
        if event.event == "stopped":
            self._last_stop = event
            previous = self._events[-1] if self._events else None
            repeated = (previous is not None and previous.event == "stopped"
                        and previous.attempt == self._attempts
                        and all(getattr(previous, key) == value for key, value in values.items()))
            if repeated or self._retained_stops >= self._max_attempts + 1:
                self._suppressed_stops += 1
                return event
            self._retained_stops += 1
        self._events.append(event)
        return event

    def select(self, state_key: str, available: tuple[str, ...], *, branch: str = "main",
               checkpoint: dict | None = None) -> str | None:
        _text(state_key, "state_key")
        _text(branch, "branch")
        _names(available, "available", empty=True)
        if any(name not in self._strategies for name in available):
            raise ValueError("available contains an unregistered strategy")
        if self._branch_budgets is not None and branch not in self._branch_budgets:
            raise ValueError("branch is not registered in branch_budgets")
        metadata = _metadata(checkpoint)
        request = (state_key, available, branch, metadata)
        if self._pending is not None:
            if self._pending["request"] == request:
                return self._pending["strategy"]
            raise ValueError("record or abandon the pending attempt before selecting again")
        reason = None
        kind = FailureKind.RESOURCE
        if self._attempts >= self._max_attempts:
            reason = "global_attempt_budget_exhausted"
        elif self._branch_budgets is not None and self._branches.get(branch, 0) >= self._branch_budgets[branch]:
            reason = "branch_attempt_budget_exhausted"
        elif not available:
            reason, kind = "no_available_strategy", FailureKind.UNAVAILABLE_RULE
        eligible = [name for name in available
                    if self._counts.get((state_key, name), {}).get("attempts", 0) < self._max_per_strategy
                    and self._counts.get((state_key, name), {}).get("failures", 0) < self._max_failures]
        if reason is None and not eligible:
            reason = "state_strategy_budgets_exhausted"
        if reason is not None:
            self._event(event="stopped", state_key=state_key, strategy=None, branch=branch,
                        failure_kind=kind, reasons=(reason,), checkpoint_json=metadata)
            return None
        failed = self._failed_order.get(state_key, [])
        strategy = min(eligible, key=lambda name: (name in failed,
                       failed.index(name) if name in failed else available.index(name)))
        self._attempts += 1
        self._branches[branch] = self._branches.get(branch, 0) + 1
        counts = self._counts.setdefault((state_key, strategy), {"attempts": 0, "failures": 0, "progress": 0})
        counts["attempts"] += 1
        self._pending = dict(request=request, state_key=state_key, strategy=strategy,
                             branch=branch, checkpoint_json=metadata)
        self._event(event="selected", state_key=state_key, strategy=strategy, branch=branch,
                    checkpoint_json=metadata)
        return strategy

    def is_duplicate(self, state_key: str, candidate_key: str) -> bool:
        _text(state_key, "state_key")
        return candidate_fingerprint(candidate_key) in self._seen.get(state_key, set())

    def record(self, state_key: str, strategy: str, candidate_key: str | None, *,
               accepted: bool, progress: bool, reasons: tuple[str, ...] = (),
               failure_kind: FailureKind | None = None, rank: int | None = None,
               revision: int | None = None) -> StrategyEvent:
        """Close one reservation using adapter-reported verification results.

        Accepted-but-unproductive and duplicate outcomes are NO_PROGRESS even
        when a different failure kind was supplied. For other failures the
        explicit kind wins, followed by exact adapter reason mappings. A rank
        is optional audit data; if progress is claimed with a rank it must beat
        earlier accepted ranks for this semantic state. Neither ranks nor this
        method establish mathematical truth. Check ``is_duplicate`` before
        redoing an already seen candidate; record is an outcome audit gate.
        """
        _text(state_key, "state_key")
        _text(strategy, "strategy")
        if type(accepted) is not bool or type(progress) is not bool:
            raise ValueError("accepted and progress must be booleans")
        _names(reasons, "reasons", empty=True)
        if failure_kind is not None and type(failure_kind) is not FailureKind:
            raise ValueError("failure_kind must be a FailureKind or None")
        _optional_count(rank, "rank")
        _optional_count(revision, "revision")
        fingerprint = None if candidate_key is None else candidate_fingerprint(candidate_key)
        if progress and not accepted:
            raise ValueError("only an independently accepted candidate can receive progress credit")
        if accepted and fingerprint is None:
            raise ValueError("an empty proposal cannot be accepted")
        if accepted and progress and failure_kind is not None:
            raise ValueError("a progress outcome cannot also declare failure")
        pending = self._pending
        if pending is None or (pending["state_key"], pending["strategy"]) != (state_key, strategy):
            raise ValueError("record must match the reserved state and strategy")
        duplicate = fingerprint is not None and fingerprint in self._seen.get(state_key, set())
        if duplicate and progress:
            raise ValueError("a duplicate semantic candidate cannot receive new progress credit")
        if progress and rank is not None and rank >= self._best_rank.get(state_key, rank + 1):
            raise ValueError("a reported progress rank must strictly decrease in the same semantic state")
        effective_reasons = reasons
        if fingerprint is None and "empty_proposal" not in effective_reasons:
            effective_reasons += ("empty_proposal",)
        if duplicate and "repeated_candidate" not in effective_reasons:
            effective_reasons += ("repeated_candidate",)
        kind = None
        if not progress:
            kind = failure_kind
            if duplicate or (fingerprint is None and kind is None) or accepted:
                kind = FailureKind.NO_PROGRESS
            if kind is None:
                kind = next((self._reason_kinds[reason] for reason in reasons if reason in self._reason_kinds),
                            FailureKind.UNKNOWN_ERROR)
            if not effective_reasons:
                effective_reasons = ("no_progress" if accepted else "proposal_rejected",)
        # All validation precedes mutation, so invalid records remain abandonable.
        counts = self._counts[(state_key, strategy)]
        if fingerprint is not None:
            self._seen.setdefault(state_key, set()).add(fingerprint)
        if progress:
            self._progress += 1
            counts["progress"] += 1
        else:
            counts["failures"] += 1
            order = self._failed_order.setdefault(state_key, [])
            if strategy in order:
                order.remove(strategy)
            order.append(strategy)
        if accepted and rank is not None:
            self._best_rank[state_key] = min(rank, self._best_rank.get(state_key, rank))
        self._pending = None
        return self._event(event="recorded", state_key=state_key, strategy=strategy,
                           branch=pending["branch"], accepted=accepted, progress=progress,
                           candidate_fingerprint=fingerprint, duplicate=duplicate,
                           failure_kind=kind, reasons=effective_reasons, rank=rank, revision=revision,
                           checkpoint_json=pending["checkpoint_json"])

    def abandon(self, *, reasons: tuple[str, ...] = ("attempt_abandoned",),
                failure_kind: FailureKind = FailureKind.UNKNOWN_ERROR,
                revision: int | None = None) -> StrategyEvent:
        """Close a reserved attempt after generation/adapter failure; no refund."""
        if self._pending is None:
            raise ValueError("there is no reserved attempt to abandon")
        return self.record(self._pending["state_key"], self._pending["strategy"], None,
                           accepted=False, progress=False, reasons=reasons,
                           failure_kind=failure_kind, revision=revision)

    def snapshot(self) -> dict:
        """Return detached audit data, not a resumable state or fact checkpoint."""
        pending = self._pending
        return {
            "strategies": list(self._strategies), "attempts": self._attempts,
            "progress": self._progress, "max_attempts": self._max_attempts,
            "failures": sum(counts["failures"] for counts in self._counts.values()),
            "completed_attempts": self._attempts - int(self._pending is not None),
            "max_attempts_per_strategy": self._max_per_strategy,
            "max_failures_per_strategy": self._max_failures,
            "max_audit_events": 3 * self._max_attempts + 1,
            "suppressed_stop_events": self._suppressed_stops,
            "last_stop": None if self._last_stop is None else self._last_stop.to_dict(),
            "branch_budgets": None if self._branch_budgets is None else dict(self._branch_budgets),
            "branch_attempts": dict(self._branches),
            "states": {state: {
                "strategies": {name: dict(counts) for (key, name), counts in self._counts.items() if key == state},
                "seen_candidates": sorted(self._seen.get(state, set())),
                "failed_order": list(self._failed_order.get(state, [])),
                "best_rank": self._best_rank.get(state),
            } for state in sorted({key for key, _ in self._counts})},
            "pending": None if pending is None else {
                "state_key": pending["state_key"], "strategy": pending["strategy"], "branch": pending["branch"],
                "checkpoint": None if pending["checkpoint_json"] is None else json.loads(pending["checkpoint_json"]),
            },
            "events": [event.to_dict() for event in self._events],
        }
