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
from .algebra import MAX_AST_NODES, validate_expression, verify_identity

DOMAIN = "algebra"
TARGET = "algebra:expanded"
SCOPE = "rational-polynomial-expansion-v1"
INPUT_ID = "polynomial:input"
ACTIONS = ("rewrite_polynomial", "certify_expansion")
RULE_ACTION = "apply_verified_rule"
POLYNOMIAL_SYNTAX_GUIDANCE = (
    "Use Python-style powers '**' (x**2), never '^', and explicit multiplication '*' "
    "(2*x, x*y). Use only declared variables, integers, parentheses, +, -, *, ** with "
    "exponents 0 through 16, and division by nonzero integer literals; use fractions, not decimals."
)


def _tree(expression: str) -> ast.expr:
    return ast.parse(expression.strip(), mode="eval").body


def _same(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(left) == ast.dump(right)


def _text(node: ast.AST) -> str:
    return ast.unparse(ast.fix_missing_locations(node))


def _has_sum(node: ast.AST) -> bool:
    return any(isinstance(part, ast.BinOp) and isinstance(part.op, (ast.Add, ast.Sub))
               for part in ast.walk(node))


def _needs_expansion(node: ast.AST) -> bool:
    return isinstance(node, ast.BinOp) and (
        (isinstance(node.op, ast.Mult) and (_has_sum(node.left) or _has_sum(node.right)))
        or (isinstance(node.op, ast.Pow) and _has_sum(node.left)))


def is_expanded(expression: str) -> bool:
    """A syntactic goal: no multiplication/power still encloses a sum.

    Like terms need not be collected. Callers must separately establish that
    the expression belongs to the validated polynomial grammar.
    """
    return not any(_needs_expansion(node) for node in ast.walk(_tree(expression)))


def _unresolved_subexpressions(expression: str) -> dict:
    """Bounded syntax guidance, not additional proof obligations or answers."""
    nodes = []

    def visit(node, path):
        if _needs_expansion(node):
            rendered = _text(node)
            nodes.append({"path": path, "operator": "*" if isinstance(node.op, ast.Mult) else "**",
                          "expression": rendered[:512], "expression_truncated": len(rendered) > 512})
        for field, child in ast.iter_fields(node):
            if isinstance(child, ast.expr):
                visit(child, path + "." + field)

    visit(_tree(expression), "$")
    # Smaller nested tasks appear before the encompassing product or power.
    nodes.sort(key=lambda item: -item["path"].count("."))
    return {"items": nodes[:12], "total": len(nodes), "omitted": max(0, len(nodes) - 12)}


def _normalized_expression(expression: str) -> str:
    """Recognize formatting-only repeats without computing a polynomial answer."""
    if len(expression) <= 8192:
        try:
            return ast.dump(_tree(expression), include_attributes=False)
        except (SyntaxError, ValueError, RecursionError):
            pass
    return "unparsed:" + content_digest(expression)


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


def _bounded_macro(expression: str, record: KnowledgeRecord, counts: WorkCounts) -> str | None:
    """Materialize one selected rule, rejecting AST growth before substitution."""
    root = _tree(expression)
    root_size = sum(1 for _ in ast.walk(root))
    rule = record.candidate
    pattern, replacement = _tree(rule.statement["lhs"]), _tree(rule.statement["rhs"])
    variables = set(rule.statement["variables"])

    def transform(node):
        counts.pattern_attempts += 1
        bindings: dict[str, ast.AST] = {}
        if not _match(pattern, node, variables, bindings):
            return None
        sizes = {name: sum(1 for _ in ast.walk(value)) for name, value in bindings.items()}

        def substituted_size(part):
            if isinstance(part, ast.Name) and part.id in sizes:
                return sizes[part.id]
            result = 1
            for child in ast.iter_child_nodes(part):
                result += substituted_size(child)
                if result > MAX_AST_NODES:
                    raise ValueError("rule substitution exceeds AST limit")
            return result

        projected = root_size - sum(1 for _ in ast.walk(node)) + substituted_size(replacement)
        if projected > MAX_AST_NODES:
            raise ValueError("rule substitution exceeds AST limit")
        changed = _substitute(replacement, bindings)
        return None if _same(changed, node) else changed

    result = _rewrite_first(root, transform)
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
        def reply(decision, reason, facts=(), *, diagnostic=None):
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence,
                                         reasons=(reason,) if diagnostic is None else (reason, diagnostic),
                                         facts=facts)
        if candidate.action not in ACTIONS or candidate.target != TARGET or TARGET not in residual.pending:
            return reply(Decision.REJECT, "unsupported_polynomial_step")
        expected = PolynomialTool(self.problem).collect(Task("verify", "Verify", DOMAIN))
        refs = (INPUT_ID,) + ((state.facts[-1].id,) if state.facts else ())
        if evidence != expected or candidate.refs != refs:
            return reply(Decision.REJECT, "polynomial_evidence_mismatch")
        current = _current(self.problem, state, self.counts)
        if len(candidate.claim) > 32768:
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite", diagnostic=(
                "rewrite_claim_size: claim exceeds 32768 characters; submit a smaller rewrite."))
        try:
            payload = json.loads(candidate.claim, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, RecursionError):
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite", diagnostic=(
                "rewrite_claim_json: claim must be a JSON object serialized inside the claim string, "
                "with double-quoted keys and string values and no duplicate keys. Escape its quotes "
                "inside the outer candidate JSON. Text such as before=...; after=... is not JSON."))
        if (type(payload) is not dict or set(payload) != {"before", "after", "rule_id"}
                or any(type(value) is not str for value in payload.values())):
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite", diagnostic=(
                "rewrite_claim_schema: the JSON object inside claim must have exactly before, "
                "after, rule_id, all string values. Use an empty string for rule_id when deriving your own rewrite."))
        before, after, rule_id = (payload[key] for key in ("before", "after", "rule_id"))
        if _normalized_expression(before) != _normalized_expression(current):
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite", diagnostic=(
                "rewrite_before_mismatch: copy the current expression from the latest fact's after "
                "or the original input if no facts exist. Whitespace and redundant parentheses may "
                "differ, but operand order and the parsed syntax tree must match; algebraic equivalence alone is insufficient."))
        # Formatting does not change the input AST. Commit the actual current
        # representation so the proof chain remains byte-for-byte continuous.
        before = current
        try:
            validate_expression(after, self.problem.variables)
        except (ValueError, TypeError, RecursionError):
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite", diagnostic=(
                "rewrite_after_unsupported: " + POLYNOMIAL_SYNTAX_GUIDANCE + " Submit a smaller step if needed."))
        self.counts.identity_checks += 1
        identity = verify_identity(before, after, self.problem.variables)
        if identity.status != "verified":
            return reply(Decision.REJECT, "polynomial_identity_not_proved", diagnostic=identity.reason)
        fingerprint = ""
        if rule_id:
            record = self.records.get(rule_id)
            recalled = None if record is None else _macro(before, record, self.counts)
            if recalled is None or not _same(_tree(recalled), _tree(after)):
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


