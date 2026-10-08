"""A reusable Agent adapter for a declared, uniform bar in static axial tension.

This is a synthetic arithmetic example, not a design or code-compliance check.
The caller supplies every load, geometry, assumption, and comparison limit.
The adapter verifies nominal stress F/A, then compares a committed stress fact
with that limit. ``solved`` means both calculations are complete, including
when ``within_allowable_stress`` is false. Assumption declarations are inputs;
this adapter cannot establish that a real member satisfies them.

The model assumes concentric, static tensile loading of a uniform section,
away from load application points and other local stress concentrations.
Formula/background: https://mechref.engr.illinois.edu/sol/stress.html
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from ..adapters import ModelProposer
from ..agent import Agent, AgentResult, Task
from ..core import Candidate, Decision, Evidence, Fact, Residual, State, TraceEvent, Verdict, canonical_json
from ..units import AREA, FORCE, PRESSURE, Quantity, UnitRegistry


DOMAIN = "engineering"
STRESS_ACTION = "calculate_nominal_stress"
LIMIT_ACTION = "compare_allowable_stress"
STRESS_TARGET = "engineering:nominal_stress"
LIMIT_TARGET = "engineering:limit_comparison"
STRESS_INPUTS = ("force", "area", "model", "axial_static", "is_uniform", "no_local_effects")
INPUTS = STRESS_INPUTS + ("allowable_stress",)
_REGISTRY = UnitRegistry()


@dataclass(frozen=True, slots=True)
class AxialBarProblem:
    """Declared data for one member and one load case.

    Quantity values are exact integers or rational/decimal strings; floats
    are rejected during verification. ``None`` records a missing input.
    Set the three assumption flags explicitly to True only when they are
    part of the declared problem. ``axial_static`` includes concentric axial
    loading; ``no_local_effects`` excludes the disturbed end regions.
    All limits here are user-supplied synthetic values, with no standard or
    regulatory meaning. Invalid data remain visible as unresolved residuals.
    """

    subject: str = "synthetic-bar"
    scope: str = "synthetic-case-1"
    force_value: int | str | None = 100
    force_unit: str = "kN"
    area_value: int | str | None = 1000
    area_unit: str = "mm2"
    allowable_stress_value: int | str | None = 150
    allowable_stress_unit: str = "MPa"
    model: str | None = "uniform_axial_bar"
    axial_static: bool | None = None
    is_uniform: bool | None = None
    no_local_effects: bool | None = None

    def __post_init__(self) -> None:
        for name in ("subject", "scope"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip() or value != value.strip():
                raise ValueError(f"{name} must be a nonempty trimmed string")


def make_evidence(problem: AxialBarProblem) -> tuple[Evidence, ...]:
    """Collect the declarations; collection alone verifies no engineering claim."""
    rows = (
        ("force", problem.force_value, problem.force_unit),
        ("area", problem.area_value, problem.area_unit),
        ("model", problem.model, ""),
        ("axial_static", problem.axial_static, ""),
        ("is_uniform", problem.is_uniform, ""),
        ("no_local_effects", problem.no_local_effects, ""),
        ("allowable_stress", problem.allowable_stress_value, problem.allowable_stress_unit),
    )
    return tuple(
        Evidence(f"input:{metric}", problem.subject, metric, value, unit,
                 "user_declared_synthetic_problem", problem.scope)
        for metric, value, unit in rows if value is not None
    )


@dataclass(frozen=True, slots=True)
class AxialBarEvidenceTool:
    problem: AxialBarProblem
    name: str = "declared_axial_bar_inputs"

    def collect(self, task: Task) -> tuple[Evidence, ...]:
        if task.domain != DOMAIN:
            raise ValueError("axial bar tool requires the engineering domain")
        context = dict(task.context)
        for key in ("subject", "scope"):
            if key in context and context[key] != getattr(self.problem, key):
                raise ValueError("task context differs from the declared problem")
        return make_evidence(self.problem)


@dataclass(frozen=True, slots=True)
class _Inputs:
    records: tuple[Evidence, ...]
    quantities: tuple[tuple[str, Quantity], ...]
    missing: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.records)

    def quantity(self, metric: str) -> Quantity:
        return dict(self.quantities)[metric]


def _read(problem: AxialBarProblem, evidence: tuple[Evidence, ...],
          metrics: tuple[str, ...]) -> _Inputs:
    records: list[Evidence] = []
    quantities: list[tuple[str, Quantity]] = []
    missing: list[str] = []
    errors: list[str] = []
    for metric in metrics:
        items = tuple(item for item in evidence if item.metric == metric)
        if not items:
            missing.append(f"missing:{metric}")
            continue
        if any(item.subject != problem.subject or item.scope != problem.scope for item in items):
            errors.append(f"wrong_subject_or_scope:{metric}")
            continue
        if len(items) != 1:
            errors.append(f"ambiguous_input:{metric}")
            continue
        item = items[0]
        records.append(item)
        if metric in ("model", "axial_static", "is_uniform", "no_local_effects"):
            if item.unit != "":
                errors.append(f"unexpected_assumption_unit:{metric}")
            elif metric == "model":
                if item.value != "uniform_axial_bar":
                    errors.append("unsupported_model")
            elif item.value is not True:
                errors.append(f"unsupported_assumption:{metric}")
            continue
        try:
            quantity = _REGISTRY.quantity(item.value, item.unit)
        except (ValueError, TypeError, ZeroDivisionError):
            errors.append(f"invalid_quantity:{metric}")
            continue
        expected = {"force": FORCE, "area": AREA, "allowable_stress": PRESSURE}[metric]
        if quantity.dimension != expected:
            errors.append(f"wrong_dimension:{metric}")
        elif quantity.value <= 0:
            errors.append(f"nonpositive:{metric}")
        else:
            quantities.append((metric, quantity))
    return _Inputs(tuple(records), tuple(quantities), tuple(missing), tuple(errors))


def _stress_fact(problem: AxialBarProblem, inputs: _Inputs) -> Fact:
    stress = inputs.quantity("force") / inputs.quantity("area")
    if stress.dimension != PRESSURE:
        raise ValueError("force / area must have the stress dimension")
    return Fact("fact:nominal_stress", problem.subject, "nominal_stress",
                str(stress.value), "Pa", inputs.refs, problem.scope)


def _limit_fact(problem: AxialBarProblem, stress: Fact, limit: _Inputs) -> Fact:
    stress_quantity = _REGISTRY.quantity(stress.value, stress.unit)
    within = stress_quantity.value <= limit.quantity("allowable_stress").value
    return Fact("fact:within_allowable_stress", problem.subject,
                "within_allowable_stress", within, "",
                stress.evidence_refs + limit.refs, problem.scope)


def _same_fact(left: Fact, right: Fact | None) -> bool:
    # Dataclass/Python equality equates True with 1; the fact contract does not.
    return right is not None and canonical_json(left) == canonical_json(right)


class AxialBarDomain:
    """Rebuild goals and constraints from declared inputs and exact committed facts."""

    def __init__(self, problem: AxialBarProblem) -> None:
        self.problem = problem

    def rebuild(self, state: State) -> Residual:
        evidence = make_evidence(self.problem)
        stress_inputs = _read(self.problem, evidence, STRESS_INPUTS)
        limit_inputs = _read(self.problem, evidence, ("allowable_stress",))
        goals = [STRESS_TARGET, LIMIT_TARGET]
        errors = list(stress_inputs.errors + limit_inputs.errors)
        missing = stress_inputs.missing + limit_inputs.missing
        expected_stress = None
        if not stress_inputs.errors and not stress_inputs.missing:
            expected_stress = _stress_fact(self.problem, stress_inputs)
            if any(_same_fact(fact, expected_stress) for fact in state.facts):
                goals.remove(STRESS_TARGET)
        expected_limit = None
        if (expected_stress is not None and STRESS_TARGET not in goals
                and not limit_inputs.errors and not limit_inputs.missing):
            expected_limit = _limit_fact(self.problem, expected_stress, limit_inputs)
            if any(_same_fact(fact, expected_limit) for fact in state.facts):
                goals.remove(LIMIT_TARGET)
        for fact in state.facts:
            if not _same_fact(fact, expected_stress) and not _same_fact(fact, expected_limit):
                errors.append(f"invalid_committed_fact:{fact.id}")
        return Residual(tuple(goals), missing, tuple(errors))


class AxialBarVerifier:
    """Recompute claims from scoped inputs; comparison requires a prior stress fact."""

    def __init__(self, problem: AxialBarProblem) -> None:
        self.problem = problem

    def verify(self, candidate: Candidate, state: State, residual: Residual,
               evidence: tuple[Evidence, ...]) -> Verdict:
        def finish(decision: Decision, reason: str, facts: tuple[Fact, ...] = ()) -> Verdict:
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence,
                                         facts=facts, reasons=(reason,))

        targets = {STRESS_ACTION: STRESS_TARGET, LIMIT_ACTION: LIMIT_TARGET}
        if candidate.action not in targets or targets[candidate.action] != candidate.target:
            return finish(Decision.REJECT, "unsupported_action_or_target")
        if candidate.target not in residual.pending:
            return finish(Decision.REJECT, "target_already_resolved")
        inputs = _read(self.problem, evidence, STRESS_INPUTS)
        if inputs.errors:
            return finish(Decision.REJECT, inputs.errors[0])
        if inputs.missing:
            return finish(Decision.DEFER, inputs.missing[0])
        # The frozen problem is the declared task snapshot; evidence may not
        # silently replace it with a different member, load case, or input set.
        declared = make_evidence(self.problem)
        for record in inputs.records:
            if record not in declared:
                return finish(Decision.REJECT, "input_differs_from_declared_problem")
        expected_stress = _stress_fact(self.problem, inputs)
        if candidate.action == STRESS_ACTION:
            if set(candidate.refs) != set(inputs.refs):
                return finish(Decision.REJECT, "stress_references_mismatch")
            if not candidate.claim.startswith("nominal_stress="):
                return finish(Decision.REJECT, "invalid_stress_claim")
            try:
                claimed = _REGISTRY.parse(candidate.claim.removeprefix("nominal_stress="))
            except (ValueError, TypeError, ZeroDivisionError):
                return finish(Decision.REJECT, "invalid_stress_claim")
            if claimed.dimension != PRESSURE:
                return finish(Decision.REJECT, "wrong_claim_dimension")
            if claimed.value != Fraction(expected_stress.value):
                return finish(Decision.REJECT, "stress_claim_mismatch")
            return finish(Decision.ACCEPT, "nominal_stress_verified", (expected_stress,))

        if not any(_same_fact(fact, expected_stress) for fact in state.facts):
            return finish(Decision.DEFER, "verified_stress_required")
        limit = _read(self.problem, evidence, ("allowable_stress",))
        if limit.errors:
            return finish(Decision.REJECT, limit.errors[0])
        if limit.missing:
            return finish(Decision.DEFER, limit.missing[0])
        if any(record not in declared for record in limit.records):
            return finish(Decision.REJECT, "input_differs_from_declared_problem")
        if set(candidate.refs) != {expected_stress.id, *limit.refs}:
            return finish(Decision.REJECT, "comparison_requires_stress_fact_reference")
        fact = _limit_fact(self.problem, expected_stress, limit)
        expected_claim = f"within_allowable_stress={str(fact.value).lower()}"
        if candidate.claim != expected_claim:
            return finish(Decision.REJECT, "limit_claim_mismatch")
        return finish(Decision.ACCEPT, "user_limit_comparison_verified", (fact,))


class AxialBarProposer:
    """Offline proposal generator; its calculations remain untrusted."""

    def __init__(self, evidence: tuple[Evidence, ...], *, wrong_first: bool = True) -> None:
        self.evidence = evidence
        self.wrong_first = wrong_first
        self.attempt = 0
        self._correct_stress = not wrong_first

    def observe(self, event: TraceEvent) -> None:
        """Correct the deliberate error only after receiving verifier feedback."""
        if (event.decision is Decision.REJECT
                and "stress_claim_mismatch" in event.reasons):
            self._correct_stress = True

    def propose(self, state: State, residual: Residual) -> tuple[Candidate, ...]:
        self.attempt += 1
        rows = {item.metric: item for item in self.evidence}
        if STRESS_TARGET in residual.goals:
            refs = tuple(rows[metric].id for metric in STRESS_INPUTS if metric in rows)
            try:
                force = _REGISTRY.quantity(rows["force"].value, rows["force"].unit)
                area = _REGISTRY.quantity(rows["area"].value, rows["area"].unit)
                value = (force / area).value
            except (KeyError, ValueError, TypeError, ZeroDivisionError):
                value = Fraction(0)
            if not self._correct_stress:
                value += 1
            return (Candidate(f"engineering-proposal:{self.attempt}", STRESS_ACTION,
                              STRESS_TARGET, f"nominal_stress={value} Pa", refs),)
        stress = next((fact for fact in state.facts if fact.metric == "nominal_stress"), None)
        limit = rows.get("allowable_stress")
        refs = (() if stress is None else (stress.id,)) + (() if limit is None else (limit.id,))
        try:
            within = (stress is not None and limit is not None
                      and _REGISTRY.quantity(stress.value, stress.unit).value
                      <= _REGISTRY.quantity(limit.value, limit.unit).value)
        except (ValueError, TypeError, ZeroDivisionError):
            within = False
        return (Candidate(f"engineering-proposal:{self.attempt}", LIMIT_ACTION,
                          LIMIT_TARGET, f"within_allowable_stress={str(within).lower()}", refs),)


def build_agent(problem: AxialBarProblem, complete: Callable[[str], str] | None = None,
                *, wrong_first: bool = True) -> Agent:
    """Create a fresh task Agent with offline or provider-neutral model proposals.

    A model receives the exact action/claim syntax below; its output never
    bypasses the independent verifier. ``wrong_first`` applies only to the
    offline proposer and demonstrates rejection followed by correction.
    """
    if type(problem) is not AxialBarProblem:
        raise TypeError("problem must be an AxialBarProblem")

    def proposers(task, routes, evidence):
        if complete is None:
            return (AxialBarProposer(evidence, wrong_first=wrong_first),)
        instruction = (
            task.instruction + "\nFor engineering:nominal_stress use action "
            "calculate_nominal_stress, claim 'nominal_stress=<exact value> <unit>', "
            "and references to force, area, model, axial_static, is_uniform, "
            "no_local_effects evidence. For engineering:limit_comparison use action "
            "compare_allowable_stress, claim 'within_allowable_stress=true' or "
            "'within_allowable_stress=false', and references to the committed "
            "nominal_stress fact and allowable_stress evidence. The comparison "
            "uses <=. This is a synthetic calculation with a user-supplied limit."
        )
        return (ModelProposer(complete, allowed_actions=(STRESS_ACTION, LIMIT_ACTION),
                              evidence=evidence, instruction=instruction),)

    return Agent(domain_factory=lambda task: AxialBarDomain(problem),
                 verifier_factory=lambda task: AxialBarVerifier(problem),
                 proposer_factory=proposers, tools=(AxialBarEvidenceTool(problem),),
                 max_steps=8, max_no_progress=3)


def run_demo() -> AgentResult:
    problem = AxialBarProblem(axial_static=True, is_uniform=True, no_local_effects=True)
    return build_agent(problem).run(Task(
        "synthetic-axial-bar", "Calculate nominal stress and compare the user-supplied limit.",
        DOMAIN, (("subject", problem.subject), ("scope", problem.scope)),
    ))
