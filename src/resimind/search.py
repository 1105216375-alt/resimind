"""Bounded search over untrusted JSON drafts, with application-owned checks.

The priority is a search heuristic, never a proof. Only a nonempty, unchanged
set of whole-draft checks, all exactly True, can release a plan. Drafts may
temporarily regress while exploring a finite alternative. This module neither
executes model code nor authorizes evidence: the application owns its expander,
verifier, and their data. Callback integrity checks are not a Python sandbox.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import heapq
import json
import math
from time import monotonic
from typing import Callable, Literal, TypeAlias, Union


JSON: TypeAlias = Union[None, bool, int, float, str, list["JSON"], dict[str, "JSON"]]
Checks: TypeAlias = Mapping[str, bool | None]
Verifier: TypeAlias = Callable[[JSON], Checks]
Expander: TypeAlias = Callable[[JSON, Checks], Iterable[JSON]]


@dataclass(frozen=True, slots=True)
class SearchLimits:
    max_expansions: int = 64
    max_generated: int = 256
    max_depth: int = 16
    max_frontier: int = 64
    max_json_bytes: int = 1_048_576
    max_trace: int = 256

    def __post_init__(self) -> None:
        for name in ("max_expansions", "max_depth", "max_trace"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        for name in ("max_generated", "max_frontier", "max_json_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(slots=True)
class SearchResult:
    status: Literal["accepted", "exhausted", "limited", "deferred"]
    reason: str
    plan: JSON = None
    checks: dict[str, bool | None] = field(default_factory=dict)
    best_draft: JSON = None
    best_checks: dict[str, bool | None] = field(default_factory=dict)
    generated: int = 0
    evaluated: int = 0
    expansions: int = 0
    duplicates: int = 0
    invalid: int = 0
    frontier_peak: int = 0
    trace: list[dict] = field(default_factory=list)
    trace_dropped: int = 0

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


class _Stop(Exception):
    def __init__(self, status: str, reason: str):
        self.status, self.reason = status, reason


def _canonical(value: JSON, max_bytes: int) -> bytes:
    """Bound JSON traversal, depth and encoded size without coercing values."""
    active: set[int] = set()
    minimum_bytes = 0

    def charge(amount: int) -> None:
        nonlocal minimum_bytes
        minimum_bytes += amount
        if minimum_bytes > max_bytes:
            raise ValueError("JSON exceeds max_json_bytes")

    def visit(item: JSON, depth: int) -> None:
        charge(1)
        if depth > 64:
            raise ValueError("JSON exceeds 64 levels of nesting")
        kind = type(item)
        if item is None or kind is bool:
            return
        if kind is str:
            charge(len(item))
            return
        if kind is int:
            # Decimal length is >= bit_length / 4; reject enormous integers
            # before converting them, including on Python without a digit cap.
            charge(item.bit_length() // 4)
            return
        if kind is float:
            if not math.isfinite(item):
                raise ValueError("JSON numbers must be finite")
            return
        if kind not in (list, dict):
            raise ValueError("expected exact JSON values")
        if id(item) in active:
            raise ValueError("JSON must not contain cycles")
        active.add(id(item))
        if kind is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                charge(len(key) + 1)
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
        raise ValueError("JSON exceeds max_json_bytes")
    return encoded


def bounded_search(
    initial_draft: JSON,
    *,
    expand: Expander,
    verifier: Verifier,
    limits: SearchLimits = SearchLimits(),
    deadline: float | None = None,
) -> SearchResult:
    """Find a fully checked plan; keep unfinished drafts separate from output.

    Best-first order is: more True checks, fewer unknown checks, fewer False
    checks, shallower depth, then generation order. Names must match the initial
    assessment exactly. JSON structure may change; preservation of task fields,
    evidence bindings and all domain rules belongs in the whole-draft verifier.

    ``max_generated`` includes the initial draft and every yielded successor,
    even an invalid value or duplicate. Each unique valid value is checked
    immediately, including the last allowed one and values that would not fit
    the frontier. ``max_expansions`` counts calls to expand. Successor iteration
    is lazy and stops at the budget; no full iterable is materialized. Frontier
    overflow discards the worst queued draft deterministically and is reported
    as a limit if no plan is found. Search exhaustion is not an infeasibility
    proof: the application controls which successors are proposed.

    ``deadline`` is an optional absolute ``time.monotonic()`` deadline. It is
    checked before and after callbacks and iterator advances; a late result is
    never accepted. This cannot interrupt a stuck Python callback or iterator.
    Hard runtime isolation requires an application-owned worker process.

    Callback exceptions, input mutation, and malformed or changed check sets
    defer the search. Unknown results remain unfinished and can be explored.
    Invalid initial JSON/configuration raises ValueError; invalid successor JSON
    is recorded and skipped. Results and callback inputs are detached snapshots.
    Trace entries contain counts and digests, never full draft payloads.
    """
    if type(limits) is not SearchLimits:
        raise ValueError("limits must be SearchLimits")
    if not callable(expand) or not callable(verifier):
        raise ValueError("expand and verifier must be callable")
    if deadline is not None:
        try:
            valid_deadline = type(deadline) in (int, float) and math.isfinite(deadline)
        except OverflowError:
            valid_deadline = False
        if not valid_deadline:
            raise ValueError("deadline must be a finite monotonic time")
    initial_bytes = _canonical(initial_draft, limits.max_json_bytes)
    best_bytes, best_checks, best_priority = initial_bytes, {}, None
    generated, evaluated, expansions, duplicates, invalid = 1, 0, 0, 0, 0
    frontier_peak, trace_dropped = 0, 0
    trace: list[dict] = []
    frontier: list[tuple[tuple[int, ...], bytes, dict[str, bool | None]]] = []
    seen = {hashlib.sha256(initial_bytes).hexdigest()}
    check_names: frozenset[str] | None = None
    pruned: set[str] = set()

    def check_deadline() -> None:
        if deadline is not None and monotonic() >= deadline:
            raise _Stop("limited", "deadline")

    def record(event: str, **details) -> None:
        nonlocal trace_dropped
        if len(trace) < limits.max_trace:
            trace.append({"event": event, **details})
        else:
            trace_dropped += 1

    def finish(status: str, reason: str, accepted_bytes: bytes | None = None,
               accepted_checks: dict | None = None) -> SearchResult:
        record("stop", status=status, reason=reason)
        return SearchResult(
            status=status, reason=reason,
            plan=json.loads(accepted_bytes) if accepted_bytes is not None else None,
            checks=dict(accepted_checks or {}), best_draft=json.loads(best_bytes),
            best_checks=dict(best_checks), generated=generated, evaluated=evaluated,
            expansions=expansions, duplicates=duplicates, invalid=invalid,
            frontier_peak=frontier_peak, trace=trace, trace_dropped=trace_dropped,
        )

    def guard_unchanged(draft: JSON, original: bytes,
                        checks: dict | None = None, checks_bytes: bytes | None = None) -> None:
        try:
            unchanged = _canonical(draft, limits.max_json_bytes) == original
            if checks is not None:
                unchanged = unchanged and _canonical(checks, limits.max_json_bytes) == checks_bytes
        except (ValueError, RuntimeError):
            unchanged = False
        if not unchanged:
            raise _Stop("deferred", "callback_mutated_input")

    def assess(draft_bytes: bytes, depth: int, sequence: int):
        nonlocal evaluated, check_names, best_bytes, best_checks, best_priority
        check_deadline()
        probe = json.loads(draft_bytes)
        evaluated += 1
        try:
            raw = verifier(probe)
        except Exception:
            check_deadline()
            raise _Stop("deferred", "verifier_exception") from None
        check_deadline()
        guard_unchanged(probe, draft_bytes)
        try:
            if not isinstance(raw, Mapping):
                raise ValueError("checks must be a mapping")
            checks: dict[str, bool | None] = {}
            name_bytes = 0
            for name, value in raw.items():
                check_deadline()
                if type(name) is not str or not name.strip() or name in checks:
                    raise ValueError("invalid check name")
                if value is not None and type(value) is not bool:
                    raise ValueError("invalid check result")
                name_bytes += len(name) + 1
                if name_bytes > limits.max_json_bytes:
                    raise ValueError("checks exceed size budget")
                checks[name] = value
            if not checks:
                raise ValueError("checks must not be empty")
            _canonical(checks, limits.max_json_bytes)
        except _Stop:
            raise
        except Exception:
            raise _Stop("deferred", "invalid_assessment") from None
        check_deadline()
        guard_unchanged(probe, draft_bytes)
        names = frozenset(checks)
        if check_names is None:
            check_names = names
        elif names != check_names:
            raise _Stop("deferred", "changed_check_names")
        passed = sum(value is True for value in checks.values())
        unknown = sum(value is None for value in checks.values())
        failed = len(checks) - passed - unknown
        priority = (-passed, unknown, failed, depth, sequence)
        if best_priority is None or priority < best_priority:
            best_bytes, best_checks, best_priority = draft_bytes, checks, priority
        record("assessed", sha256=hashlib.sha256(draft_bytes).hexdigest(),
               depth=depth, passed=passed, failed=failed, unknown=unknown)
        return checks, priority, passed == len(checks)

    def enqueue(draft_bytes: bytes, checks: dict, priority: tuple[int, ...]) -> None:
        nonlocal frontier_peak
        if priority[3] >= limits.max_depth:
            pruned.add("max_depth")
            record("pruned", reason="max_depth", depth=priority[3])
            return
        heapq.heappush(frontier, (priority, draft_bytes, checks))
        if len(frontier) > limits.max_frontier:
            worst = max(range(len(frontier)), key=lambda i: frontier[i][0])
            discarded = frontier.pop(worst)
            heapq.heapify(frontier)
            pruned.add("max_frontier")
            record("pruned", reason="max_frontier",
                   sha256=hashlib.sha256(discarded[1]).hexdigest())
        frontier_peak = max(frontier_peak, len(frontier))

    try:
        checks, priority, accepted = assess(initial_bytes, 0, 0)
        if accepted:
            return finish("accepted", "all_checks_passed", initial_bytes, checks)
        enqueue(initial_bytes, checks, priority)
        while frontier:
            check_deadline()
            if generated >= limits.max_generated:
                raise _Stop("limited", "max_generated")
            if expansions >= limits.max_expansions:
                raise _Stop("limited", "max_expansions")
            priority, draft_bytes, checks = heapq.heappop(frontier)
            depth = priority[3]
            probe, probe_checks = json.loads(draft_bytes), dict(checks)
            checks_bytes = _canonical(probe_checks, limits.max_json_bytes)
            try:
                check_deadline()
                expansions += 1
                record("expanded", sha256=hashlib.sha256(draft_bytes).hexdigest(), depth=depth)
                successors = expand(probe, probe_checks)
                check_deadline()
                guard_unchanged(probe, draft_bytes, probe_checks, checks_bytes)
                iterator = iter(successors)
                check_deadline()
                guard_unchanged(probe, draft_bytes, probe_checks, checks_bytes)
            except _Stop:
                raise
            except Exception:
                check_deadline()
                raise _Stop("deferred", "expander_exception") from None
            while True:
                check_deadline()
                if generated >= limits.max_generated:
                    raise _Stop("limited", "max_generated")
                try:
                    successor = next(iterator)
                except StopIteration:
                    check_deadline()
                    guard_unchanged(probe, draft_bytes, probe_checks, checks_bytes)
                    break
                except Exception:
                    check_deadline()
                    raise _Stop("deferred", "successor_exception") from None
                generated += 1
                check_deadline()
                guard_unchanged(probe, draft_bytes, probe_checks, checks_bytes)
                try:
                    successor_bytes = _canonical(successor, limits.max_json_bytes)
                except (ValueError, RuntimeError):
                    invalid += 1
                    record("invalid_successor", depth=depth + 1)
                    continue
                digest = hashlib.sha256(successor_bytes).hexdigest()
                if digest in seen:
                    duplicates += 1
                    record("duplicate", sha256=digest, depth=depth + 1)
                    continue
                seen.add(digest)
                checks, child_priority, accepted = assess(successor_bytes, depth + 1, generated - 1)
                if accepted:
                    return finish("accepted", "all_checks_passed", successor_bytes, checks)
                enqueue(successor_bytes, checks, child_priority)
        if pruned:
            return finish("limited", ",".join(sorted(pruned)))
        return finish("exhausted", "no_remaining_drafts")
    except _Stop as stopped:
        return finish(stopped.status, stopped.reason)