def _state_fingerprint(problem: PolynomialProblem, state: State, expression: str) -> str:
    return content_digest(("polynomial-state-bound.v1", SCOPE, problem.subject,
                           state.revision, expression))


class _StateBoundPolynomialVerifier:
    """Verify an explicitly selected action against the actual committed state."""

    def __init__(self, problem, records, counts, *, rule_lookup, allow_rule_execution=False):
        self.problem, self.counts = problem, counts
        self.records = {record.candidate.id: record for record in _expansion_rules(records)}
        self.rule_lookup = rule_lookup
        self.actions = ACTIONS + ((RULE_ACTION,) if allow_rule_execution else ())

    def verify(self, candidate, state, residual, evidence):
        def reply(decision, reason, facts=(), *, diagnostic=None):
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence,
                                         reasons=(reason,) if diagnostic is None else (reason, diagnostic),
                                         facts=facts)

        if candidate.action not in self.actions or candidate.target != TARGET or TARGET not in residual.pending:
            return reply(Decision.REJECT, "unsupported_polynomial_step")
        expected = PolynomialTool(self.problem).collect(Task("verify", "Verify", DOMAIN))
        refs = (INPUT_ID,) + ((state.facts[-1].id,) if state.facts else ())
        if evidence != expected or candidate.refs != refs:
            return reply(Decision.REJECT, "polynomial_evidence_mismatch")
        current = _current(self.problem, state, self.counts)
        try:
            if len(candidate.claim) > 32768:
                raise ValueError("claim too long")
            payload = json.loads(candidate.claim, object_pairs_hook=_unique_object)
        except (ValueError, TypeError, RecursionError):
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite", diagnostic=(
                "state_bound_claim_json: use one JSON object serialized inside claim; duplicate keys are forbidden."))
        executing = candidate.action == RULE_ACTION
        expected_fields = {"state_revision", "state_fingerprint", "rule_id",
                           "rule_fingerprint" if executing else "after"}
        if (type(payload) is not dict or set(payload) != expected_fields
                or type(payload["state_revision"]) is not int
                or any(type(payload[key]) is not str for key in expected_fields - {"state_revision"})):
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite", diagnostic=(
                "state_bound_claim_schema: use exactly the displayed fields for the selected action; "
                "state_revision must be an integer, with all other fields strings."))
        if (payload["state_revision"] != state.revision
                or payload["state_fingerprint"] != _state_fingerprint(self.problem, state, current)):
            return reply(Decision.REJECT, "polynomial_state_mismatch", diagnostic=(
                "Copy state_revision and state_fingerprint from the CURRENT prompt. "
                "An accepted step advances the state; previous bindings cannot be replayed."))
        rule_id = payload["rule_id"]
        record = None
        if rule_id:
            initial = self.records.get(rule_id)
            try:
                record = self.rule_lookup(rule_id) if initial is not None else None
            except KeyError:
                record = None
            if (record is None or record.status != "verified" or record.verification.status != "verified"
                    or record.candidate.id != rule_id or record.fingerprint != initial.fingerprint
                    or record != initial):
                return reply(Decision.REJECT, "rule_unavailable_or_changed")
        if executing and (record is None or payload["rule_fingerprint"] != record.fingerprint):
            return reply(Decision.REJECT, "rule_fingerprint_mismatch")
        try:
            recalled = _bounded_macro(current, record, self.counts) if record is not None else None
        except (ValueError, RecursionError):
            return reply(Decision.REJECT, "rule_result_unsupported")
        if record is not None and recalled is None:
            return reply(Decision.REJECT, "rule_not_applicable")
        after = recalled if executing else payload["after"]
        try:
            validate_expression(after, self.problem.variables)
        except (ValueError, TypeError, RecursionError):
            return reply(Decision.REJECT, "malformed_or_unsupported_rewrite", diagnostic=(
                "rewrite_after_unsupported: " + POLYNOMIAL_SYNTAX_GUIDANCE + " Submit a smaller step if needed."))
        self.counts.identity_checks += 1
        identity = verify_identity(current, after, self.problem.variables)
        if identity.status != "verified":
            return reply(Decision.REJECT, "polynomial_identity_not_proved", diagnostic=identity.reason)
        if record is not None and not _same(_tree(recalled), _tree(after)):
            return reply(Decision.REJECT, "rule_not_applicable")
        if _same(_tree(current), _tree(after)) and not is_expanded(after):
            return reply(Decision.REJECT, "rewrite_makes_no_progress")
        if candidate.action == "certify_expansion" and not is_expanded(after):
            return reply(Decision.REJECT, "expansion_still_incomplete")
        step = len(state.facts) + 1
        fact = Fact(f"polynomial:step:{step}", self.problem.subject, f"rewrite:{step}",
                    (current, after, rule_id, record.fingerprint if record else ""),
                    "rational-polynomial", (INPUT_ID,), SCOPE)
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


