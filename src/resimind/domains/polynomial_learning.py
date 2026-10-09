"""Derive polynomial expansions, admit their certificates, reuse them on new tasks.

The default proposer is a deterministic AST rewrite grammar, not a neural
model. It never calls the polynomial checker to generate an answer. Pass a
ModelProposer-compatible completion callback to use neural proposals instead.
Every primitive or recalled rewrite is checked independently with exact
rational polynomial arithmetic. This adapter does not solve arbitrary equations.
"""
from __future__ import annotations

import ast
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json

from ..adapters import ModelProposer
from ..agent import Agent, AgentResult, Task
from ..core import Candidate, Decision, Evidence, Fact, Residual, State, TraceEvent, Verdict, content_digest
from ..knowledge import KnowledgeCandidate, KnowledgeLibrary, KnowledgeRecord
from ..learning import LearningAgent
from .algebra import validate_expression, verify_identity

DOMAIN = "algebra"
TARGET = "algebra:expanded"
SCOPE = "rational-polynomial-expansion-v1"
INPUT_ID = "polynomial:input"
ACTIONS = ("rewrite_polynomial", "certify_expansion")


def _tree(expression: str) -> ast.expr:
    return ast.parse(expression.strip(), mode="eval").body


def _same(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(left) == ast.dump(right)


def _text(node: ast.AST) -> str:
    return ast.unparse(ast.fix_missing_locations(node))


def _has_sum(node: ast.AST) -> bool:
    return any(isinstance(part, ast.BinOp) and isinstance(part.op, (ast.Add, ast.Sub))
               for part in ast.walk(node))


def is_expanded(expression: str) -> bool:
    """A syntactic goal: no multiplication/power still encloses a sum.

    Like terms need not be collected. Callers must separately establish that
    the expression belongs to the validated polynomial grammar.
    """
    return not any(
        isinstance(node, ast.BinOp)
        and ((isinstance(node.op, ast.Mult) and (_has_sum(node.left) or _has_sum(node.right)))
             or (isinstance(node.op, ast.Pow) and _has_sum(node.left)))
        for node in ast.walk(_tree(expression))
    )


@dataclass(frozen=True, slots=True)
class PolynomialProblem:
    expression: str
    variables: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.variables) is not tuple:
            raise ValueError("variables must be a tuple")
        validate_expression(self.expression, self.variables)

    @property
    def subject(self) -> str:
        return "polynomial:" + content_digest((self.expression, self.variables))[:20]


@dataclass
class WorkCounts:
    """Executed work, not estimated tokens or a hardware-independent cost."""
    proposal_calls: int = 0
    primitive_node_visits: int = 0
    pattern_attempts: int = 0
    identity_checks: int = 0


@dataclass(frozen=True)
class PolynomialTool:
    problem: PolynomialProblem
    name: str = "polynomial_input"

    def collect(self, task: Task) -> tuple[Evidence, ...]:
        if task.domain != DOMAIN:
            raise ValueError("polynomial task domain must be algebra")
        return (Evidence(INPUT_ID, self.problem.subject, "expression",
                         (self.problem.expression, self.problem.variables), "rational-polynomial",
                         "configured-symbolic-problem", SCOPE),)


def _checked(lhs: str, rhs: str, variables: tuple[str, ...], counts: WorkCounts) -> bool:
    counts.identity_checks += 1
    return verify_identity(lhs, rhs, variables).status == "verified"


def _current(problem: PolynomialProblem, state: State, counts: WorkCounts) -> str:
    """Recheck the evidence-bound chain; a solved-looking string is insufficient."""
    current = problem.expression
    if state.revision != len(state.facts):
        raise ValueError("invalid proof revision")
    for index, fact in enumerate(state.facts, 1):
        if (fact.id != f"polynomial:step:{index}" or fact.subject != problem.subject
                or fact.metric != f"rewrite:{index}" or fact.unit != "rational-polynomial"
                or fact.scope != SCOPE or fact.evidence_refs != (INPUT_ID,)
                or type(fact.value) is not tuple or len(fact.value) != 4):
            raise ValueError("invalid proof fact")
        before, after, rule_id, fingerprint = fact.value
        if any(type(value) is not str for value in fact.value) or before != current:
            raise ValueError("broken proof chain")
        if not _checked(before, after, problem.variables, counts):
            raise ValueError("unverified proof chain")
        current = after
    return current


