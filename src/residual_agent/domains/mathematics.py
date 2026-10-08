"""Exact, evidence-bound reasoning for one rational equation ``a*x + b = c``.

This is a reusable mathematics adapter, not a general theorem prover. Inputs
are exact integers, rational strings, or ``Fraction`` values. The three proof
steps are normalization, solving (including degenerate cases), and an explicit
check against the original equation. A model may propose each step, but only
the independent verifier creates facts. The default callback is an offline,
deterministic demonstration; it is not a trained model or an LLM benchmark.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
import json
import re

from ..adapters import ModelProposer
from ..agent import Agent, AgentResult, Task
from ..core import Candidate, Decision, Evidence, Fact, Residual, State, Verdict


DOMAIN = "mathematics"
NORMALIZE_TARGET = "mathematics:normalized"
SOLVE_TARGET = "mathematics:solution"
CHECK_TARGET = "mathematics:checked"
COEFFICIENT_TARGET = "mathematics:rational_coefficients"
ACTIONS = ("normalize_equation", "solve_equation", "check_solution")
SCOPE = "rational-linear-equation-v1"
SOURCE = "configured-rational-equation"
EVIDENCE_IDS = ("equation:a", "equation:b", "equation:c")
NORMALIZED_FACT = "equation:normalized"
COEFFICIENT_FACT = "equation:rational_coefficients"
SOLUTION_FACT = "equation:solution"
CHECK_FACT = "equation:checked"


def _rational(value: object) -> Fraction:
    """Do not silently turn booleans, floats, or decimal text into exact input."""
    if type(value) is int or type(value) is Fraction:
        return Fraction(value)
    if type(value) is str and re.fullmatch(r"[+-]?[0-9]+(?:/[+-]?[0-9]+)?", value):
        try:
            if "/" in value:
                numerator, denominator = value.split("/")
                return Fraction(int(numerator), int(denominator))
            return Fraction(int(value))
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("coefficient must be an exact rational with nonzero denominator") from exc
    raise ValueError("coefficient must be an int, Fraction, or rational string such as '-3/2'")


@dataclass(frozen=True, slots=True)
class LinearEquation:
    """A rational equation; coefficients become canonical rational strings.

    ``a=0`` is allowed: ``b=c`` has every rational as a solution, and ``b!=c``
    has no solution. A completed classification is a solved *reasoning task*,
    which does not imply that the equation has a unique solution.
    """

    a: int | str | Fraction
    b: int | str | Fraction
    c: int | str | Fraction
    variable: str = "x"

    def __post_init__(self) -> None:
        for name in ("a", "b", "c"):
            object.__setattr__(self, name, str(_rational(getattr(self, name))))
        if type(self.variable) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", self.variable):
            raise ValueError("variable must be an ASCII identifier beginning with a letter")

    @property
    def subject(self) -> str:
        return f"linear-equation:{self.variable}"


@dataclass(frozen=True, slots=True)
class EquationTool:
    """Register the configured equation as input evidence on every Agent run."""

    problem: LinearEquation
    name: str = "rational_equation_reader"

    def __post_init__(self) -> None:
        if type(self.problem) is not LinearEquation:
            raise ValueError("problem must be a LinearEquation")

    def collect(self, task: Task) -> tuple[Evidence, ...]:
        if task.domain != DOMAIN:
            raise ValueError("EquationTool requires a mathematics task")
        return tuple(
            Evidence(identifier, self.problem.subject, metric, getattr(self.problem, metric),
                     "rational", SOURCE, SCOPE)
            for identifier, metric in zip(EVIDENCE_IDS, ("a", "b", "c"))
        )


def _proof(problem: LinearEquation) -> tuple[tuple[Fact, ...], tuple[str, str, str]]:
    """Trusted exact proof specification used by verifier and residual rebuild.

    This function never reads a candidate or a model-produced value. The
    proposer below implements its own arithmetic and cannot create these facts.
    """
    a, b, c = (_rational(getattr(problem, key)) for key in ("a", "b", "c"))
    rhs = c - b
    if a:
        value = rhs / a
        solution = ("unique", str(value))
        lhs = a * value + b
        check = ("unique", str(value), str(lhs), str(c))
        solution_claim = f"{problem.variable}={value}"
        check_claim = f"substitution:{lhs}={c}"
    elif rhs:
        solution = ("no_solution",)
        check = ("no_solution", str(b), str(c))
        solution_claim = "no_solution"
        check_claim = f"inconsistent:{b}!={c}"
    else:
        solution = ("all_rationals",)
        check = ("all_rationals", str(b), str(c))
        solution_claim = "all_rationals"
        check_claim = f"identity:{b}={c}"
    facts = tuple(
        Fact(identifier, problem.subject, metric, value, "rational", EVIDENCE_IDS, SCOPE)
        for identifier, metric, value in (
            (NORMALIZED_FACT, "normalized_equation", (str(a), str(rhs))),
            (COEFFICIENT_FACT, "rational_coefficients", (str(a), str(b), str(c))),
            (SOLUTION_FACT, "solution", solution),
            (CHECK_FACT, "checked_solution", check),
        )
    )
    return facts, (f"{a}*{problem.variable}={rhs}", solution_claim, check_claim)


def _has_fact(state: State, expected: Fact) -> bool:
    # Require the complete record, including original evidence provenance.
    matching = tuple(fact for fact in state.facts if fact.key == expected.key)
    return matching == (expected,)


@dataclass(frozen=True, slots=True)
class MathematicsDomain:
    """Recompute the proof obligations from exact committed facts, never text."""

    problem: LinearEquation

    def __post_init__(self) -> None:
        if type(self.problem) is not LinearEquation:
            raise ValueError("problem must be a LinearEquation")

    def rebuild(self, state: State) -> Residual:
        normalized, coefficients, solution, checked = _proof(self.problem)[0]
        has_coefficients = _has_fact(state, coefficients)
        has_normalized = has_coefficients and _has_fact(state, normalized)
        has_solution = has_normalized and _has_fact(state, solution)
        has_check = has_solution and _has_fact(state, checked)
        return Residual(
            goals=() if has_solution else (SOLVE_TARGET,),
            unknowns=() if has_normalized else (NORMALIZE_TARGET,),
            hard_constraints=(
                (() if has_coefficients else (COEFFICIENT_TARGET,))
                + (() if has_check else (CHECK_TARGET,))
            ),
        )


@dataclass(frozen=True, slots=True)
class MathematicsVerifier:
    """Validate equation binding, prerequisite steps, exact claims, and refs."""

    problem: LinearEquation

    def __post_init__(self) -> None:
        if type(self.problem) is not LinearEquation:
            raise ValueError("problem must be a LinearEquation")

    def verify(self, candidate: Candidate, state: State, residual: Residual,
               evidence: tuple[Evidence, ...]) -> Verdict:
        def verdict(decision: Decision, reason: str, facts=()) -> Verdict:
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence,
                                         facts=facts, reasons=(reason,))

        targets = (NORMALIZE_TARGET, SOLVE_TARGET, CHECK_TARGET)
        if candidate.action not in ACTIONS:
            return verdict(Decision.REJECT, "unsupported_action_or_target")
        stage = ACTIONS.index(candidate.action)
        if candidate.target != targets[stage] or candidate.target not in residual.pending:
            return verdict(Decision.REJECT, "unsupported_action_or_target")

        expected_evidence = EquationTool(self.problem).collect(Task("verify", "Verify equation", DOMAIN))
        if len(evidence) != len(expected_evidence) or {item.id: item for item in evidence} != {
            item.id: item for item in expected_evidence
        }:
            return verdict(Decision.REJECT, "equation_evidence_mismatch")
        if not set(EVIDENCE_IDS) <= set(candidate.refs):
            return verdict(Decision.REJECT, "missing_original_equation_refs")
        known = set(EVIDENCE_IDS) | {fact.id for fact in state.facts}
        if not set(candidate.refs) <= known:
            return verdict(Decision.REJECT, "unknown_reference")

        expected, claims = _proof(self.problem)
        prerequisites = () if stage == 0 else expected[:2] if stage == 1 else expected[:3]
        if any(not _has_fact(state, fact) for fact in prerequisites):
            return verdict(Decision.DEFER, "missing_verified_derivation")
        if not {fact.id for fact in prerequisites} <= set(candidate.refs):
            return verdict(Decision.REJECT, "missing_derivation_refs")
        if candidate.claim != claims[stage]:
            return verdict(Decision.REJECT, ("normalization_claim_mismatch", "solution_claim_mismatch",
                                             "substitution_claim_mismatch")[stage])
        additions = expected[:2] if stage == 0 else (expected[2],) if stage == 1 else (expected[3],)
        return verdict(Decision.ACCEPT, ("normalization_verified", "solution_verified",
                                         "original_equation_checked")[stage], additions)


def _offline_completion() -> Callable[[str], str]:
    """Demonstrate one rejected wrong answer and correction using feedback."""
    attempt = 0

    def complete(prompt: str) -> str:
        nonlocal attempt
        attempt += 1
        payload = json.loads(prompt)
        observations = {item["metric"]: item["value"] for item in payload["registered_evidence"]}
        a, b, c = (Fraction(observations[key]) for key in ("a", "b", "c"))
        variable = payload["registered_evidence"][0]["subject"].split(":", 1)[1]
        facts = {item["id"]: item for item in payload["state"]["facts"]}
        refs = list(EVIDENCE_IDS)
        if NORMALIZED_FACT not in facts:
            stage, claim = 0, f"{a}*{variable}={c - b}"
        elif SOLUTION_FACT not in facts:
            stage = 1
            refs.extend((NORMALIZED_FACT, COEFFICIENT_FACT))
            feedback = payload["last_feedback"]
            corrected = bool(feedback and "solution_claim_mismatch" in feedback["reasons"])
            if not corrected:
                # This proposal is always wrong, including either a=0 case.
                claim = f"{variable}={(c - b) / a + 1}" if a else f"{variable}=0"
            else:
                claim = f"{variable}={(c - b) / a}" if a else (
                    "no_solution" if b != c else "all_rationals"
                )
        else:
            stage = 2
            refs.extend((NORMALIZED_FACT, COEFFICIENT_FACT, SOLUTION_FACT))
            if a:
                # Use the committed solution and original equation for checking.
                solution = Fraction(facts[SOLUTION_FACT]["value"][1])
                claim = f"substitution:{a * solution + b}={c}"
            else:
                claim = f"inconsistent:{b}!={c}" if b != c else f"identity:{b}={c}"
        return json.dumps({"id": f"equation-proposal:{attempt}", "action": ACTIONS[stage],
                           "target": (NORMALIZE_TARGET, SOLVE_TARGET, CHECK_TARGET)[stage],
                           "claim": claim, "refs": refs})

    return complete


def build_agent(problem: LinearEquation, complete: Callable[[str], str] | None = None) -> Agent:
    """Build a fresh, reusable Agent for the supplied rational equation.

    Run with ``Task(..., domain='mathematics')``. To use an actual language
    model, supply ``complete(prompt: str) -> str``; the prompt contains state,
    evidence, action names, claim formats, and feedback. The callback must
    enforce its own resource limits. ``None`` selects the offline demonstration.
    """
    if type(problem) is not LinearEquation:
        raise ValueError("problem must be a LinearEquation")
    if complete is not None and not callable(complete):
        raise ValueError("complete must be callable or None")

    def proposers(task, routes, evidence):
        instruction = (
            f"{task.instruction}\nSolve a*{problem.variable}+b=c over the rationals in three steps. "
            "Coefficients and claims use canonical Fraction strings (e.g. 2 or -3/2). "
            f"1. {ACTIONS[0]} targets {NORMALIZE_TARGET}: claim '<a>*{problem.variable}=<c-b>'. "
            f"2. {ACTIONS[1]} targets {SOLVE_TARGET}: claim '{problem.variable}=<solution>', "
            "or 'no_solution'/'all_rationals' when a=0. "
            f"3. {ACTIONS[2]} targets {CHECK_TARGET}: claim 'substitution:<a*solution+b>=<c>', "
            "'inconsistent:<b>!=<c>', or 'identity:<b>=<c>'. "
            "Cite all three original evidence IDs at every step and all prerequisite fact IDs. "
            "A solution alone does not discharge the final equation-check obligation."
        )
        return (ModelProposer(complete if complete is not None else _offline_completion(),
                              allowed_actions=ACTIONS, evidence=evidence, instruction=instruction),)

    return Agent(domain_factory=lambda task: MathematicsDomain(problem),
                 verifier_factory=lambda task: MathematicsVerifier(problem),
                 proposer_factory=proposers, tools=(EquationTool(problem),),
                 max_steps=12, max_no_progress=4)


def run_demo() -> AgentResult:
    """Solve ``(3/2)*x - 1/3 = 5/3`` with an intentionally wrong first solution."""
    return build_agent(LinearEquation("3/2", "-1/3", "5/3")).run(Task(
        "rational-equation-demo", "Solve the configured rational equation and check the answer.", DOMAIN,
    ))