class _StructuredPolynomialProposer(ModelProposer):
    """Add bounded, state-scoped hints without changing candidate verification."""

    def __init__(self, *args, problem: PolynomialProblem, **kwargs):
        super().__init__(*args, **kwargs)
        self.problem = problem
        self._rejections: dict[tuple[str, str], dict] = {}

    def _expression(self, state: State) -> str:
        return state.facts[-1].value[1] if state.facts else self.problem.expression

    def _state_key(self, state: State) -> str:
        return content_digest((state.revision, self._expression(state)))

    def observe(self, event: TraceEvent) -> None:
        super().observe(event)
        candidate = event.candidate
        if event.decision is Decision.ACCEPT or candidate is None:
            return
        try:
            if len(candidate.claim) > 32768:
                return
            rewrite = json.loads(candidate.claim, object_pairs_hook=_unique_object)
            if (type(rewrite) is not dict or set(rewrite) != {"before", "after", "rule_id"}
                    or any(type(value) is not str for value in rewrite.values())):
                return
        except (ValueError, TypeError, RecursionError):
            return
        signature = content_digest((candidate.action, candidate.target,
                                    _normalized_expression(rewrite["before"]),
                                    _normalized_expression(rewrite["after"]), rewrite["rule_id"]))
        key = (self._state_key(event.before), signature)
        previous = self._rejections.pop(key, None)
        self._rejections[key] = {
            "rewrite_fingerprint": signature,
            "action": candidate.action,
            "after": rewrite["after"][:512],
            "after_truncated": len(rewrite["after"]) > 512,
            "rule_id": rewrite["rule_id"][:128],
            "rule_id_truncated": len(rewrite["rule_id"]) > 128,
            "before_matches_current": (
                _normalized_expression(rewrite["before"])
                == _normalized_expression(self._expression(event.before))),
            "occurrences": 1 if previous is None else previous["occurrences"] + 1,
            "last_step": event.step,
            "reasons": [reason[:256] for reason in event.reasons[:3]],
        }
        while len(self._rejections) > 8:
            del self._rejections[next(iter(self._rejections))]

    def _prompt(self, state: State, residual: Residual) -> str:
        payload = json.loads(super()._prompt(state, residual))
        state_key = self._state_key(state)
        history = [record for (key, _), record in self._rejections.items() if key == state_key]
        payload["polynomial_guidance"] = {
            "protocol": "polynomial-structured-guidance.v1",
            "state_revision": state.revision,
            "unresolved_subexpressions": _unresolved_subexpressions(self._expression(state)),
            "rejected_rewrites_at_current_state": history,
            "repeated_rewrite_count": sum(record["occurrences"] - 1 for record in history),
            "instructions": (
                "The subexpressions identify syntax still needing expansion; they are hints, "
                "not independent proof obligations or replacement answers. Paths start at $ and "
                "follow Python expression AST fields. Deepest subexpressions are listed first. "
                "You may rewrite a smaller subexpression and preserve the rest exactly, but "
                "before and after must still be whole expressions. A complete expansion is also allowed. "
                "Apply a coefficient diagnostic only to its named monomial; it does not authorize "
                "changing other coefficients or certify the remaining expression. History counts "
                "formatting-equivalent rewrites even when candidate IDs change. Inspect the reasons: "
                "for repeated identity mismatches or no-progress rewrites, do not submit the same "
                "failed rewrite again; try one smaller unresolved subexpression or factor-by-factor "
                "distribution. A reference or action error may instead need corrected refs or action "
                "with an unchanged after expression. No hint, rule, or prior feedback bypasses verification."
            ),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)