@dataclass
class PolynomialDomain:
    problem: PolynomialProblem
    counts: WorkCounts

    def rebuild(self, state: State) -> Residual:
        expression = _current(self.problem, state, self.counts)
        complete = bool(state.facts) and is_expanded(expression)
        return Residual(goals=() if complete else (TARGET,))


def _match(pattern: ast.AST, actual: ast.AST, variables: set[str],
           bindings: dict[str, ast.AST]) -> bool:
    if isinstance(pattern, ast.Name) and pattern.id in variables:
        if pattern.id in bindings:
            return _same(bindings[pattern.id], actual)
        bindings[pattern.id] = actual
        return True
    if type(pattern) is not type(actual):
        return False
    for field in pattern._fields:
        left, right = getattr(pattern, field), getattr(actual, field)
        if isinstance(left, ast.AST):
            if not _match(left, right, variables, bindings):
                return False
        elif left != right:
            return False
    return True


def _substitute(node: ast.AST, bindings: dict[str, ast.AST]) -> ast.AST:
    class Substitute(ast.NodeTransformer):
        def visit_Name(self, item):
            return deepcopy(bindings.get(item.id, item))
    return Substitute().visit(deepcopy(node))


def _rewrite_first(node: ast.AST, transform: Callable[[ast.AST], ast.AST | None]) -> ast.AST | None:
    changed = transform(node)
    if changed is not None:
        return changed
    for field, child in ast.iter_fields(node):
        if isinstance(child, ast.expr):
            replacement = _rewrite_first(child, transform)
            if replacement is not None:
                result = deepcopy(node)
                setattr(result, field, replacement)
                return result
    return None


def _macro(expression: str, record: KnowledgeRecord, counts: WorkCounts) -> str | None:
    rule = record.candidate
    pattern, replacement = _tree(rule.statement["lhs"]), _tree(rule.statement["rhs"])
    variables = set(rule.statement["variables"])

    def transform(node):
        counts.pattern_attempts += 1
        bindings: dict[str, ast.AST] = {}
        if _match(pattern, node, variables, bindings):
            changed = _substitute(replacement, bindings)
            if not _same(changed, node):
                return changed
        return None

    result = _rewrite_first(_tree(expression), transform)
    return None if result is None else _text(result)


