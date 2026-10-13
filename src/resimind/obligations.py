"""Dependency-aware residual obligations for verified Agent runs.

An obligation is a pending piece of work, never a truth claim.  The graph
tracks which obligations depend on which evidence or subgoals so a changed
input can invalidate only the affected branch.  Domain verifiers still own
truth; this module only provides deterministic bookkeeping and planning.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Literal

Status = Literal["pending", "verified", "rejected", "unknown", "invalidated"]


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


@dataclass(frozen=True, slots=True)
class Obligation:
    id: str
    goal: str
    depends_on: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    status: Status = "pending"
    detail: str = ""

    def __post_init__(self) -> None:
        _text(self.id, "id"); _text(self.goal, "goal")
        if type(self.depends_on) is not tuple or type(self.evidence) is not tuple:
            raise ValueError("depends_on and evidence must be tuples")
        for values, name in ((self.depends_on, "depends_on"), (self.evidence, "evidence")):
            if any(type(item) is not str or not item.strip() for item in values):
                raise ValueError(f"{name} contains an invalid id")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicates")
        if self.status not in ("pending", "verified", "rejected", "unknown", "invalidated"):
            raise ValueError("invalid obligation status")
        if type(self.detail) is not str:
            raise ValueError("detail must be text")


@dataclass(frozen=True, slots=True)
class ObligationUpdate:
    id: str
    status: Status
    detail: str = ""
    evidence_revision: str | None = None

    def __post_init__(self) -> None:
        _text(self.id, "id")
        if self.status not in ("pending", "verified", "rejected", "unknown", "invalidated"):
            raise ValueError("invalid obligation status")
        if type(self.detail) is not str:
            raise ValueError("detail must be text")
        if self.evidence_revision is not None:
            _text(self.evidence_revision, "evidence_revision")


class ObligationGraph:
    """Immutable-snapshot-friendly DAG with transitive invalidation."""

    def __init__(self, obligations: tuple[Obligation, ...] = (), *, evidence_revision: str = "initial"):
        if type(obligations) is not tuple:
            raise ValueError("obligations must be a tuple")
        if any(type(item) is not Obligation for item in obligations):
            raise ValueError("obligations must contain Obligation values")
        _text(evidence_revision, "evidence_revision")
        self._items = MappingProxyType({item.id: item for item in obligations})
        if len(self._items) != len(obligations):
            raise ValueError("obligation ids must be unique")
        self._validate_graph(self._items)
        self._evidence_revision = evidence_revision

    @property
    def evidence_revision(self) -> str:
        return self._evidence_revision

    @staticmethod
    def _validate_graph(items: dict[str, Obligation]) -> None:
        for item in items.values():
            missing = [dep for dep in item.depends_on if dep not in items]
            if missing:
                raise ValueError(f"missing dependency for {item.id}: {missing[0]}")
        visiting: set[str] = set(); visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("obligation dependency cycle")
            if node in visited:
                return
            visiting.add(node)
            for dep in items[node].depends_on: visit(dep)
            visiting.remove(node); visited.add(node)
        for node in items: visit(node)

    def snapshot(self) -> tuple[Obligation, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def get(self, obligation_id: str) -> Obligation:
        _text(obligation_id, "obligation_id")
        try: return self._items[obligation_id]
        except KeyError as exc: raise KeyError(obligation_id) from exc

    def ready(self) -> tuple[Obligation, ...]:
        """Return pending obligations whose dependencies are verified."""
        result = []
        for item in self._items.values():
            if item.status == "pending" and all(self._items[d].status == "verified" for d in item.depends_on):
                result.append(item)
        return tuple(sorted(result, key=lambda value: value.id))

    def blocked(self) -> tuple[Obligation, ...]:
        return tuple(sorted((item for item in self._items.values()
                             if item.status == "pending" and item not in self.ready()), key=lambda x: x.id))

    def pending(self) -> tuple[Obligation, ...]:
        return tuple(sorted((item for item in self._items.values()
                             if item.status != "verified"), key=lambda x: x.id))

    def update(self, update: ObligationUpdate) -> "ObligationGraph":
        if type(update) is not ObligationUpdate:
            raise ValueError("update must be an ObligationUpdate")
        _text(update.id, "id")
        if update.id not in self._items: raise KeyError(update.id)
        if update.status not in ("pending", "verified", "rejected", "unknown", "invalidated"):
            raise ValueError("invalid update status")
        old = self._items[update.id]
        items = dict(self._items)
        items[update.id] = Obligation(old.id, old.goal, old.depends_on, old.evidence,
                                      update.status, update.detail)
        self._validate_graph(items)
        return ObligationGraph(tuple(items.values()), evidence_revision=update.evidence_revision or self.evidence_revision)

    def invalidate_evidence(self, evidence_ids: tuple[str, ...], *, revision: str) -> "ObligationGraph":
        if type(evidence_ids) is not tuple or any(type(item) is not str or not item for item in evidence_ids):
            raise ValueError("evidence_ids must be a tuple of nonempty strings")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("evidence_ids must not contain duplicates")
        _text(revision, "revision")
        changed = set(evidence_ids)
        affected = {item.id for item in self._items.values() if changed.intersection(item.evidence)}
        # Any dependent of an invalidated node must be rechecked too.
        grew = True
        while grew:
            grew = False
            for item in self._items.values():
                if item.id not in affected and any(dep in affected for dep in item.depends_on):
                    affected.add(item.id); grew = True
        items = dict(self._items)
        for ident in affected:
            old = items[ident]
            items[ident] = Obligation(old.id, old.goal, old.depends_on, old.evidence, "invalidated", "evidence_changed")
        return ObligationGraph(tuple(items.values()), evidence_revision=revision)

    def plan_slice(self, target: str | None = None) -> tuple[Obligation, ...]:
        """Topologically ordered pending obligations needed for target."""
        if target is not None: _text(target, "target")
        wanted = set(self._items) if target is None else set()
        def collect(ident: str) -> None:
            if ident in wanted: return
            wanted.add(ident)
            for dep in self._items[ident].depends_on: collect(dep)
        if target is not None: collect(target)
        result: list[Obligation] = []; visited: set[str] = set()
        def emit(ident: str) -> None:
            if ident in visited: return
            visited.add(ident)
            for dep in self._items[ident].depends_on: emit(dep)
            if self._items[ident].status != "verified":
                result.append(self._items[ident])
        for ident in sorted(wanted): emit(ident)
        return tuple(result)

    @property
    def solved(self) -> bool:
        # Rejection is a failed check, not a proof that the obligation is done.
        # Callers can explicitly reopen it with ``ObligationUpdate(...,
        # "pending")`` after changing the candidate or evidence.
        return all(item.status == "verified" for item in self._items.values())

    def digest(self) -> str:
        payload = {"evidence_revision": self.evidence_revision,
                   "obligations": [item.__dict__ if hasattr(item, "__dict__") else {
                       "id": item.id, "goal": item.goal, "depends_on": item.depends_on,
                       "evidence": item.evidence, "status": item.status, "detail": item.detail}
                       for item in self.snapshot()]}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
