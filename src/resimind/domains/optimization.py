"""Small rational strictly convex QPs, with independently checked certificates.

The offline proposer enumerates active sets. The verifier never calls that
solver: it checks LDL, feasibility, KKT, and a polynomial sum-of-squares identity.
An injected model can supply exactly the same witnesses. No numeric tolerance,
external solver, or model SDK is needed. This is not a general theorem prover.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
import json
import re

from ..adapters import ModelProposer
from ..agent import Agent, AgentResult, Task
from ..core import Candidate, Decision, Evidence, Fact, Residual, State, Verdict

DOMAIN = "constrained_optimization"
SCOPE = "rational-strictly-convex-qp-v1"
SUBJECT = "configured-quadratic-program"
ACTIONS = ("certify_convexity", "certify_primal_dual", "certify_global")
TARGETS = ("qp:convexity", "qp:primal_dual", "qp:global_optimality")
FACT_IDS = ("qp:ldl", "qp:primal", "qp:multipliers", "qp:global_certificate")
CONVEXITY_FACT, PRIMAL_FACT, KKT_FACT, GLOBAL_FACT = FACT_IDS
EVIDENCE_IDS = tuple(f"qp:input:{name}" for name in ("q", "c", "a", "b", "g", "h"))
_METRICS = ("positive_definite", "primal_feasible", "kkt", "unique_global_minimum")


def _q(value: object) -> Fraction:
    if type(value) is str:
        if len(value) > 256 or not re.fullmatch(r"[+-]?[0-9]+(?:/[+-]?[0-9]+)?", value):
            raise ValueError("use bounded exact rational strings, integers, or Fractions")
        try:
            result = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("invalid rational") from exc
    elif type(value) in (int, Fraction):
        result = Fraction(value)
    else:
        raise ValueError("floats and booleans are not exact rational inputs")
    if max(result.numerator.bit_length(), result.denominator.bit_length()) > 2048 or len(str(result)) > 256:
        raise ValueError("rational input is too large")
    return result


def _vector(value, size: int) -> tuple[Fraction, ...]:
    if type(value) not in (list, tuple) or len(value) != size:
        raise ValueError("vector dimension mismatch")
    return tuple(_q(item) for item in value)


def _matrix(value, rows: int, columns: int) -> tuple[tuple[Fraction, ...], ...]:
    if type(value) not in (list, tuple) or len(value) != rows:
        raise ValueError("matrix dimension mismatch")
    return tuple(_vector(row, columns) for row in value)


def _strings(value):
    if isinstance(value, Fraction):
        return str(value)
    if type(value) in (list, tuple):
        return tuple(_strings(item) for item in value)
    if type(value) is dict:
        return {key: _strings(item) for key, item in value.items()}
    return value


def _dot(a, b):
    return sum((x * y for x, y in zip(a, b)), Fraction(0))


def _data(problem):
    n = len(problem.c)
    return (_matrix(problem.q, n, n), _vector(problem.c, n),
            _matrix(problem.a, len(problem.b), n), _vector(problem.b, len(problem.b)),
            _matrix(problem.g, len(problem.h), n), _vector(problem.h, len(problem.h)))


@dataclass(frozen=True, slots=True)
class QuadraticProgram:
    """Minimize ``x.T*q*x/2+c.T*x`` subject to ``a*x=b, g*x<=h``.

    Input limits: 1..6 variables, at most n equalities and 8 inequalities.
    Q must be symmetric; positive definiteness is a separately verified proof
    obligation. The offline solver is bounded active-set enumeration, and does
    not classify infeasibility or support indefinite/semidefinite objectives.
    Dependent equalities can require a user-supplied proposer.
    """
    q: tuple
    c: tuple
    a: tuple = ()
    b: tuple = ()
    g: tuple = ()
    h: tuple = ()

    def __post_init__(self):
        if type(self.c) not in (list, tuple) or not 1 <= len(self.c) <= 6:
            raise ValueError("require 1..6 variables")
        if type(self.b) not in (list, tuple) or len(self.b) > len(self.c):
            raise ValueError("too many equalities")
        if type(self.h) not in (list, tuple) or len(self.h) > 8:
            raise ValueError("too many inequalities")
        values = _data(self)
        q = values[0]
        if any(q[i][j] != q[j][i] for i in range(len(q)) for j in range(len(q))):
            raise ValueError("Q must be symmetric")
        for name, value in zip(("q", "c", "a", "b", "g", "h"), values):
            object.__setattr__(self, name, _strings(value))


@dataclass(frozen=True, slots=True)
class OptimizationTool:
    problem: QuadraticProgram
    name: str = "rational_qp_reader"

    def collect(self, task: Task) -> tuple[Evidence, ...]:
        if task.domain != DOMAIN:
            raise ValueError(f"OptimizationTool requires domain={DOMAIN!r}")
        return tuple(Evidence(identifier, SUBJECT, name, getattr(self.problem, name),
                              "rational", "configured-quadratic-program", SCOPE)
                     for identifier, name in zip(EVIDENCE_IDS, ("q", "c", "a", "b", "g", "h")))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate certificate key")
        result[key] = value
    return result


def _parse(claim):
    if type(claim) is not str or len(claim) > 16384:
        raise ValueError("certificate must be a bounded JSON object")
    result = json.loads(claim, object_pairs_hook=_unique,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if type(result) is not dict:
        raise ValueError("certificate must be a JSON object")
    return result


def _keys(payload, keys):
    if set(payload) != set(keys):
        raise ValueError("certificate fields do not match action")


def _objective(q, c, x):
    return sum((x[i] * _dot(q[i], x) for i in range(len(x))), Fraction(0)) / 2 + _dot(c, x)


def _check(problem, stage, payload, previous):
    """Validate algebra alone; never search for an optimum or call a solver."""
    q, c, a, b, g, h = _data(problem)
    n, p, m = len(c), len(b), len(h)
    if stage == 0:
        _keys(payload, ("l", "d"))
        l, d = _matrix(payload["l"], n, n), _vector(payload["d"], n)
        if any(l[i][i] != 1 or any(l[i][j] for j in range(i + 1, n)) for i in range(n)):
            raise ValueError("ldl_factor_must_be_unit_lower_triangular")
        if any(value <= 0 for value in d):
            raise ValueError("ldl_diagonal_not_positive")
        if any(sum((l[i][k] * d[k] * l[j][k] for k in range(n)), Fraction(0)) != q[i][j]
               for i in range(n) for j in range(n)):
            raise ValueError("ldl_factorization_mismatch")
        return _strings({"l": l, "d": d})
    if stage == 1:
        _keys(payload, ("x", "objective", "slack"))
        x, objective = _vector(payload["x"], n), _q(payload["objective"])
        slack = tuple(hj - _dot(row, x) for row, hj in zip(g, h))
        if any(_dot(row, x) != bj for row, bj in zip(a, b)):
            raise ValueError("primal_equality_violation")
        if any(value < 0 for value in slack):
            raise ValueError("primal_inequality_violation")
        if _vector(payload["slack"], m) != slack:
            raise ValueError("primal_slack_mismatch")
        if objective != _objective(q, c, x):
            raise ValueError("objective_value_mismatch")
        return _strings({"x": x, "objective": objective, "slack": slack})
    if stage == 2:
        _keys(payload, ("lambda", "mu"))
        lam, mu = _vector(payload["lambda"], p), _vector(payload["mu"], m)
        x = _vector(previous[1]["x"], n)
        if any(value < 0 for value in mu):
            raise ValueError("negative_inequality_multiplier")
        if any(_dot(q[i], x) + c[i] + sum((a[j][i] * lam[j] for j in range(p)), Fraction(0))
               + sum((g[j][i] * mu[j] for j in range(m)), Fraction(0)) for i in range(n)):
            raise ValueError("stationarity_mismatch")
        if any(multiplier * (hj - _dot(row, x)) for multiplier, row, hj in zip(mu, g, h)):
            raise ValueError("complementarity_mismatch")
        return _strings({"lambda": lam, "mu": mu})
    _keys(payload, ("weights", "directions", "offsets", "lambda", "mu", "bound"))
    weights = _vector(payload["weights"], n)
    directions = _matrix(payload["directions"], n, n)
    offsets = _vector(payload["offsets"], n)
    lam, mu = _vector(payload["lambda"], p), _vector(payload["mu"], m)
    bound = _q(payload["bound"])
    if any(value <= 0 for value in weights):
        raise ValueError("square_weight_not_positive")
    if (lam, mu) != (_vector(previous[2]["lambda"], p), _vector(previous[2]["mu"], m)):
        raise ValueError("global_multipliers_mismatch")
    if bound != _q(previous[1]["objective"]):
        raise ValueError("global_bound_mismatch")
    # Expand sum w_i(v_i.y+t_i)^2 + mu.(h-Gy) + lambda.(b-Ay).
    # It must equal f(y)-bound coefficient by coefficient for every y.
    for i in range(n):
        for j in range(n):
            if sum((weights[k] * directions[k][i] * directions[k][j] for k in range(n)),
                   Fraction(0)) != q[i][j] / 2:
                raise ValueError("global_quadratic_coefficient_mismatch")
        linear = sum((2 * weights[k] * offsets[k] * directions[k][i] for k in range(n)), Fraction(0))
        linear -= sum((mu[j] * g[j][i] for j in range(m)), Fraction(0))
        linear -= sum((lam[j] * a[j][i] for j in range(p)), Fraction(0))
        if linear != c[i]:
            raise ValueError("global_linear_coefficient_mismatch")
    constant = _dot(weights, tuple(value * value for value in offsets)) + _dot(mu, h) + _dot(lam, b)
    if constant != -bound:
        raise ValueError("global_constant_coefficient_mismatch")
    return _strings({"weights": weights, "directions": directions, "offsets": offsets,
                     "lambda": lam, "mu": mu, "bound": bound})


def _fact(stage, payload):
    return Fact(FACT_IDS[stage], SUBJECT, _METRICS[stage],
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "rational", EVIDENCE_IDS, SCOPE)


def _chain(problem, state):
    previous = []
    for stage in range(4):
        matches = [fact for fact in state.facts if fact.key == (SUBJECT, _METRICS[stage], SCOPE)]
        if len(matches) != 1:
            break
        try:
            payload = _check(problem, stage, _parse(matches[0].value), previous)
            if matches[0] != _fact(stage, payload):
                break
        except (ValueError, TypeError, KeyError, RecursionError):
            break
        previous.append(payload)
    return previous


@dataclass(frozen=True, slots=True)
class OptimizationDomain:
    problem: QuadraticProgram

    def rebuild(self, state: State) -> Residual:
        count = len(_chain(self.problem, state))
        return Residual(goals=() if count == 4 else (TARGETS[2],),
                        unknowns=() if count >= 3 else (TARGETS[1],),
                        hard_constraints=() if count >= 1 else (TARGETS[0],))


@dataclass(frozen=True, slots=True)
class OptimizationVerifier:
    problem: QuadraticProgram

    def verify(self, candidate: Candidate, state: State, residual: Residual,
               evidence: tuple[Evidence, ...]) -> Verdict:
        def verdict(decision, reason, facts=()):
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence,
                                         reasons=(reason,), facts=facts)
        if candidate.action not in ACTIONS:
            return verdict(Decision.REJECT, "unsupported_action_or_target")
        stage = ACTIONS.index(candidate.action)
        if candidate.target != TARGETS[stage] or candidate.target not in residual.pending:
            return verdict(Decision.REJECT, "unsupported_action_or_target")
        expected = OptimizationTool(self.problem).collect(Task("verify", "verify", DOMAIN))
        if len(evidence) != len(expected) or {item.id: item for item in evidence} != {item.id: item for item in expected}:
            return verdict(Decision.REJECT, "qp_evidence_mismatch")
        if not set(EVIDENCE_IDS) <= set(candidate.refs):
            return verdict(Decision.REJECT, "missing_original_problem_refs")
        if not set(candidate.refs) <= set(EVIDENCE_IDS) | {fact.id for fact in state.facts}:
            return verdict(Decision.REJECT, "unknown_reference")
        previous = _chain(self.problem, state)
        required = (0, 1, 3)[stage]
        if len(previous) < required:
            return verdict(Decision.DEFER, "missing_verified_prerequisite")
        if not set(FACT_IDS[:required]) <= set(candidate.refs):
            return verdict(Decision.REJECT, "missing_certificate_refs")
        try:
            proposal = _parse(candidate.claim)
            if stage == 1:
                _keys(proposal, ("primal", "dual"))
                if type(proposal["primal"]) is not dict or type(proposal["dual"]) is not dict:
                    raise ValueError("primal_and_dual_must_be_objects")
                primal = _check(self.problem, 1, proposal["primal"], previous)
                dual = _check(self.problem, 2, proposal["dual"], previous[:1] + [primal])
                additions = (_fact(1, primal), _fact(2, dual))
            else:
                proof_stage = 0 if stage == 0 else 3
                payload = _check(self.problem, proof_stage, proposal, previous)
                additions = (_fact(proof_stage, payload),)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            return verdict(Decision.REJECT, str(exc) or "invalid_certificate")
        return verdict(Decision.ACCEPT, ("positive_definiteness_verified", "primal_and_kkt_verified",
                                        "global_gap_identity_verified")[stage], additions)


# Everything below constructs untrusted witnesses. The verifier above does not
# invoke _solve, _ldl, _active_set_solution, or the completion callback.
def _solve(matrix, rhs):
    n = len(rhs)
    augmented = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for col in range(n):
        pivot = next((row for row in range(col, n) if augmented[row][col]), None)
        if pivot is None:
            return None
        augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
        divisor = augmented[col][col]
        augmented[col] = [value / divisor for value in augmented[col]]
        for row in range(n):
            if row != col:
                factor = augmented[row][col]
                augmented[row] = [value - factor * other for value, other in zip(augmented[row], augmented[col])]
    return tuple(row[-1] for row in augmented)


def _ldl(q):
    n = len(q)
    l = [[Fraction(i == j) for j in range(n)] for i in range(n)]
    d = []
    for j in range(n):
        diagonal = q[j][j] - sum((l[j][k] ** 2 * d[k] for k in range(j)), Fraction(0))
        if diagonal <= 0:
            return None
        d.append(diagonal)
        for i in range(j + 1, n):
            l[i][j] = (q[i][j] - sum((l[i][k] * l[j][k] * d[k] for k in range(j)), Fraction(0))) / diagonal
    return tuple(tuple(row) for row in l), tuple(d)


def _stationary(q, c, a, b):
    n, p = len(c), len(b)
    matrix = tuple(tuple(q[i]) + tuple(a[j][i] for j in range(p)) for i in range(n))
    matrix += tuple(tuple(row) + (Fraction(0),) * p for row in a)
    return _solve(matrix, tuple(-value for value in c) + b)


def _active_set_solution(q, c, a, b, g, h):
    n, p, m = len(c), len(b), len(h)
    for size in range(min(n - p, m) + 1):
        for active in combinations(range(m), size):
            answer = _stationary(q, c, a + tuple(g[j] for j in active), b + tuple(h[j] for j in active))
            if answer is None:
                continue
            x, lam = answer[:n], answer[n:n + p]
            mu = [Fraction(0)] * m
            for j, value in zip(active, answer[n + p:]):
                mu[j] = value
            if any(value < 0 for value in mu) or any(_dot(row, x) > hj for row, hj in zip(g, h)):
                continue
            return x, lam, tuple(mu)
    return None


def _offline_completion(problem, wrong_first):
    attempt, corrected = 0, not wrong_first
    q, c, a, b, g, h = _data(problem)
    factors = _ldl(q)
    witness = _active_set_solution(q, c, a, b, g, h) if factors else None
    relaxed = _stationary(q, c, a, b) if factors else None

    def complete(prompt):
        nonlocal attempt, corrected
        attempt += 1
        data = json.loads(prompt)
        if factors is None or witness is None:
            return "null"
        feedback = data["last_feedback"]
        if feedback and feedback["decision"] == "reject" and "primal_inequality_violation" in feedback["reasons"]:
            corrected = True
        facts = {fact["id"]: json.loads(fact["value"]) for fact in data["state"]["facts"] if fact["id"] in FACT_IDS}
        stage = 0 if CONVEXITY_FACT not in facts else 1 if KKT_FACT not in facts else 2
        if GLOBAL_FACT in facts:
            return "null"
        l, d = factors
        x, lam, mu = witness
        if stage == 0:
            payload = {"l": l, "d": d}
        elif stage == 1:
            # Only deliberately propose the relaxed point when it is actually
            # infeasible. The next correction is triggered by verifier feedback.
            trial = relaxed[:len(c)] if relaxed else x
            if not corrected and any(_dot(row, trial) > hj for row, hj in zip(g, h)):
                x = trial
            payload = {"primal": {"x": x, "objective": _objective(q, c, x),
                                  "slack": tuple(hj - _dot(row, x) for row, hj in zip(g, h))},
                       "dual": {"lambda": lam, "mu": mu}}
        else:
            directions = tuple(tuple(l[j][i] for j in range(len(c))) for i in range(len(c)))
            payload = {"weights": tuple(value / 2 for value in d), "directions": directions,
                       "offsets": tuple(-_dot(row, x) for row in directions),
                       "lambda": lam, "mu": mu, "bound": _objective(q, c, x)}
        return json.dumps({"id": f"qp-proposal:{attempt}", "action": ACTIONS[stage], "target": TARGETS[stage],
                           "claim": json.dumps(_strings(payload)), "refs": list(EVIDENCE_IDS + FACT_IDS[:(0, 1, 3)[stage]])})
    return complete


def build_agent(problem: QuadraticProgram, complete: Callable[[str], str] | None = None,
                *, wrong_first: bool = True) -> Agent:
    """Fresh Agent with an offline active-set proposer or an injected text model.

    ``complete(prompt)->str`` returns the standard candidate JSON; claim itself
    is a JSON-encoded certificate. Primal and dual witnesses are checked and
    committed atomically, so a nonoptimal feasible proposal leaves no facts and
    can be corrected after feedback. A stalled run is not an infeasibility proof.
    """
    if type(problem) is not QuadraticProgram:
        raise ValueError("problem must be a QuadraticProgram")
    if complete is not None and not callable(complete):
        raise ValueError("complete must be callable or None")
    if type(wrong_first) is not bool:
        raise ValueError("wrong_first must be bool")

    def proposers(task, routes, evidence):
        instruction = (
            task.instruction + "\nMinimize x^T Q x/2+c^T x with Ax=b, Gx<=h. "
            "Return claim as a JSON object encoded inside the candidate claim STRING. Use rational strings. "
            f"Stages/actions/targets: {list(zip(ACTIONS, TARGETS))}. "
            "1. {l:unit-lower-triangular matrix,d:positive diagonal} proves Q=L diag(d) L^T. "
            "2. {primal:{x:vector,objective:exact value,slack:h-Gx},dual:{lambda:vector,mu:vector}}. "
            "Both feasibility and KKT are verified before either fact commits. "
            "Equality multipliers lambda are unrestricted; inequality multipliers mu>=0. "
            "Qx+c+A^T lambda+G^T mu=0, mu_j*slack_j=0. "
            "3. {weights:positive vector,directions:matrix,offsets:vector,lambda:vector,mu:vector,bound:objective}. "
            "Prove the polynomial identity f(y)-bound = sum_i weights_i*(directions_i.y+offsets_i)^2 "
            "+ mu.(h-Gy) + lambda.(b-Ay). There are n square terms; multipliers match stage 2. "
            f"Cite all evidence and all earlier fact IDs {FACT_IDS}. Completing KKT alone does not close the global proof."
        )
        callback = complete if complete is not None else _offline_completion(problem, wrong_first)
        return (ModelProposer(callback, allowed_actions=ACTIONS, evidence=evidence, instruction=instruction),)

    return Agent(domain_factory=lambda task: OptimizationDomain(problem),
                 verifier_factory=lambda task: OptimizationVerifier(problem),
                 proposer_factory=proposers, tools=(OptimizationTool(problem),),
                 max_steps=16, max_no_progress=4)


def demo_problem() -> QuadraticProgram:
    """Three coupled variables, one equality, four inequalities, one active cap."""
    return QuadraticProgram(q=((4, 1, 0), (1, 2, 0), (0, 0, 2)), c=(-8, -3, -3),
                            a=((1, 1, 1),), b=(3,),
                            g=((-1, 0, 0), (0, -1, 0), (0, 0, -1), (1, 0, 0)), h=(0, 0, 0, 1))


def run_demo() -> AgentResult:
    return build_agent(demo_problem()).run(Task(
        "constrained-optimization-demo", "Prove the unique global minimum of the configured QP.", DOMAIN))