def _primitive(expression: str, counts: WorkCounts) -> str | None:
    """Only local distributivity and integer-power unfolding; no answer table."""
    def transform(node):
        counts.primitive_node_visits += 1
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.UAdd):
                return deepcopy(node.operand)
            if (isinstance(node.op, ast.USub) and isinstance(node.operand, ast.BinOp)
                    and isinstance(node.operand.op, (ast.Add, ast.Sub))):
                return ast.BinOp(
                    left=ast.UnaryOp(op=ast.USub(), operand=deepcopy(node.operand.left)),
                    op=deepcopy(node.operand.op),
                    right=ast.UnaryOp(op=ast.USub(), operand=deepcopy(node.operand.right)))
        if not isinstance(node, ast.BinOp):
            return None
        if isinstance(node.op, ast.Pow) and _has_sum(node.left):
            exponent = node.right
            if isinstance(exponent, ast.UnaryOp):
                power = exponent.operand.value * (-1 if isinstance(exponent.op, ast.USub) else 1)
            else:
                power = exponent.value
            if power == 0:
                return ast.Constant(value=1)
            if power == 1:
                return deepcopy(node.left)
            left = deepcopy(node.left) if power == 2 else ast.BinOp(
                left=deepcopy(node.left), op=ast.Pow(), right=ast.Constant(value=power - 1))
            return ast.BinOp(left=left, op=ast.Mult(), right=deepcopy(node.left))
        if (isinstance(node.op, ast.Div) and isinstance(node.left, ast.BinOp)
                and isinstance(node.left.op, (ast.Add, ast.Sub))):
            return ast.BinOp(
                left=ast.BinOp(left=deepcopy(node.left.left), op=ast.Div(), right=deepcopy(node.right)),
                op=deepcopy(node.left.op),
                right=ast.BinOp(left=deepcopy(node.left.right), op=ast.Div(), right=deepcopy(node.right)))
        if isinstance(node.op, ast.Mult):
            if isinstance(node.left, ast.BinOp) and isinstance(node.left.op, (ast.Add, ast.Sub)):
                return ast.BinOp(
                    left=ast.BinOp(left=deepcopy(node.left.left), op=ast.Mult(), right=deepcopy(node.right)),
                    op=deepcopy(node.left.op),
                    right=ast.BinOp(left=deepcopy(node.left.right), op=ast.Mult(), right=deepcopy(node.right)))
            if isinstance(node.right, ast.BinOp) and isinstance(node.right.op, (ast.Add, ast.Sub)):
                return ast.BinOp(
                    left=ast.BinOp(left=deepcopy(node.left), op=ast.Mult(), right=deepcopy(node.right.left)),
                    op=deepcopy(node.right.op),
                    right=ast.BinOp(left=deepcopy(node.left), op=ast.Mult(), right=deepcopy(node.right.right)))
        # Lift a negated sum into a sum, so enclosing products can distribute.
        for side in ("left", "right"):
            child = getattr(node, side)
            if (isinstance(node.op, ast.Mult) and isinstance(child, ast.UnaryOp)
                    and isinstance(child.op, ast.USub) and isinstance(child.operand, ast.BinOp)
                    and isinstance(child.operand.op, (ast.Add, ast.Sub))):
                result = deepcopy(node)
                setattr(result, side, ast.BinOp(
                    left=ast.UnaryOp(op=ast.USub(), operand=deepcopy(child.operand.left)),
                    op=deepcopy(child.operand.op),
                    right=ast.UnaryOp(op=ast.USub(), operand=deepcopy(child.operand.right))))
                return result
        return None

    result = _rewrite_first(_tree(expression), transform)
    return None if result is None else _text(result)


def _expansion_rules(records: tuple[KnowledgeRecord, ...]) -> tuple[KnowledgeRecord, ...]:
    # Only expansion-oriented identities guide this adapter. Other domains and
    # equation-cancellation rules remain in the generic library for their users.
    def bound_variables(record):
        left = {node.id for node in ast.walk(_tree(record.candidate.statement["lhs"]))
                if isinstance(node, ast.Name)}
        right = {node.id for node in ast.walk(_tree(record.candidate.statement["rhs"]))
                 if isinstance(node, ast.Name)}
        return right <= left

    return tuple(record for record in deepcopy(records)
                 if record.status == "verified" and record.candidate.domain == DOMAIN
                 and record.candidate.kind == "polynomial_identity"
                 and not record.candidate.assumptions
                 and bound_variables(record)
                 and not is_expanded(record.candidate.statement["lhs"])
                 and is_expanded(record.candidate.statement["rhs"]))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class PolynomialVerifier:
    def __init__(self, problem: PolynomialProblem, records: tuple[KnowledgeRecord, ...], counts: WorkCounts):
        self.problem, self.counts = problem, counts
        self.records = {record.candidate.id: record for record in _expansion_rules(records)}

    def verify(self, candidate: Candidate, state: State, residual: Residual,
               evidence: tuple[Evidence, ...]) -> Verdict:
        def reply(decision, reason, facts=()):
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence,
                                         reasons=(reason,), facts=facts)
        if candidate.action not in ACTIONS or candidate.target != TARGET or TARGET not in residual.pending:
            return reply(Decision.REJECT, "unsupported_polynomial_step")
        expected = PolynomialTool(self.problem).collect(Task("verify", "Verify", DOMAIN))
        refs = (INPUT_ID,) + ((state.facts[-1].id,) if state.facts else ())
        if evidence != expected or candidate.refs != refs:
            return reply(Decision.REJECT, "polynomial_evidence_mismatch")
        current = _current(self.problem, state, self.counts)
        try:
            if len(candidate.claim) > 32768:
                raise ValueError("rewrite claim exceeds size limit")
            payload = json.loads(candidate.claim, object_pairs_hook=_unique_object)
            if type(payload) is not dict or set(payload) != {"before", "after", "rule_id"}:
                raise ValueError("invalid rewrite schema")
            before, after, rule_id = (payload[key] for key in ("before", "after", "rule_id"))
            if any(type(value) is not str for value in (before, after, rule_id)) or before != current:
                raise ValueError("rewrite does not bind to current expression")
            validate_expression(after, self.problem.variables)
        except (ValueError, TypeError, RecursionError):
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite")
        if not _checked(before, after, self.problem.variables, self.counts):
            return reply(Decision.REJECT, "polynomial_identity_not_proved")
        fingerprint = ""
        if rule_id:
            record = self.records.get(rule_id)
            if record is None or _macro(before, record, self.counts) != after:
                return reply(Decision.REJECT, "rule_not_applicable")
            fingerprint = record.fingerprint
        if _same(_tree(before), _tree(after)) and not is_expanded(after):
            return reply(Decision.REJECT, "rewrite_makes_no_progress")
        if candidate.action == "certify_expansion" and not is_expanded(after):
            return reply(Decision.REJECT, "expansion_still_incomplete")
        step = len(state.facts) + 1
        fact = Fact(f"polynomial:step:{step}", self.problem.subject, f"rewrite:{step}",
                    (before, after, rule_id, fingerprint), "rational-polynomial", (INPUT_ID,), SCOPE)
        return reply(Decision.ACCEPT, "polynomial_identity_verified", (fact,))


