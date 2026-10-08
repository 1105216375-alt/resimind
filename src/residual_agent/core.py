"""Immutable data contracts for a small evidence-bound reasoning runtime.

These are data integrity boundaries, not a sandbox for Python extension code.
Values are JSON scalars or tuples of values; mutable containers are rejected.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
from typing import TypeAlias, Union


JSONScalar: TypeAlias = Union[None, bool, int, float, str]
JSONValue: TypeAlias = Union[JSONScalar, tuple["JSONValue", ...]]


def _text(value: str, name: str, *, empty: bool = False) -> None:
    if type(value) is not str or (not empty and not value.strip()):
        raise ValueError(f"{name} must be a {'possibly empty ' if empty else 'nonempty '}string")


def _value(value: JSONValue) -> None:
    if type(value) in (str, bool, int) or value is None:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is tuple:
        for item in value:
            _value(item)
        return
    raise ValueError("values must be finite JSON scalars or immutable tuples of values")


def _strings(values: tuple[str, ...], name: str, *, nonempty: bool = False) -> None:
    if type(values) is not tuple or (nonempty and not values):
        raise ValueError(f"{name} must be a {'nonempty ' if nonempty else ''}tuple")
    for value in values:
        _text(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must not contain duplicates")


def _objects(values: tuple, item_type: type, name: str) -> None:
    if type(values) is not tuple or any(type(item) is not item_type for item in values):
        raise ValueError(f"{name} must be a tuple of {item_type.__name__}")


def _json_data(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _json_data(getattr(value, field.name)) for field in fields(value)}
    if type(value) is tuple:
        return [_json_data(item) for item in value]
    return value


def canonical_json(value) -> str:
    """Return a deterministic JSON representation of the public data contracts."""
    return json.dumps(_json_data(value), ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def content_digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class Serializable:
    __slots__ = ()

    def to_dict(self) -> dict:
        """Return detached, JSON-compatible data; modifying it cannot change a snapshot."""
        return _json_data(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                          indent=indent, allow_nan=False)


class Decision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    DEFER = "defer"
    INTERRUPT = "interrupt"


@dataclass(frozen=True, slots=True)
class Evidence(Serializable):
    """Registered input, not an automatically verified fact.

    The application chooses sources. The domain verifier must establish that
    subject, metric, value, unit and scope actually support the proposed claim.
    """

    id: str
    subject: str
    metric: str
    value: JSONValue
    unit: str
    source: str
    scope: str = "default"

    def __post_init__(self) -> None:
        for name in ("id", "subject", "metric", "source", "scope"):
            _text(getattr(self, name), name)
        _text(self.unit, "unit", empty=True)
        _value(self.value)


@dataclass(frozen=True, slots=True)
class Fact(Serializable):
    """A verifier's proposed commit, with provenance into registered evidence.

    Constructing a Fact does not commit it; only an accepted, correctly bound
    Verdict can do that. The runtime enforces references, not domain truth.
    """

    id: str
    subject: str
    metric: str
    value: JSONValue
    unit: str
    evidence_refs: tuple[str, ...]
    scope: str = "default"

    def __post_init__(self) -> None:
        for name in ("id", "subject", "metric", "scope"):
            _text(getattr(self, name), name)
        _text(self.unit, "unit", empty=True)
        _value(self.value)
        _strings(self.evidence_refs, "evidence_refs", nonempty=True)

    @property
    def key(self) -> tuple[str, str, str]:
        """One immutable observation slot; differing units or values conflict."""
        return self.subject, self.metric, self.scope


@dataclass(frozen=True, slots=True)
class Candidate(Serializable):
    """Untrusted proposal. It has no verified, safe, solved or decision field."""

    id: str
    action: str
    target: str
    claim: str
    refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("id", "action", "target", "claim"):
            _text(getattr(self, name), name)
        _strings(self.refs, "refs")


@dataclass(frozen=True, slots=True)
class State(Serializable):
    """Append-only committed facts. Revisions advance only when facts are added."""

    revision: int = 0
    facts: tuple[Fact, ...] = ()

    def __post_init__(self) -> None:
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a nonnegative integer")
        _objects(self.facts, Fact, "facts")
        if len({fact.id for fact in self.facts}) != len(self.facts):
            raise ValueError("fact ids must be unique")


@dataclass(frozen=True, slots=True)
class Residual(Serializable):
    """Outstanding obligations computed by the trusted domain adapter.

    An empty residual means solved only according to that adapter's contract.
    Hard constraints are outstanding checks/violations and block completion.
    """

    goals: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    hard_constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("goals", "unknowns", "hard_constraints"):
            _strings(getattr(self, name), name)
        _strings(self.pending, "residual obligation ids")

    @property
    def pending(self) -> tuple[str, ...]:
        return self.goals + self.unknowns + self.hard_constraints

    @property
    def solved(self) -> bool:
        return not self.pending

    @property
    def measure(self) -> int:
        return len(self.pending)


@dataclass(frozen=True, slots=True)
class Binding(Serializable):
    """Content binding, not a signature or proof of verifier correctness."""

    candidate_digest: str
    state_revision: int
    state_digest: str
    evidence_digest: str

    def __post_init__(self) -> None:
        for name in ("candidate_digest", "state_digest", "evidence_digest"):
            value = getattr(self, name)
            if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError(f"{name} must be a SHA-256 hex digest")
        if type(self.state_revision) is not int or self.state_revision < 0:
            raise ValueError("state_revision must be a nonnegative integer")

    @classmethod
    def for_candidate(cls, candidate: Candidate, state: State,
                      evidence: tuple[Evidence, ...]) -> Binding:
        return cls(content_digest(candidate), state.revision,
                   content_digest(state), content_digest(evidence))


@dataclass(frozen=True, slots=True)
class Verdict(Serializable):
    """Returned only by the trusted verifier; commit independently checks binding."""

    decision: Decision
    binding: Binding
    facts: tuple[Fact, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.decision) is not Decision or type(self.binding) is not Binding:
            raise ValueError("verdict requires a Decision and Binding")
        _objects(self.facts, Fact, "facts")
        _strings(self.reasons, "reasons")

    @classmethod
    def for_candidate(cls, candidate: Candidate, state: State, decision: Decision,
                      *, evidence: tuple[Evidence, ...], facts: tuple[Fact, ...] = (),
                      reasons: tuple[str, ...] = ()) -> Verdict:
        return cls(decision, Binding.for_candidate(candidate, state, evidence), facts, reasons)


@dataclass(frozen=True, slots=True)
class TraceEvent(Serializable):
    """An immutable before/after record, including rejected attempts.

    Positive step numbers identify proposal attempts. Step zero denotes a
    setup or observer diagnostic and does not consume another attempt.
    """

    step: int
    proposer: str
    decision: Decision
    reasons: tuple[str, ...]
    before: State
    after: State
    residual_before: Residual
    residual_after: Residual
    candidate: Candidate | None = None
    verdict: Verdict | None = None

    def __post_init__(self) -> None:
        if type(self.step) is not int or self.step < 0:
            raise ValueError("step must be a nonnegative integer")
        _text(self.proposer, "proposer")
        _strings(self.reasons, "reasons")
        if type(self.decision) is not Decision:
            raise ValueError("decision must be a Decision")
        for name in ("before", "after"):
            if type(getattr(self, name)) is not State:
                raise ValueError(f"{name} must be a State")
        for name in ("residual_before", "residual_after"):
            if type(getattr(self, name)) is not Residual:
                raise ValueError(f"{name} must be a Residual")
        if self.candidate is not None and type(self.candidate) is not Candidate:
            raise ValueError("candidate must be a Candidate or None")
        if self.verdict is not None and type(self.verdict) is not Verdict:
            raise ValueError("verdict must be a Verdict or None")


@dataclass(frozen=True, slots=True)
class RunResult(Serializable):
    state: State
    residual: Residual
    status: str
    stop_reason: str
    trace: tuple[TraceEvent, ...] = ()
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        if type(self.state) is not State or type(self.residual) is not Residual:
            raise ValueError("result requires a State and Residual")
        _text(self.status, "status")
        _text(self.stop_reason, "stop_reason")
        _objects(self.trace, TraceEvent, "trace")
        _objects(self.evidence, Evidence, "evidence")

    @property
    def steps(self) -> int:
        return sum(event.step > 0 for event in self.trace)