class _StateBoundPolynomialProposer(ModelProposer):
    """Expose current state directly; never compute a proposed mathematical answer."""

    def __init__(self, *args, problem, records, structured=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.problem = problem
        self.records = _expansion_rules(records)
        self.structured = structured
        self._rejections: dict[tuple[str, str], dict] = {}

    def _expression(self, state):
        return state.facts[-1].value[1] if state.facts else self.problem.expression

    def observe(self, event):
        super().observe(event)
        if not self.structured or event.decision is Decision.ACCEPT or event.candidate is None:
            return
        candidate = event.candidate
        normalized = candidate.claim
        try:
            if len(candidate.claim) <= 32768:
                parsed = json.loads(candidate.claim, object_pairs_hook=_unique_object)
                if type(parsed) is dict:
                    normalized = dict(parsed)
                    if type(parsed.get("after")) is str:
                        normalized["after"] = _normalized_expression(parsed["after"])
        except (ValueError, TypeError, RecursionError):
            pass
        try:
            signature = content_digest((candidate.action, candidate.target, normalized))
        except (ValueError, TypeError, RecursionError):
            signature = content_digest((candidate.action, candidate.target, candidate.claim))
        state_key = _state_fingerprint(self.problem, event.before, self._expression(event.before))
        key = (state_key, signature)
        previous = self._rejections.pop(key, None)
        self._rejections[key] = {
            "rewrite_fingerprint": signature, "action": candidate.action,
            "claim_preview": candidate.claim[:768], "claim_truncated": len(candidate.claim) > 768,
            "occurrences": 1 if previous is None else previous["occurrences"] + 1,
            "last_step": event.step, "reasons": [reason[:256] for reason in event.reasons[:3]],
        }
        while len(self._rejections) > 8:
            del self._rejections[next(iter(self._rejections))]

    def _prompt(self, state, residual):
        payload = json.loads(super()._prompt(state, residual))
        current = self._expression(state)
        fingerprint = _state_fingerprint(self.problem, state, current)
        refs = [INPUT_ID] + ([state.facts[-1].id] if state.facts else [])
        binding = {"state_revision": state.revision, "state_fingerprint": fingerprint}
        schemas = {
            action: {"type": "object", "additionalProperties": False,
                     "required": ["state_revision", "state_fingerprint", "after", "rule_id"],
                     "properties": {"state_revision": {"type": "integer", "const": state.revision},
                                    "state_fingerprint": {"type": "string", "const": fingerprint},
                                    "after": {"type": "string"}, "rule_id": {"type": "string"}}}
            for action in ACTIONS
        }
        examples = [{"id": f"example-revision-{state.revision}", "action": "rewrite_polynomial",
                     "target": TARGET, "claim": json.dumps({**binding, "after": "YOUR_EQUIVALENT_REWRITE",
                                                           "rule_id": ""}, sort_keys=True), "refs": refs}]
        if RULE_ACTION in self.allowed_actions:
            schemas[RULE_ACTION] = {
                "type": "object", "additionalProperties": False,
                "required": ["state_revision", "state_fingerprint", "rule_id", "rule_fingerprint"],
                "properties": {"state_revision": {"type": "integer", "const": state.revision},
                               "state_fingerprint": {"type": "string", "const": fingerprint},
                               "rule_id": {"type": "string", "minLength": 1},
                               "rule_fingerprint": {"type": "string", "minLength": 1}},
            }
            examples.append({"id": f"example-rule-revision-{state.revision}", "action": RULE_ACTION,
                             "target": TARGET, "claim": json.dumps({
                                 **binding, "rule_id": "SELECT_A_LISTED_RULE_ID",
                                 "rule_fingerprint": "COPY_SELECTED_RULE_FINGERPRINT"}, sort_keys=True),
                             "refs": refs})
        payload.update(polynomial_protocol="polynomial-state-bound.v1", current_expression=current,
                       state_revision=state.revision, state_fingerprint=fingerprint,
                       required_reference_ids=refs, claim_schemas=schemas,
                       current_state_serialization_examples=examples,
                       available_verified_identities=[{
                           "id": record.candidate.id, "fingerprint": record.fingerprint,
                           **record.candidate.statement} for record in self.records])
        payload["candidate_schema"]["properties"]["refs"]["const"] = refs
        if self.structured:
            history = [record for (key, _), record in self._rejections.items() if key == fingerprint]
            payload["polynomial_guidance"] = {
                "protocol": "polynomial-structured-state-bound.v1", "state_revision": state.revision,
                "unresolved_subexpressions": _unresolved_subexpressions(current),
                "rejected_rewrites_at_current_state": history,
                "repeated_rewrite_count": sum(record["occurrences"] - 1 for record in history),
                "instructions": (
                    "Subterms are bounded syntax hints, not answers or independent proof obligations. "
                    "Rewrite one smaller subterm while preserving the rest, or propose a complete expansion. "
                    "after must describe the entire new expression. Apply a coefficient diagnostic only to its "
                    "named monomial. Repeated identity failures favor a smaller local rewrite. Stale state or "
                    "reference errors require copying the CURRENT bindings and refs, not replaying the prior step. "
                    "All proposals and selected rules still require independent verification."),
            }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)


