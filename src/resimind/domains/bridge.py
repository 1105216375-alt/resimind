"""Evidence-bound analysis of a synthetic two-span continuous bridge line beam.

The untrusted proposer solves the three-moment equation. The verifier instead
checks a polynomial certificate: equilibrium, curvature, support displacement,
and pier rotation continuity. Every declared combination and all four live-load
patterns must be verified before an envelope can be committed.

This is a linear Euler–Bernoulli teaching model with supplied comparison limits,
not a real bridge capacity or code-compliance assessment. Deflections checked
here are at the two midspans, NOT the global displacement extrema.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
import json
import re

from ..adapters import ModelProposer
from ..agent import Agent, AgentResult, Task
from ..core import Candidate, Decision, Evidence, Fact, Residual, State, TraceEvent, Verdict, canonical_json
from ..units import DIMENSIONLESS, FORCE, LENGTH, UnitRegistry

DOMAIN = "continuous_bridge"
MODEL_TARGET = "bridge:model"
ENVELOPE_TARGET = "bridge:envelope"
CHECK_TARGET = "bridge:comparisons"
ACTIONS = ("declare_bridge_model", "solve_continuous_case", "envelope_bridge_cases", "compare_bridge_limits")
PATTERNS = ("none", "left", "right", "both")
_REGISTRY = UnitRegistry()
for _name, _dimension, _scale in (
    ("Npm", FORCE / LENGTH, 1), ("kNpm", FORCE / LENGTH, 1000),
    ("Nm", FORCE * LENGTH, 1), ("kNm", FORCE * LENGTH, 1000),
    ("Nm2", FORCE * LENGTH ** 2, 1), ("kNm2", FORCE * LENGTH ** 2, 1000),
):
    _REGISTRY.register(_name, _dimension, Fraction(_scale))


@dataclass(frozen=True, slots=True)
class LoadCombination:
    """User-supplied factors; names such as 'amplified' imply no design standard."""
    name: str
    dead_factor: int | str = 1
    live_factor: int | str = 1

    def __post_init__(self) -> None:
        if type(self.name) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", self.name):
            raise ValueError("combination name must be a short lowercase identifier")


@dataclass(frozen=True, slots=True)
class ContinuousBridgeProblem:
    """Two unequal spans, individual constant EI, and downward uniform loads.

    Exact integers or rational/decimal strings only; no binary floats. Missing
    numerical data or undeclared assumptions remain unresolved. EI includes the
    chosen section stiffness; the adapter does not establish it from materials.
    All three supports restrain vertical movement in either direction. A
    negative reaction is reported as uplift demand, not clipped to zero.
    """
    subject: str = "synthetic-continuous-bridge"
    scope: str = "declared-line-beam-v1"
    spans: tuple[int | str | None, int | str | None] = (24, 30)
    span_unit: str = "m"
    flexural_rigidities: tuple[int | str | None, int | str | None] = (36000000000, 48000000000)
    rigidity_unit: str = "Nm2"
    dead_loads: tuple[int | str | None, int | str | None] = (35, 35)
    live_loads: tuple[int | str | None, int | str | None] = (25, 25)
    load_unit: str = "kNpm"
    combinations: tuple[LoadCombination, ...] = (
        LoadCombination("baseline", 1, 1), LoadCombination("amplified", "1.2", "1.5"),
    )
    moment_limit: int | str | None = 9000
    moment_unit: str = "kNm"
    midspan_deflection_limit: int | str | None = 40
    deflection_unit: str = "mm"
    model: str | None = "two_span_euler_bernoulli"
    supports: str | None = "three_vertical_restraints_free_end_rotations"
    linear_elastic: bool | None = None
    small_deflection: bool | None = None
    prismatic_per_span: bool | None = None
    no_support_settlement: bool | None = None
    continuous_at_pier: bool | None = None

    def __post_init__(self) -> None:
        for key in ("subject", "scope"):
            val = getattr(self, key)
            if type(val) is not str or not val.strip() or val != val.strip():
                raise ValueError(f"{key} must be a nonempty trimmed string")
        for key in ("spans", "flexural_rigidities", "dead_loads", "live_loads"):
            val = getattr(self, key)
            if type(val) is not tuple or len(val) != 2:
                raise ValueError(f"{key} must contain exactly two entries")
        if (type(self.combinations) is not tuple or not 1 <= len(self.combinations) <= 12
                or any(type(c) is not LoadCombination for c in self.combinations)
                or len({c.name for c in self.combinations}) != len(self.combinations)):
            raise ValueError("provide 1–12 uniquely named LoadCombination objects")

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(f"{c.name}:{p}" for c in self.combinations for p in PATTERNS)


_FLAGS = ("linear_elastic", "small_deflection", "prismatic_per_span", "no_support_settlement", "continuous_at_pier")


def make_evidence(problem: ContinuousBridgeProblem) -> tuple[Evidence, ...]:
    rows: list[tuple[str, object, str]] = [("model", problem.model, ""), ("supports", problem.supports, "")]
    rows.extend((key, getattr(problem, key), "") for key in _FLAGS)
    for key, vals, unit in (
        ("L", problem.spans, problem.span_unit), ("EI", problem.flexural_rigidities, problem.rigidity_unit),
        ("dead", problem.dead_loads, problem.load_unit), ("live", problem.live_loads, problem.load_unit),
    ):
        rows.extend((f"{key}{i}", value, unit) for i, value in enumerate(vals, 1))
    for combo in problem.combinations:
        rows.extend(((f"{combo.name}:dead_factor", combo.dead_factor, "1"),
                     (f"{combo.name}:live_factor", combo.live_factor, "1")))
    rows.extend((("moment_limit", problem.moment_limit, problem.moment_unit),
                 ("midspan_deflection_limit", problem.midspan_deflection_limit, problem.deflection_unit)))
    return tuple(Evidence(f"bridge-input:{metric}", problem.subject, metric, value, unit,
                          "user_declared_synthetic_bridge", problem.scope)
                 for metric, value, unit in rows if value is not None)


@dataclass(frozen=True, slots=True)
class BridgeEvidenceTool:
    problem: ContinuousBridgeProblem
    name: str = "declared_continuous_bridge_inputs"

    def collect(self, task: Task) -> tuple[Evidence, ...]:
        if task.domain != DOMAIN:
            raise ValueError("bridge tool requires continuous_bridge domain")
        if any(key in dict(task.context) and dict(task.context)[key] != getattr(self.problem, key)
               for key in ("subject", "scope")):
            raise ValueError("task subject/scope differs from the declared bridge")
        return make_evidence(self.problem)


def _inputs(problem, evidence):
    """Validate evidence against the immutable task snapshot, then normalize SI."""
    missing, errors, values = [], [], {}
    required = ("model", "supports", *_FLAGS, "L1", "L2", "EI1", "EI2", "dead1", "dead2", "live1", "live2",
                *(f"{c.name}:{kind}_factor" for c in problem.combinations for kind in ("dead", "live")),
                "moment_limit", "midspan_deflection_limit")
    declared = {item.metric: item for item in make_evidence(problem)}
    for metric in required:
        rows = [row for row in evidence if row.metric == metric]
        if not rows:
            missing.append(f"missing:{metric}")
            continue
        if len(rows) != 1:
            errors.append(f"ambiguous_input:{metric}")
            continue
        row = rows[0]
        if row.subject != problem.subject or row.scope != problem.scope:
            errors.append(f"wrong_subject_or_scope:{metric}")
            continue
        if metric not in declared or canonical_json(row) != canonical_json(declared[metric]):
            errors.append(f"input_differs_from_declared_problem:{metric}")
            continue
        if metric in ("model", "supports", *_FLAGS):
            expected = ("two_span_euler_bernoulli" if metric == "model" else
                        "three_vertical_restraints_free_end_rotations" if metric == "supports" else True)
            if row.unit or type(row.value) is not type(expected) or row.value != expected:
                errors.append(f"unsupported_assumption:{metric}")
            continue
        dim = (LENGTH if metric.startswith("L") or metric == "midspan_deflection_limit" else
               FORCE * LENGTH ** 2 if metric.startswith("EI") else
               FORCE * LENGTH if metric == "moment_limit" else
               DIMENSIONLESS if metric.endswith("_factor") else FORCE / LENGTH)
        try:
            q = _REGISTRY.quantity(row.value, row.unit)
            if q.dimension != dim:
                errors.append(f"wrong_dimension:{metric}")
            elif q.value < 0 or (q.value == 0 and not (metric.startswith(("dead", "live")) or metric.endswith("_factor"))):
                errors.append(f"invalid_sign:{metric}")
            else:
                values[metric] = q.value
        except (ValueError, TypeError, ZeroDivisionError):
            errors.append(f"invalid_quantity:{metric}")
    if any(row.metric not in required for row in evidence):
        errors.append("unexpected_bridge_evidence")
    return values, tuple(missing), tuple(errors)


def _loads(values, case):
    combo, pattern = case.split(":")
    return tuple(values[f"dead{i}"] * values[f"{combo}:dead_factor"]
                 + values[f"live{i}"] * values[f"{combo}:live_factor"]
                 * int(pattern == "both" or pattern == ("left" if i == 1 else "right")) for i in (1, 2))


def _encode(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _decode(text):
    if type(text) is not str or len(text) > 64000:
        raise ValueError("claim must be JSON text under 64000 characters")
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out
    try:
        return json.loads(text, object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError("JSON nesting limit exceeded") from exc


def _number(value) -> Fraction:
    if type(value) is not str:
        raise ValueError("certificate numbers must be exact strings")
    return _REGISTRY.quantity(value, "1").value


def _poly(coefficients, x, derivative=0):
    value = Fraction(0)
    for power, coefficient in enumerate(coefficients, 1):
        if power < derivative:
            continue
        factor = 1
        for offset in range(derivative):
            factor *= power - offset
        value += coefficient * factor * x ** (power - derivative)
    return value


def _certificate(values, data, case):
    """Independent residual equations. Never invokes the proposal solver."""
    if type(data) is not dict or set(data) != {"case", "q", "pier_moment", "reactions", "v1", "v2"} or data["case"] != case:
        raise ValueError("certificate_case_or_schema_mismatch")
    for key, length in (("q", 2), ("reactions", 3), ("v1", 4), ("v2", 4)):
        if type(data[key]) is not list or len(data[key]) != length:
            raise ValueError("certificate_vector_shape")
    q1, q2 = map(_number, data["q"])
    if (q1, q2) != _loads(values, case):
        raise ValueError("load_combination_or_pattern_mismatch")
    mb = _number(data["pier_moment"])
    ra, rb, rc = map(_number, data["reactions"])
    c1, c2 = (tuple(map(_number, data[key])) for key in ("v1", "v2"))
    l1, l2, ei1, ei2 = (values[key] for key in ("L1", "L2", "EI1", "EI2"))
    if ra + rb + rc != q1*l1 + q2*l2:
        raise ValueError("vertical_equilibrium_failed")
    if rb*l1 + rc*(l1+l2) != q1*l1*l1/2 + q2*l2*(l1+l2/2):
        raise ValueError("global_moment_equilibrium_failed")
    vleft2 = q2*l2 - rc
    if ra*l1 - q1*l1*l1/2 != mb or mb+vleft2*l2-q2*l2*l2/2 != 0:
        raise ValueError("span_end_moments_failed")
    for coefficients, length, ei, mleft, shear, load in (
        (c1, l1, ei1, Fraction(0), ra, q1), (c2, l2, ei2, mb, vleft2, q2),
    ):
        if (2*ei*coefficients[1] != mleft or 6*ei*coefficients[2] != shear
                or 24*ei*coefficients[3] != -load):
            raise ValueError("moment_curvature_failed")
        if _poly(coefficients, length) != 0:
            raise ValueError("support_displacement_failed")
    if _poly(c1, l1, 1) != _poly(c2, Fraction(0), 1):
        raise ValueError("pier_rotation_continuity_failed")
    # Canonicalize all exact values before committing the proof certificate.
    return {"case": case, "q": list(map(str, (q1, q2))), "pier_moment": str(mb),
            "reactions": list(map(str, (ra, rb, rc))), "v1": list(map(str, c1)), "v2": list(map(str, c2))}


def _model_fact(problem):
    return Fact("bridge:declared_model", problem.subject, "declared_model", True, "",
                tuple(row.id for row in make_evidence(problem)), problem.scope)


def _case_fact(problem, data):
    return Fact(f"bridge:solution:{data['case']}", problem.subject, "case_solution", _encode(data), "SI",
                _model_fact(problem).evidence_refs, f"{problem.scope}:{data['case']}")


def _same(left, right):
    return canonical_json(left) == canonical_json(right)


def _verified_cases(problem, values, state):
    cases = {}
    for fact in state.facts:
        if fact.metric != "case_solution":
            continue
        try:
            data = _decode(fact.value)
            case = data["case"]
            if case not in problem.case_ids:
                continue
            normalized = _certificate(values, data, case)
            if _same(fact, _case_fact(problem, normalized)):
                cases[case] = normalized
        except (ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError):
            continue
    return cases


def _envelope(values, cases, case_ids):
    """Trusted aggregation: exact stationary points and endpoints, never sampling."""
    moments, positives, midspans, reactions, piers = [], [[], []], [[], []], [[], [], []], []
    for case in case_ids:
        data = cases[case]
        mb = _number(data["pier_moment"])
        q = list(map(_number, data["q"]))
        r = list(map(_number, data["reactions"]))
        piers.append((mb, case))
        for i in (0, 1):
            length = values[f"L{i+1}"]
            left_moment = Fraction(0) if i == 0 else mb
            shear = r[0] if i == 0 else q[1]*length-r[2]
            xs = [Fraction(0), length]
            if q[i] and 0 < shear/q[i] < length:
                xs.append(shear/q[i])
            samples = [(left_moment + shear*x-q[i]*x*x/2, x) for x in xs]
            peak, where = max(samples, key=lambda item: item[0])
            positives[i].append((peak, case, where))
            moments.extend((abs(moment), case) for moment, x in samples)
            coefficients = tuple(map(_number, data[f"v{i+1}"]))
            midspans[i].append((abs(_poly(coefficients, length/2)), case))
        for i, reaction in enumerate(r):
            reactions[i].append((reaction, case))
    def pair(entry):
        return {"value": str(entry[0]), "case": entry[1]}
    maxmoment = max(moments, key=lambda item: item[0])
    return {
        "cases": list(case_ids),
        "pier_min": pair(min(piers, key=lambda item: item[0])),
        "positive_max": [{**pair(max(rows, key=lambda item: item[0])), "x": str(max(rows, key=lambda item: item[0])[2])} for rows in positives],
        "midspan_abs_max": [pair(max(rows, key=lambda item: item[0])) for rows in midspans],
        "reaction_min": [pair(min(rows, key=lambda item: item[0])) for rows in reactions],
        "moment_abs_max": pair(maxmoment),
    }


def _aggregate_fact(problem, metric, data):
    return Fact(f"bridge:{metric}", problem.subject, metric, _encode(data), "SI" if metric == "envelope" else "",
                _model_fact(problem).evidence_refs, problem.scope)


def _comparisons(values, envelope):
    return {"moment_within_supplied_limit": _number(envelope["moment_abs_max"]["value"]) <= values["moment_limit"],
            "midspans_within_supplied_limit": all(_number(item["value"]) <= values["midspan_deflection_limit"] for item in envelope["midspan_abs_max"]),
            "all_support_reactions_nonnegative": all(_number(item["value"]) >= 0 for item in envelope["reaction_min"])}


class BridgeDomain:
    def __init__(self, problem):
        self.problem = problem

    def rebuild(self, state):
        values, missing, errors = _inputs(self.problem, make_evidence(self.problem))
        goals = [MODEL_TARGET, *(f"bridge:case:{case}" for case in self.problem.case_ids), ENVELOPE_TARGET, CHECK_TARGET]
        valid = []
        if not missing and not errors:
            model = _model_fact(self.problem)
            if any(_same(fact, model) for fact in state.facts):
                goals.remove(MODEL_TARGET)
                valid.append(model)
                cases = _verified_cases(self.problem, values, state)
                for case, data in cases.items():
                    goals.remove(f"bridge:case:{case}")
                    valid.append(_case_fact(self.problem, data))
                if len(cases) == len(self.problem.case_ids):
                    envelope = _envelope(values, cases, self.problem.case_ids)
                    fact = _aggregate_fact(self.problem, "envelope", envelope)
                    if any(_same(item, fact) for item in state.facts):
                        goals.remove(ENVELOPE_TARGET)
                        valid.append(fact)
                        checks = _aggregate_fact(self.problem, "comparisons", _comparisons(values, envelope))
                        if any(_same(item, checks) for item in state.facts):
                            goals.remove(CHECK_TARGET)
                            valid.append(checks)
        invalid = tuple(f"invalid_committed_fact:{fact.id}" for fact in state.facts if not any(_same(fact, expected) for expected in valid))
        return Residual(tuple(goals), missing, errors+invalid)


class BridgeVerifier:
    def __init__(self, problem):
        self.problem = problem

    def verify(self, candidate, state, residual, evidence):
        def finish(decision, reason, facts=()):
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence, facts=facts, reasons=(reason,))
        values, missing, errors = _inputs(self.problem, evidence)
        if errors:
            return finish(Decision.REJECT, errors[0])
        if missing:
            return finish(Decision.DEFER, missing[0])
        if candidate.action not in ACTIONS or candidate.target not in residual.goals:
            return finish(Decision.REJECT, "unsupported_or_resolved_target")
        model = _model_fact(self.problem)
        if candidate.action == ACTIONS[0]:
            if candidate.target != MODEL_TARGET or candidate.claim != "declared_linear_continuous_beam":
                return finish(Decision.REJECT, "model_claim_mismatch")
            if set(candidate.refs) != set(model.evidence_refs):
                return finish(Decision.REJECT, "model_references_mismatch")
            return finish(Decision.ACCEPT, "declared_model_and_dimensions_verified", (model,))
        if not any(_same(fact, model) for fact in state.facts):
            return finish(Decision.DEFER, "declared_model_required")
        if candidate.action == ACTIONS[1]:
            case = candidate.target.removeprefix("bridge:case:")
            if candidate.target != f"bridge:case:{case}" or case not in self.problem.case_ids:
                return finish(Decision.REJECT, "unsupported_case")
            if candidate.refs != (model.id,):
                return finish(Decision.REJECT, "case_requires_model_reference")
            try:
                data = _certificate(values, _decode(candidate.claim), case)
            except (ValueError, TypeError, KeyError, ZeroDivisionError, OverflowError) as exc:
                return finish(Decision.REJECT, str(exc))
            return finish(Decision.ACCEPT, "equilibrium_curvature_supports_and_continuity_verified", (_case_fact(self.problem, data),))
        cases = _verified_cases(self.problem, values, state)
        if len(cases) != len(self.problem.case_ids):
            return finish(Decision.DEFER, "all_load_combinations_and_patterns_required")
        envelope = _envelope(values, cases, self.problem.case_ids)
        if candidate.action == ACTIONS[2]:
            if candidate.target != ENVELOPE_TARGET or set(candidate.refs) != {f"bridge:solution:{case}" for case in self.problem.case_ids}:
                return finish(Decision.REJECT, "envelope_case_references_mismatch")
            expected, metric = envelope, "envelope"
        else:
            fact = _aggregate_fact(self.problem, "envelope", envelope)
            if not any(_same(item, fact) for item in state.facts):
                return finish(Decision.DEFER, "verified_envelope_required")
            if candidate.target != CHECK_TARGET or candidate.refs != (fact.id,):
                return finish(Decision.REJECT, "comparison_requires_envelope_reference")
            expected, metric = _comparisons(values, envelope), "comparisons"
        try:
            if _encode(_decode(candidate.claim)) != _encode(expected):
                return finish(Decision.REJECT, f"{metric}_claim_mismatch")
        except (ValueError, TypeError):
            return finish(Decision.REJECT, "invalid_json_claim")
        return finish(Decision.ACCEPT, f"{metric}_verified", (_aggregate_fact(self.problem, metric, expected),))


def _propose_solution(values, case, wrong):
    """Untrusted solver: three-moment equation, separate from certificate checks."""
    l1, l2, e1, e2 = (values[key] for key in ("L1", "L2", "EI1", "EI2"))
    q1, q2 = _loads(values, case)
    mb = -(q1*l1**3/e1 + q2*l2**3/e2) / (8*(l1/e1+l2/e2))
    if wrong:
        mb = Fraction(0)  # Tempting but wrong: split a continuous beam into simple spans.
    ra = q1*l1/2 + mb/l1
    left2 = q2*l2/2 - mb/l2
    reactions = (ra, q1*l1-ra+left2, q2*l2-left2)
    polynomials = []
    for length, ei, mleft, shear, load in ((l1,e1,0,ra,q1), (l2,e2,mb,left2,q2)):
        c2, c3, c4 = mleft/(2*ei), shear/(6*ei), -load/(24*ei)
        c1 = -c2*length-c3*length**2-c4*length**3
        polynomials.append([str(v) for v in (c1,c2,c3,c4)])
    return {"case":case, "q":[str(q1),str(q2)], "pier_moment":str(mb),
            "reactions":list(map(str,reactions)), "v1":polynomials[0], "v2":polynomials[1]}


def _propose_envelope(values, cases, case_ids):
    """Untrusted aggregation implemented independently of the verifier's reducer."""
    out = {"cases":list(case_ids), "positive_max":[None,None], "midspan_abs_max":[None,None], "reaction_min":[None,None,None]}
    def update(key, entry, maximize=True, index=None):
        old = out.get(key) if index is None else out[key][index]
        better = old is None or (_number(entry["value"]) > _number(old["value"]) if maximize else _number(entry["value"]) < _number(old["value"]))
        if better:
            if index is None:
                out[key] = entry
            else:
                out[key][index] = entry
    for case in case_ids:
        row = cases[case]
        moment = Fraction(row["pier_moment"])
        update("pier_min", {"case":case,"value":str(moment)}, False)
        reactions = list(map(Fraction,row["reactions"]))
        for i in range(3):
            update("reaction_min", {"case":case,"value":str(reactions[i])}, False, i)
        for i in range(2):
            length = values[f"L{i+1}"]
            load = Fraction(row["q"][i])
            left = Fraction(0) if i == 0 else moment
            shear = reactions[0] if i == 0 else load*length-reactions[2]
            location = min(length,max(Fraction(0),shear/load)) if load else (length if shear > 0 else Fraction(0))
            peak = left+shear*location-load*location**2/2
            update("positive_max", {"case":case,"value":str(peak),"x":str(location)}, True, i)
            for x in (Fraction(0),length,location):
                update("moment_abs_max", {"case":case,"value":str(abs(left+shear*x-load*x*x/2))})
            coeff = list(map(Fraction,row[f"v{i+1}"]))
            x = length/2
            displacement = x*(coeff[0]+x*(coeff[1]+x*(coeff[2]+x*coeff[3])))
            update("midspan_abs_max", {"case":case,"value":str(abs(displacement))}, True, i)
    return out


