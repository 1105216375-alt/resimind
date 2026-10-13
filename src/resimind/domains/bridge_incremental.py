"""Reuse checked line-beam results through explicit input dependencies.

Only in-memory, internally verified certificates are reused. The full declared
input is validated on every run. Changed evidence invalidates descendants;
changing the case topology starts a fresh graph. This is the existing
synthetic Euler–Bernoulli model with supplied limits, not code compliance.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json

from ..core import canonical_json, content_digest
from ..obligations import Obligation, ObligationGraph, ObligationUpdate
from . import bridge


@dataclass(frozen=True, slots=True)
class IncrementalBridgeResult:
    status: str
    payload_json: str
    graph: ObligationGraph
    checked_cases: tuple[str, ...]
    reused_cases: tuple[str, ...]
    envelope_checks: int
    comparison_checks: int
    reasons: tuple[str, ...] = ()

    @property
    def payload(self) -> dict:
        return json.loads(self.payload_json)

    @property
    def cases(self) -> dict:
        """Same exact-number format as bridge.verified_case_results."""
        return {case: {"pier_moment": Fraction(row["pier_moment"]),
                       **{key: tuple(map(Fraction, row[key])) for key in ("q", "reactions", "v1", "v2")}}
                for case, row in self.payload.get("cases", {}).items()}


def _graph(problem: bridge.ContinuousBridgeProblem, revision: str) -> ObligationGraph:
    assumptions = ("model", "supports", *bridge._FLAGS, "subject", "scope")
    nodes = [Obligation("model", "validate declared physical assumptions", evidence=assumptions)]
    for case in problem.case_ids:
        combo, pattern = case.split(":")
        inputs = ["L1", "L2", "EI1", "EI2", "dead1", "dead2", f"{combo}:dead_factor"]
        if pattern != "none":
            inputs += [f"{combo}:live_factor"]
        if pattern in ("left", "both"):
            inputs += ["live1"]
        if pattern in ("right", "both"):
            inputs += ["live2"]
        nodes.append(Obligation(case, "verify equilibrium and deformation certificate", ("model",), tuple(inputs)))
    nodes += [Obligation("envelope", "verify exact case envelope", problem.case_ids),
              Obligation("comparisons", "compare supplied limits", ("envelope",),
                         ("moment_limit", "midspan_deflection_limit"))]
    return ObligationGraph(tuple(nodes), evidence_revision=revision)


class IncrementalBridgeAgent:
    """Application-owned cache; returned JSON is detached from saved state."""

    def __init__(self):
        self._graph: ObligationGraph | None = None
        self._evidence: dict[str, str] = {}
        self._cases: tuple[str, ...] = ()
        self._cache: dict[str, str] = {}

    def run(self, problem: bridge.ContinuousBridgeProblem) -> IncrementalBridgeResult:
        if type(problem) is not bridge.ContinuousBridgeProblem:
            raise ValueError("expected ContinuousBridgeProblem")
        evidence = bridge.make_evidence(problem)
        values, missing, errors = bridge._inputs(problem, evidence)
        inputs = {item.metric: canonical_json(item) for item in evidence}
        inputs.update(subject=problem.subject, scope=problem.scope)
        revision = content_digest(inputs)
        if self._graph is None or self._cases != problem.case_ids:
            graph, cache = _graph(problem, revision), {}
        else:
            changed = tuple(sorted(key for key in set(inputs) | set(self._evidence)
                                   if inputs.get(key) != self._evidence.get(key)))
            graph = self._graph.invalidate_evidence(changed, revision=revision)
            cache = dict(self._cache)
        if missing or errors:
            # Old certificates are retained internally but never released for
            # a failed input snapshot. A future valid run compares to the last
            # successfully verified snapshot, so invalid evidence cannot bless it.
            return IncrementalBridgeResult("deferred", "{}", graph, (), (), 0, 0, missing + errors)
        checked, reused = [], []
        envelope_checks = comparison_checks = 0
        for item in graph.plan_slice("comparisons"):
            if any(graph.get(dep).status != "verified" for dep in item.depends_on):
                raise RuntimeError("dependency order was violated")
            if item.id == "model":
                result = {"declared_model_verified": True}
            elif item.id in problem.case_ids:
                proposal = bridge._propose_solution(values, item.id, False)
                result = bridge._certificate(values, proposal, item.id)
                checked.append(item.id)
            elif item.id == "envelope":
                cases = {case: json.loads(cache[case]) for case in problem.case_ids}
                result = bridge._propose_envelope(values, cases, problem.case_ids)
                if result != bridge._envelope(values, cases, problem.case_ids):
                    raise ValueError("envelope verification failed")
                envelope_checks += 1
            else:
                result = bridge._comparisons(values, json.loads(cache["envelope"]))
                comparison_checks += 1
            cache[item.id] = json.dumps(result, sort_keys=True)
            graph = graph.update(ObligationUpdate(item.id, "verified", content_digest(result)))
        if not graph.solved:
            return IncrementalBridgeResult("deferred", "{}", graph, tuple(checked), (),
                                           envelope_checks, comparison_checks, ("pending_obligations",))
        reused = [case for case in problem.case_ids if case not in checked]
        self._graph, self._cache, self._cases, self._evidence = graph, cache, problem.case_ids, inputs
        payload = {"cases": {case: json.loads(cache[case]) for case in problem.case_ids},
                   "envelope": json.loads(cache["envelope"]), "comparisons": json.loads(cache["comparisons"])}
        return IncrementalBridgeResult("verified", json.dumps(payload, sort_keys=True), graph,
                                       tuple(checked), tuple(reused), envelope_checks, comparison_checks)
