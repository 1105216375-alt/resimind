"""Transparent, frozen strategy selection from execution experience.

This is a small contextual empirical policy, deliberately dependency-free. It
learns which already-available strategy tends to succeed at what cost; it does
not certify facts, alter verifiers, or turn traces into rules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Iterable


@dataclass(frozen=True, slots=True)
class StrategyObservation:
    context: str
    strategy: str
    success: bool
    cost: float

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() for value in (self.context, self.strategy)):
            raise ValueError("context and strategy must be nonempty strings")
        if type(self.success) is not bool or type(self.cost) not in (int, float):
            raise ValueError("success must be bool and cost must be numeric")
        if not math.isfinite(float(self.cost)) or float(self.cost) < 0:
            raise ValueError("cost must be finite and nonnegative")


class StrategyPolicy:
    """Smoothed success-per-cost ranking with an explicit frozen snapshot."""

    VERSION = "empirical-policy-v1"

    def __init__(self, observations: Iterable[StrategyObservation] = (), *, min_evidence: int = 2):
        if type(min_evidence) is not int or min_evidence < 1 or min_evidence > 1000:
            raise ValueError("min_evidence must be in 1..1000")
        self.min_evidence = min_evidence
        self._records = tuple(observations)
        if any(type(item) is not StrategyObservation for item in self._records):
            raise ValueError("observations must contain StrategyObservation values")
        self._frozen = False
        self._table: dict[tuple[str, str], tuple[int, int, float]] = {}

    @classmethod
    def fit(cls, observations: Iterable[StrategyObservation], *, min_evidence: int = 2) -> "StrategyPolicy":
        policy = cls(tuple(observations), min_evidence=min_evidence)
        policy._fit()
        policy._frozen = True
        return policy

    def _fit(self) -> None:
        table: dict[tuple[str, str], list[float]] = {}
        for record in self._records:
            key = (record.context, record.strategy)
            row = table.setdefault(key, [0.0, 0.0, 0.0])
            row[0] += 1; row[1] += int(record.success); row[2] += float(record.cost)
        self._table = {key: (int(row[0]), int(row[1]), row[2] / row[0]) for key, row in table.items()}

    @property
    def frozen(self) -> bool:
        return self._frozen

    def rank(self, context: str, available: Iterable[str]) -> tuple[str, ...]:
        if type(context) is not str or not context.strip(): raise ValueError("context must be nonempty")
        names = tuple(available)
        if not names or any(type(item) is not str or not item.strip() for item in names):
            raise ValueError("available must contain strategy names")
        if len(set(names)) != len(names): raise ValueError("available strategies must be unique")
        scored = []
        for index, name in enumerate(names):
            count, successes, mean_cost = self._table.get((context, name), (0, 0, 0.0))
            if count < self.min_evidence:
                # Unknown strategies retain caller order; exploration is explicit.
                score = None
            else:
                # Laplace-smoothed reliability, with cost as a secondary factor.
                reliability = (successes + 1.0) / (count + 2.0)
                score = reliability / (1.0 + mean_cost)
            scored.append((score, count, index, name))
        known = sorted((row for row in scored if row[0] is not None), key=lambda row: (-row[0], -row[1], row[2]))
        unknown = [row for row in scored if row[0] is None]
        return tuple(row[3] for row in known + unknown)

    def observe(self, record: StrategyObservation) -> None:
        """Add experience only before fitting; fitted policies are immutable."""
        if self._frozen: raise RuntimeError("fitted policy is frozen")
        if type(record) is not StrategyObservation: raise ValueError("record must be StrategyObservation")
        self._records += (record,); self._fit()

    def to_json(self) -> str:
        payload = {"version": self.VERSION, "min_evidence": self.min_evidence,
                   "observations": [asdict(item) for item in self._records],
                   "frozen": self._frozen}
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "StrategyPolicy":
        if type(text) is not str: raise ValueError("policy JSON must be text")
        try: payload = json.loads(text)
        except (ValueError, TypeError) as exc: raise ValueError("invalid policy JSON") from exc
        if type(payload) is not dict or payload.get("version") != cls.VERSION or type(payload.get("observations")) is not list:
            raise ValueError("unsupported policy snapshot")
        records = []
        for row in payload["observations"]:
            if type(row) is not dict or set(row) != {"context", "strategy", "success", "cost"}:
                raise ValueError("invalid policy observation")
            records.append(StrategyObservation(**row))
        policy = cls(records, min_evidence=payload.get("min_evidence", 2))
        policy._fit()
        if payload.get("frozen") is not True: raise ValueError("only frozen snapshots may be loaded")
        policy._frozen = True
        return policy

    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()
