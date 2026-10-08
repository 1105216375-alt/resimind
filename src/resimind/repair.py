"""Evidence-referenced, atomic local repairs for JSON agent plans.

Proposals, hashes and evidence references never establish correctness. A trusted
application supplies the reference allowlist and a whole-plan verifier. Only
strict progress under the same checks can commit a repair; completion requires
every check to pass. These integrity checks are not a sandbox for Python code.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Callable, Literal, TypeAlias, Union


JSON: TypeAlias = Union[None, bool, int, float, str, list["JSON"], dict[str, "JSON"]]
Checks: TypeAlias = Mapping[str, bool | None]
Verifier: TypeAlias = Callable[[JSON], Checks]

_DEFAULT_BYTES = 1_048_576
_MAX_DEPTH = 64
_INDEX = re.compile(r"0|[1-9][0-9]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class Edit:
    """Replace one existing value; ``path`` is a non-root RFC 6901 pointer.

    ``before`` is an exact, type-sensitive precondition. Each reference must
    already be authorized by the application; references are provenance, not
    proof that the replacement is correct. Add, remove and append are omitted
    deliberately, so independent edits cannot shift one another's list indices.
    """

    path: str
    before: JSON
    value: JSON
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepairProposal:
    base_sha256: str
    edits: tuple[Edit, ...]


@dataclass(frozen=True, slots=True)
class RepairResult:
    status: Literal["accepted", "repaired", "rejected", "deferred"]
    plan: JSON
    reason: str
    before_checks: dict[str, bool | None] = field(default_factory=dict)
    after_checks: dict[str, bool | None] = field(default_factory=dict)
    applied_edits: int = 0

    @property
    def committed(self) -> bool:
        """Intermediate repairs commit, but only ``accepted`` means complete."""
        return self.status in ("accepted", "repaired")


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _canonical(value: JSON, max_bytes: int) -> bytes:
    """Validate before encoding; never silently coerce Python values to JSON."""
    active: set[int] = set()
    # A compact JSON value consumes at least one byte per node. This bounds
    # traversal even when the eventual serialization exceeds the byte budget.
    nodes = 0

    def visit(item: JSON, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > max_bytes:
            raise ValueError("JSON exceeds the byte budget")
        if depth > _MAX_DEPTH:
            raise ValueError("JSON exceeds the maximum nesting depth")
        kind = type(item)
        if item is None or kind in (bool, int):
            return
        if kind is str:
            if len(item) > max_bytes:
                raise ValueError("JSON exceeds the byte budget")
            return
        if kind is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
            return
        if kind not in (list, dict):
            raise ValueError("expected exact JSON scalars, lists and string-keyed dicts")
        if id(item) in active:
            raise ValueError("JSON must not contain cycles")
        active.add(id(item))
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                if len(key) > max_bytes:
                    raise ValueError("JSON exceeds the byte budget")
                visit(child, depth + 1)
        else:
            for child in item:
                visit(child, depth + 1)
        active.remove(id(item))

    visit(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True,
                             allow_nan=False, separators=(",", ":")).encode("ascii")
    except (ValueError, OverflowError, RecursionError) as error:
        raise ValueError("JSON cannot be canonically encoded") from error
    if len(encoded) > max_bytes:
        raise ValueError("JSON exceeds the byte budget")
    return encoded


def plan_sha256(plan: JSON, *, max_json_bytes: int = _DEFAULT_BYTES) -> str:
    """Hash canonical JSON, rejecting non-JSON, non-finite and oversized data."""
    _positive_integer(max_json_bytes, "max_json_bytes")
    return hashlib.sha256(_canonical(plan, max_json_bytes)).hexdigest()


def _pointer(path: str) -> tuple[str, ...]:
    if type(path) is not str or not path.startswith("/"):
        raise ValueError("edit path must be a non-root JSON pointer beginning with /")
    parts = []
    for part in path[1:].split("/"):
        if re.search(r"~(?:[^01]|$)", part):
            raise ValueError("invalid JSON pointer escape")
        # The order matters: ~01 names ~1, not /.
        parts.append(part.replace("~1", "/").replace("~0", "~"))
    return tuple(parts)


def _slot(container: JSON, part: str) -> str | int:
    if type(container) is dict:
        if part not in container:
            raise ValueError("edit path does not exist")
        return part
    if type(container) is list:
        if not _INDEX.fullmatch(part):
            raise ValueError("array path must use a canonical nonnegative integer index")
        # Avoid conversion of a potentially enormous decimal string.
        if len(part) > len(str(len(container))) or int(part) >= len(container):
            raise ValueError("array index is out of range")
        return int(part)
    raise ValueError("edit path traverses a scalar")


def _parent(plan: JSON, parts: tuple[str, ...]) -> tuple[list | dict, str | int]:
    current = plan
    for part in parts[:-1]:
        current = current[_slot(current, part)]  # type: ignore[index]
    slot = _slot(current, parts[-1])
    return current, slot  # type: ignore[return-value]


def _check(verifier: Verifier, plan_bytes: bytes, max_bytes: int) -> dict[str, bool | None]:
    probe = json.loads(plan_bytes)
    checks = verifier(probe)
    if _canonical(probe, max_bytes) != plan_bytes:
        raise ValueError("verifier mutated its input")
    if not isinstance(checks, Mapping) or not checks:
        raise ValueError("verifier must return a nonempty mapping of named checks")
    snapshot = dict(checks)
    for name, value in snapshot.items():
        if type(name) is not str or not name.strip():
            raise ValueError("verifier check names must be nonempty strings")
        if value is not None and type(value) is not bool:
            raise ValueError("verifier check results must be bool or None")
    return snapshot


def apply_repair(
    base_plan: JSON,
    proposal: RepairProposal,
    *,
    evidence_ids: Collection[str],
    verifier: Verifier,
    max_edits: int = 8,
    max_json_bytes: int = _DEFAULT_BYTES,
) -> RepairResult:
    """Atomically check a bounded local repair, preserving inputs on every path.

    The application, never a proposal/model, owns ``evidence_ids`` and
    ``verifier``. The verifier receives a fresh detached copy of each entire
    plan. Mutation, exceptions or invalid checker output defer the transaction.
    Both passes must return the same nonempty check names. A commit requires
    at least one additional True check and preservation of every previous True.
    A known failed check becoming None defers instead of hiding a violation.
    None remains unfinished work. Unknown checks never count as passing.

    Invalid application configuration or a non-JSON base raises ValueError.
    Invalid untrusted proposals return rejected. Rejected/deferred results
    contain a detached copy of the original, with zero applied edits. There
    is no execution of a model's code, automatic tool call or external write.
    Limits bound data size and edit count, not a trusted callback's runtime.
    """
    _positive_integer(max_edits, "max_edits")
    _positive_integer(max_json_bytes, "max_json_bytes")
    if not callable(verifier):
        raise ValueError("verifier must be callable")
    if isinstance(evidence_ids, (str, bytes)) or not isinstance(evidence_ids, Collection):
        raise ValueError("evidence_ids must be an application-supplied collection")
    if any(type(ref) is not str or not ref.strip() for ref in evidence_ids):
        raise ValueError("evidence_ids must contain nonempty strings")
    allowed_refs = frozenset(evidence_ids)
    base_bytes = _canonical(base_plan, max_json_bytes)
    original = json.loads(base_bytes)

    def stop(status, reason, before=None, after=None):
        return RepairResult(status, original, reason, before or {}, after or {}, 0)

    try:
        if type(proposal) is not RepairProposal:
            raise ValueError("proposal must be a RepairProposal")
        if (type(proposal.base_sha256) is not str
                or not _SHA256.fullmatch(proposal.base_sha256)
                or proposal.base_sha256 != hashlib.sha256(base_bytes).hexdigest()):
            raise ValueError("proposal base hash does not match the current plan")
        if type(proposal.edits) is not tuple or not 1 <= len(proposal.edits) <= max_edits:
            raise ValueError("proposal must contain a nonempty tuple within the edit limit")
        paths: list[tuple[str, ...]] = []
        prepared = []
        for edit in proposal.edits:
            if type(edit) is not Edit:
                raise ValueError("proposal edits must be Edit values")
            if type(edit.path) is str and len(edit.path) > max_json_bytes:
                raise ValueError("edit path exceeds the byte budget")
            parts = _pointer(edit.path)
            if len(parts) > _MAX_DEPTH:
                raise ValueError("edit path exceeds the maximum nesting depth")
            for existing in paths:
                if existing[:len(parts)] == parts or parts[:len(existing)] == existing:
                    raise ValueError("edit paths must not overlap")
            paths.append(parts)
            refs = edit.evidence_refs
            if (type(refs) is not tuple or not refs
                    or any(type(ref) is not str or not ref.strip() for ref in refs)):
                raise ValueError("each edit requires a nonempty tuple of evidence references")
            if len(set(refs)) != len(refs) or any(ref not in allowed_refs for ref in refs):
                raise ValueError("edit has duplicate or unregistered evidence references")
            container, slot = _parent(original, parts)
            if _canonical(container[slot], max_json_bytes) != _canonical(edit.before, max_json_bytes):
                raise ValueError("edit expected-before does not match the current value")
            value_bytes = _canonical(edit.value, max_json_bytes)
            prepared.append((parts, value_bytes))
        candidate = json.loads(base_bytes)
        for parts, value_bytes in prepared:
            container, slot = _parent(candidate, parts)
            container[slot] = json.loads(value_bytes)
        candidate_bytes = _canonical(candidate, max_json_bytes)
    except ValueError as error:
        return stop("rejected", str(error))

    try:
        before = _check(verifier, base_bytes, max_json_bytes)
    except Exception as error:
        return stop("deferred", f"base verification failed: {type(error).__name__}")
    try:
        after = _check(verifier, candidate_bytes, max_json_bytes)
    except Exception as error:
        return stop("deferred", f"candidate verification failed: {type(error).__name__}", before)
    if before.keys() != after.keys():
        return stop("deferred", "verifier check names changed", before, after)
    if any(passed is True and after[name] is not True for name, passed in before.items()):
        return stop("rejected", "repair regresses a previously passed check", before, after)
    if any(passed is False and after[name] is None for name, passed in before.items()):
        return stop("deferred", "a previously failed check became unknown", before, after)
    if sum(value is True for value in after.values()) <= sum(value is True for value in before.values()):
        return stop("rejected", "repair makes no strict progress in unresolved checks", before, after)
    complete = all(value is True for value in after.values())
    return RepairResult(
        "accepted" if complete else "repaired", candidate,
        "all checks passed" if complete else "strict progress; unresolved checks remain",
        before, after, len(prepared),
    )