class PolynomialProposer:
    def __init__(self, problem: PolynomialProblem, records: tuple[KnowledgeRecord, ...], counts: WorkCounts):
        self.problem, self.counts = problem, counts
        self.records = _expansion_rules(records)
        self._failed_macros: set[tuple[str, str]] = set()

    def observe(self, event: TraceEvent) -> None:
        # A true generic identity can still exceed the current task's supported
        # expression bounds. Rejection should allow another rule or primitives.
        if event.decision is not Decision.ACCEPT and event.candidate is not None:
            payload = json.loads(event.candidate.claim)
            if payload["rule_id"]:
                self._failed_macros.add((payload["before"], payload["rule_id"]))

    def propose(self, state: State, residual: Residual) -> tuple[Candidate, ...]:
        self.counts.proposal_calls += 1
        before = state.facts[-1].value[1] if state.facts else self.problem.expression
        after, rule_id = None, ""
        for record in self.records:
            if (before, record.candidate.id) in self._failed_macros:
                continue
            after = _macro(before, record, self.counts)
            if after is not None:
                rule_id = record.candidate.id
                break
        if after is None:
            after = _primitive(before, self.counts)
        if after is None and not is_expanded(before):
            return ()
        action = "rewrite_polynomial" if after is not None else "certify_expansion"
        claim = json.dumps({"before": before, "after": after or before, "rule_id": rule_id}, sort_keys=True)
        refs = (INPUT_ID,) + ((state.facts[-1].id,) if state.facts else ())
        return (Candidate(f"polynomial-proposal:{len(state.facts) + 1}", action, TARGET, claim, refs),)


def distill_expansion(result: AgentResult) -> tuple[KnowledgeCandidate, ...]:
    """Extract an actual committed symbolic proof; no template RHS is supplied."""
    run = result.run_result
    if run.status != "solved" or not run.residual.solved or not run.state.facts:
        return ()
    expression, variables = run.evidence[0].value
    after = run.state.facts[-1].value[1]
    if _same(_tree(expression), _tree(after)):
        return ()
    steps = tuple({"lhs": fact.value[0], "rhs": fact.value[1]} for fact in run.state.facts)
    statement = {"lhs": expression, "rhs": after, "variables": list(variables)}
    return (KnowledgeCandidate(
        id="expansion:" + content_digest((result.task.id, expression, after))[:24],
        domain=DOMAIN, kind="polynomial_identity", statement=statement,
        derivation=steps, source_task_id=result.task.id, evidence_refs=(INPUT_ID,),
    ),)