class BridgeProposer:
    def __init__(self, problem, evidence, *, wrong_first=True):
        self.problem, self.evidence = problem, evidence
        self.correct = not wrong_first
        self.attempt = 0

    def observe(self, event: TraceEvent):
        if event.decision is Decision.REJECT and "pier_rotation_continuity_failed" in event.reasons:
            self.correct = True

    def propose(self, state, residual):
        self.attempt += 1
        values, missing, errors = _inputs(self.problem, self.evidence)
        target = residual.goals[0]
        if target == MODEL_TARGET or missing or errors:
            action, claim, refs = ACTIONS[0], "declared_linear_continuous_beam", tuple(row.id for row in self.evidence)
        elif target.startswith("bridge:case:"):
            action, refs = ACTIONS[1], ("bridge:declared_model",)
            claim = _encode(_propose_solution(values, target.removeprefix("bridge:case:"), not self.correct))
        else:
            cases = {data["case"]:data for fact in state.facts if fact.metric == "case_solution" for data in (_decode(fact.value),)}
            envelope = _propose_envelope(values, cases, self.problem.case_ids)
            if target == ENVELOPE_TARGET:
                action, claim = ACTIONS[2], _encode(envelope)
                refs = tuple(f"bridge:solution:{case}" for case in self.problem.case_ids)
            else:
                action, refs = ACTIONS[3], ("bridge:envelope",)
                checks = {"moment_within_supplied_limit": Fraction(envelope["moment_abs_max"]["value"]) <= values["moment_limit"],
                          "midspans_within_supplied_limit": max(Fraction(x["value"]) for x in envelope["midspan_abs_max"]) <= values["midspan_deflection_limit"],
                          "all_support_reactions_nonnegative": min(Fraction(x["value"]) for x in envelope["reaction_min"]) >= 0}
                claim = _encode(checks)
        return (Candidate(f"bridge-proposal:{self.attempt}", action, target, claim, refs),)


