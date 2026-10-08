"""Reviewed action routes, never a store of verified facts or past answers.

Routes only narrow a caller's choice of registered actions. A suggested route
does not prove that any action is applicable or that its output is correct:
the caller must run its normal verifier against the current evidence.

This module is an in-memory reference implementation. Reviewer names record
attribution; they do not authenticate people. Applications supply their own
authentication, persistence, concurrency control, and domain verifiers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field} must be nonempty with no surrounding whitespace")
    return value


def _names(values: Iterable[str], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, Mapping)):
        raise TypeError(f"{field} must be an iterable of names, not a string or mapping")
    return tuple(_text(value, field) for value in values)


def _context(values: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise TypeError("context must be a mapping of strings to strings")
    return {
        _text(key, "context key"): _text(value, "context value")
        for key, value in values.items()
    }


@dataclass(frozen=True)
class Route:
    """An immutable, domain-scoped sequence of action names.

    ``context`` contains exact-match applicability requirements, not free-text
    memory. Every pair must match the query; extra query fields are allowed.
    ``required_metrics`` names evidence capabilities the caller must provide.
    These names are declarations, not independently verified evidence.

    Tuples are required so callers cannot mutate a route through nested lists.
    Actions may repeat, but context keys and required metrics must be unique.
    Registration of action names is checked by :meth:`RouteMemory.add`.
    """

    route_id: str
    domain: str
    context: tuple[tuple[str, str], ...]
    actions: tuple[str, ...]
    required_metrics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.route_id, "route_id")
        _text(self.domain, "domain")
        for name in ("context", "actions", "required_metrics"):
            if not isinstance(getattr(self, name), tuple):
                raise TypeError(f"{name} must be a tuple")
        keys: set[str] = set()
        for pair in self.context:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise TypeError("each context requirement must be a (key, value) tuple")
            key, value = pair
            _text(key, "context key")
            _text(value, "context value")
            if key in keys:
                raise ValueError(f"duplicate context key: {key}")
            keys.add(key)
        if not self.actions:
            raise ValueError("actions must contain at least one registered action name")
        _names(self.actions, "action")
        metrics = _names(self.required_metrics, "required metric")
        if len(metrics) != len(set(metrics)):
            raise ValueError("required_metrics must be unique")


def _route_payload(route: Route) -> dict[str, Any]:
    return {
        "route_id": route.route_id,
        "domain": route.domain,
        "context": [list(pair) for pair in route.context],
        "actions": list(route.actions),
        "required_metrics": list(route.required_metrics),
    }


class RouteMemory:
    """Approve and revoke routes before suggesting them under hard gates.

    Lifecycle: ``add`` creates a pending route, ``review`` approves it, and
    ``revoke`` retires a pending or approved route. Revocation is terminal; a
    replacement requires a new route ID and a fresh review. Routes cannot be
    overwritten or implicitly approved by matching context.

    The registry is an allowlist of names, not executable code. Neither review
    nor suggestion runs actions, validates a reviewer's authority, reads source
    data, or adds facts to a reasoning state.
    """

    def __init__(self, registered_actions: Iterable[str]) -> None:
        self._registered_actions = frozenset(_names(registered_actions, "registered action"))
        self._routes: dict[str, Route] = {}
        self._statuses: dict[str, str] = {}
        self._audit: list[dict[str, Any]] = []

    def add(self, route: Route) -> None:
        """Add a new route as pending; reject duplicate IDs and unknown actions."""
        if not isinstance(route, Route):
            raise TypeError("route must be a Route")
        # Reconstruct to validate fields and detach from the submitted object.
        stored = Route(
            route.route_id, route.domain, route.context,
            route.actions, route.required_metrics,
        )
        if stored.route_id in self._routes:
            raise ValueError(f"route ID already exists: {stored.route_id}")
        unknown = set(stored.actions) - self._registered_actions
        if unknown:
            raise ValueError(f"unregistered action names: {', '.join(sorted(unknown))}")
        self._routes[stored.route_id] = stored
        self._statuses[stored.route_id] = "pending"
        self._record(stored, "add", None, "pending", None, "route submitted for review")

    def review(self, route_id: str, *, reviewer: str, reason: str) -> None:
        """Approve a pending route and record the supplied reviewer and reason."""
        _text(reviewer, "reviewer")
        _text(reason, "reason")
        route = self._lookup(route_id)
        status = self._statuses[route_id]
        if status != "pending":
            raise ValueError(f"only pending routes can be approved; current status: {status}")
        self._statuses[route_id] = "approved"
        self._record(route, "review", status, "approved", reviewer, reason)

    def revoke(self, route_id: str, *, reviewer: str, reason: str) -> None:
        """Retire a pending or approved route immediately, preserving its audit."""
        _text(reviewer, "reviewer")
        _text(reason, "reason")
        route = self._lookup(route_id)
        status = self._statuses[route_id]
        if status == "revoked":
            raise ValueError("route is already revoked")
        self._statuses[route_id] = "revoked"
        self._record(route, "revoke", status, "revoked", reviewer, reason)

    def suggest(
        self,
        *,
        domain: str,
        context: Mapping[str, str],
        available_metrics: Iterable[str],
    ) -> tuple[Route, ...]:
        """Return approved routes whose domain, context, and metrics all match.

        Results preserve insertion order. There is no similarity ranking or
        fallback across domains. Availability is caller-supplied metadata:
        every selected action and resulting claim still needs re-verification
        against current evidence. The method never executes or promotes facts.
        """
        _text(domain, "domain")
        query = _context(context)
        available = frozenset(_names(available_metrics, "available metric"))
        return tuple(
            deepcopy(route)
            for route_id, route in self._routes.items()
            if self._statuses[route_id] == "approved"
            and route.domain == domain
            and all(query.get(key) == value for key, value in route.context)
            and set(route.required_metrics).issubset(available)
        )

    def status(self, route_id: str) -> str:
        """Return ``pending``, ``approved``, or ``revoked``; unknown IDs raise KeyError."""
        self._lookup(route_id)
        return self._statuses[route_id]

    def audit_log(self) -> list[dict[str, Any]]:
        """Return a detached, JSON-serializable history including route snapshots.

        Audit snapshots contain route metadata and reviewer attribution, which
        applications should treat according to their own data policies. This
        local list is not a tamper-proof or authenticated external audit log.
        """
        return deepcopy(self._audit)

    def _lookup(self, route_id: str) -> Route:
        _text(route_id, "route_id")
        if route_id not in self._routes:
            raise KeyError(route_id)
        return self._routes[route_id]

    def _record(
        self,
        route: Route,
        event: str,
        previous_status: str | None,
        status: str,
        reviewer: str | None,
        reason: str,
    ) -> None:
        self._audit.append({
            "sequence": len(self._audit) + 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "route_id": route.route_id,
            "previous_status": previous_status,
            "status": status,
            "reviewer": reviewer,
            "reason": reason,
            "route": _route_payload(route),
        })