def build_learning_agent(
    problem: PolynomialProblem, library: KnowledgeLibrary, *, max_steps: int = 64,
    counts: WorkCounts | None = None, complete: Callable[[str], str] | None = None,
) -> LearningAgent:
    """Use the same Agent runtime for primitive and recalled proof steps.

    A model callback receives candidate JSON instructions and the available
    checked rules. Its proposed proof steps face the exact same verifier.
    Neither the deterministic proposer nor the callback can admit knowledge.
    """
    counts = counts if counts is not None else WorkCounts()

    def factory(task, records):
        def proposers(task, routes, evidence):
            if complete is None:
                return (PolynomialProposer(problem, records, counts),)
            instruction = (
                f"Expand {problem.expression} over rational variables {problem.variables}. "
                "Use one algebraically equivalent rewrite per step. Candidate claim must be a JSON "
                "string containing exactly before, after, rule_id. before is the latest expression "
                "or original input. rule_id is empty for your own derivation. Cite polynomial:input "
                "then the latest fact ID, if present. Do not claim solved until all products and powers "
                "of sums are expanded. Available verified identities: "
                + json.dumps([{"id": record.candidate.id, **record.candidate.statement}
                              for record in records if record.candidate.kind == "polynomial_identity"],
                             sort_keys=True)
            )
            def counted_complete(prompt):
                counts.proposal_calls += 1
                return complete(prompt)
            return (ModelProposer(counted_complete, allowed_actions=ACTIONS,
                                  evidence=evidence, instruction=instruction),)
        return Agent(domain_factory=lambda task: PolynomialDomain(problem, counts),
                     verifier_factory=lambda task: PolynomialVerifier(problem, records, counts),
                     proposer_factory=proposers, tools=(PolynomialTool(problem),),
                     max_steps=max_steps, max_no_progress=max_steps)

    return LearningAgent(library=library, agent_factory=factory, distill=distill_expansion)


def run_demo() -> dict:
    """An offline derive/save/reload/transfer demonstration with real checks."""
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from .algebra import AlgebraVerifier

    verifiers = {DOMAIN: AlgebraVerifier()}
    store = KnowledgeLibrary(verifiers)
    discovery_counts = WorkCounts()
    discovery = build_learning_agent(
        PolynomialProblem("(u+v)**3", ("u", "v")), store, counts=discovery_counts,
    ).run(Task("discover-cube", "Derive a symbolic cubic expansion", DOMAIN))
    with TemporaryDirectory(prefix="resimind-knowledge-") as directory:
        path = Path(directory) / "rules.json"
        store.save(path)
        reloaded = KnowledgeLibrary.load(path, verifiers)
    problem = PolynomialProblem("(2*x+3*y)**3", ("x", "y"))
    arms = {}
    for name, library in (("fixed", KnowledgeLibrary(verifiers)), ("growing", reloaded)):
        counts = WorkCounts()
        result = build_learning_agent(problem, library, counts=counts).run(
            Task("transfer-cube", "Expand the new polynomial", DOMAIN), learn=False)
        run = result.result.run_result
        arms[name] = {"status": run.status, "steps": run.steps,
                      "proposal_calls": counts.proposal_calls,
                      "identity_checks": counts.identity_checks,
                      "rule_uses": [{"id": fact.value[2], "fingerprint": fact.value[3]}
                                    for fact in run.state.facts if fact.value[2]],
                      "final_expression": run.state.facts[-1].value[1] if run.state.facts else None}
    return {"mode": "offline-deterministic-knowledge-growth", "live_model": False,
            "discovery": {"task": discovery.result.task.id,
                          "status": discovery.result.run_result.status,
                          "proposal_calls": discovery_counts.proposal_calls,
                          "admitted_rules": [record.candidate.id for record in discovery.admissions
                                             if record.status == "verified"]},
            "transfer_expression": problem.expression, "arms": arms,
            "scope": "Exact rational polynomial identities; no model-performance claim."}