def build_agent(problem: ContinuousBridgeProblem, complete: Callable[[str], str] | None = None,
                *, wrong_first: bool = True) -> Agent:
    """Build a fresh Agent; complete(prompt)->JSON optionally supplies real LLM proposals.

    Default proposals are deterministic/offline. A model must return the same
    exact rational polynomial certificates. No neural API is bundled or claimed
    to have been benchmarked. See docs/continuous-bridge.md for the full schema.
    """
    if type(problem) is not ContinuousBridgeProblem:
        raise TypeError("problem must be ContinuousBridgeProblem")
    if complete is not None and not callable(complete):
        raise TypeError("complete must be callable or None")
    if type(wrong_first) is not bool:
        raise TypeError("wrong_first must be a bool")
    def proposer_factory(task, routes, evidence):
        if complete is None:
            return (BridgeProposer(problem, evidence, wrong_first=wrong_first),)
        instruction = task.instruction + "\n" + (
            "Use declare_bridge_model for bridge:model, claim 'declared_linear_continuous_beam', refs all evidence IDs. "
            "Then solve_continuous_case for bridge:case:<combination>:<pattern>, refs ['bridge:declared_model']. "
            "Patterns none,left,right,both are all mandatory for every declared combination. Claim is JSON with exactly "
            "case (combination:pattern), q [q1,q2], pier_moment, reactions [RA,RB,RC], v1 [c1,c2,c3,c4], v2 [c1,c2,c3,c4]. "
            "All numeric entries must be exact rational strings in SI. Downward q positive, sagging M positive; "
            "v(x)=c1*x+c2*x^2+c3*x^3+c4*x^4 is upward-positive, EI*v''=M. All supports v=0; rotations continuous at pier. "
            "Next envelope_bridge_cases on bridge:envelope references every bridge:solution:<case> fact. Its JSON claim: "
            "cases ordered as declared combinations then none,left,right,both; pier_min {value,case}; positive_max [{value,case,x},...] "
            "for both spans; midspan_abs_max [{value,case},...] for both spans; reaction_min [{value,case},...] for A,B,C; "
            "moment_abs_max {value,case}. Ties keep first case, then endpoint 0,L,then interior shear-zero point. "
            "Finally compare_bridge_limits on bridge:comparisons refs ['bridge:envelope'], JSON booleans "
            "moment_within_supplied_limit, midspans_within_supplied_limit, all_support_reactions_nonnegative. "
            "These are supplied comparison limits, not code checks or real bridge safety certification."
        )
        return (ModelProposer(complete, allowed_actions=ACTIONS, evidence=evidence, instruction=instruction),)
    return Agent(domain_factory=lambda task:BridgeDomain(problem), verifier_factory=lambda task:BridgeVerifier(problem),
                 proposer_factory=proposer_factory, tools=(BridgeEvidenceTool(problem),),
                 max_steps=len(problem.case_ids)+10, max_no_progress=3)