def build_learning_agent(
    problem: PolynomialProblem, library: KnowledgeLibrary, *, max_steps: int = 64,
    counts: WorkCounts | None = None, complete: Callable[[str], str] | None = None,
    guidance: str = "default", proposal_protocol: str = "legacy", allow_rule_execution: bool = False,
) -> LearningAgent:
    """Use the same Agent runtime for primitive and recalled proof steps.

    A model callback receives candidate JSON instructions and the available
    checked rules. Its proposed proof steps face the exact same verifier.
    Neither the deterministic proposer nor the callback can admit knowledge.
    Opt-in ``guidance="structured"`` adds bounded syntax subterms and rejected
    rewrite history to neural prompts. It supplies no computed answer, automatic
    fallback, or additional model call; the core expansion goal is unchanged.
    ``proposal_protocol="state_bound"`` is opt-in for neural callbacks: actions
    cite the current revision and fingerprint instead of copying the prior
    expression. ``allow_rule_execution=True`` additionally lets the model select
    one verified rule for bounded symbolic substitution and independent checking.
    Neither option changes the default legacy or deterministic proposer.
    """
    if guidance not in ("default", "structured"):
        raise ValueError("guidance must be 'default' or 'structured'")
    if proposal_protocol not in ("legacy", "state_bound"):
        raise ValueError("proposal_protocol must be 'legacy' or 'state_bound'")
    if type(allow_rule_execution) is not bool:
        raise ValueError("allow_rule_execution must be a bool")
    if allow_rule_execution and proposal_protocol != "state_bound":
        raise ValueError("rule execution requires the state_bound protocol")
    if proposal_protocol == "state_bound" and complete is None:
        raise ValueError("state_bound protocol requires a model completion callback")
    counts = counts if counts is not None else WorkCounts()

    def factory(task, records):
        def proposers(task, routes, evidence):
            if complete is None:
                return (PolynomialProposer(problem, records, counts),)
            if proposal_protocol == "state_bound":
                def state_counted_complete(prompt):
                    counts.proposal_calls += 1
                    return complete(prompt)
                instruction = (
                    f"Expand current_expression over rational variables {problem.variables}. "
                    "Use declared variables, integer literals, unary +/-, +, -, *, division by a nonzero "
                    "integer literal, and integer powers 0 through 16. Write rational coefficients as "
                    "fractions, not decimals. One equivalent whole-expression rewrite per action; a complete "
                    "expansion in one action is allowed. Copy state_revision, state_fingerprint and "
                    "required_reference_ids from THIS prompt. After an accepted step these bindings change. "
                    "claim must be a JSON object serialized inside the outer claim string, matching the selected "
                    "action's claim_schema. The displayed examples contain placeholders, not mathematical answers. "
                    "For rewrite_polynomial or certify_expansion, supply after and rule_id. Use an empty rule_id "
                    "for your own derivation. A nonempty rule_id requires after to be exactly one substitution "
                    "of that identity at the first matching current subexpression (root, then children left to right); "
                    "additional simplification requires an empty rule_id. Do not replay an accepted initial rewrite. "
                    "Apply coefficient feedback only to its named monomial. The task is complete only when "
                    "all products and powers enclosing sums are expanded and the verifier accepts."
                )
                if allow_rule_execution:
                    instruction += (
                        " Inspect the listed verified identities. If a listed identity matches an unexpanded "
                        "subexpression of current_expression, prefer apply_verified_rule over manually reproducing "
                        "that expansion; otherwise use your own rewrite. Select the matching rule_id and its rule_fingerprint. "
                        "Do not supply after for this action: the verifier performs exactly one first-match AST "
                        "substitution and independently checks the resulting identity. Choose a rule only when "
                        "its lhs matches a current subexpression. No rule is automatically selected or applied."
                    )
                return (_StateBoundPolynomialProposer(
                    state_counted_complete, problem=problem, records=records,
                    structured=guidance == "structured",
                    allowed_actions=ACTIONS + ((RULE_ACTION,) if allow_rule_execution else ()),
                    evidence=evidence, instruction=instruction),)
            instruction = (
                f"Expand {problem.expression} over rational variables {problem.variables}. "
                "Supported expression syntax: declared variables, integer literals, parentheses, "
                "unary +/-, +, -, *, division by a nonzero integer literal, and integer powers "
                "from 0 through 16. Write rational coefficients as division, not decimals. "
                "Use one algebraically equivalent rewrite per step; a complete expansion in a "
                "single step is allowed, with no restriction to primitive rewrites. "
                "Candidate claim must be a JSON "
                "string containing exactly before, after, rule_id. before is the latest expression "
                "or original input: whitespace and redundant parentheses may differ, but its parsed "
                "expression tree must match exactly, including operand order and operators. "
                "Using a supplied rule is optional: set rule_id to an empty "
                "string for your own derivation. Only cite a supplied rule_id when after is exactly one structural "
                "substitution of that identity at its first matching subexpression (search the "
                "whole expression first, then its children left to right). Whitespace and redundant "
                "parentheses may differ; additional simplification or multiple substitutions "
                "require an empty rule_id. Cite polynomial:input "
                "then the latest fact ID, if present. Do not claim solved until all products and powers "
                "of sums are expanded. The claim string must itself contain valid JSON, not prose "
                "such as before=...; after=...; rule_id=.... Serialization-only first-step example "
                "(replace YOUR_EQUIVALENT_REWRITE with your own expression and use a fresh id; "
                "for later steps update before and refs to the current state): "
                + json.dumps({"id": "example-step", "action": "rewrite_polynomial", "target": TARGET,
                              "claim": json.dumps({"before": problem.expression,
                                                   "after": "YOUR_EQUIVALENT_REWRITE", "rule_id": ""}),
                              "refs": [INPUT_ID]}, sort_keys=True)
                + " Available verified identities: "
                + json.dumps([{"id": record.candidate.id, **record.candidate.statement}
                              for record in records if record.candidate.kind == "polynomial_identity"],
                             sort_keys=True)
            )
            def counted_complete(prompt):
                counts.proposal_calls += 1
                return complete(prompt)
            if guidance == "structured":
                return (_StructuredPolynomialProposer(
                    counted_complete, problem=problem, allowed_actions=ACTIONS,
                    evidence=evidence, instruction=instruction),)
            return (ModelProposer(counted_complete, allowed_actions=ACTIONS,
                                  evidence=evidence, instruction=instruction),)
        return Agent(domain_factory=lambda task: PolynomialDomain(problem, counts),
                     verifier_factory=lambda task: (
                         _StateBoundPolynomialVerifier(problem, records, counts, rule_lookup=library.get,
                                                       allow_rule_execution=allow_rule_execution)
                         if proposal_protocol == "state_bound" else PolynomialVerifier(problem, records, counts)),
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