def demo_problem() -> ContinuousBridgeProblem:
    return ContinuousBridgeProblem(linear_elastic=True, small_deflection=True, prismatic_per_span=True,
                                   no_support_settlement=True, continuous_at_pier=True)


def run_demo() -> AgentResult:
    problem = demo_problem()
    return build_agent(problem).run(Task("two-span-bridge", "Verify all load patterns, continuous-beam response, and supplied limits.",
                                        DOMAIN, (("subject",problem.subject),("scope",problem.scope))))


def verified_case_results(result: AgentResult) -> dict[str, dict]:
    """Read committed case certificates from this adapter's result, as Fractions.

    This convenience reader does not reverify arbitrary caller-constructed
    AgentResults. Use build_agent and its trusted verifier to establish facts.
    """
    out = {}
    for fact in result.run_result.state.facts:
        if fact.metric == "case_solution":
            row = _decode(fact.value)
            out[row["case"]] = {"pier_moment":Fraction(row["pier_moment"]),
                                **{key:tuple(map(Fraction,row[key])) for key in ("q","reactions","v1","v2")}}
    return out


def verified_summary(result: AgentResult) -> dict:
    """Return the committed envelope and comparisons, preserving exact strings."""
    return {fact.metric:_decode(fact.value) for fact in result.run_result.state.facts if fact.metric in ("envelope","comparisons")}
