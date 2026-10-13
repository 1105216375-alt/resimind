"""Bounded best-first polynomial search guided by residuals and experience.

Queue entries are *actions*, so ranking a strategy changes which work is
actually executed. Alternatives survive local failures within a global budget.
Schema/library and policy snapshots are frozen by the caller; evaluation never
trains them. Every accepted edge and released endpoint is independently checked.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
import hashlib
import heapq
import json
import time
from typing import Callable

from ..core import content_digest
from ..knowledge import KnowledgeLibrary
from ..obligations import Obligation, ObligationGraph
from ..policy import StrategyObservation, StrategyPolicy
from ..diagnosis import diagnose
from ..integrations.lean import (LeanPolynomialBackend, LeanProofResult, APPROVED_AXIOMS,
                                 SUPPORTED_LEAN_VERSION, polynomial_binding_digest, _source)
from .algebra import validate_expression, verify_identity
from .polynomial_learning import PolynomialProblem, WorkCounts, _primitive, is_expanded, _needs_expansion
from .polynomial_scheduling import expression_cost
from .polynomial_simplification import compact_expression, bounded_distribute
from .schema_learning import apply_schema
from .rule_index import RuleIndex


def _key(expression: str) -> str:
    return ast.dump(ast.parse(expression, mode="eval").body, include_attributes=False)


def residual_graph(expression: str) -> ObligationGraph:
    """Each unresolved AST goal depends on its nearest unresolved children."""
    root = ast.parse(expression, mode="eval").body
    nodes = []

    def visit(node, path):
        children = []
        for name in ("left", "right", "operand"):
            child = getattr(node, name, None)
            if isinstance(child, ast.expr):
                children.extend(visit(child, (*path, name)))
        if _needs_expansion(node):
            ident = "ast:" + "/".join(path)
            nodes.append(Obligation(ident, ast.unparse(node), tuple(children), ("input",)))
            return [ident]
        return children

    top = visit(root, ())
    nodes.append(Obligation("goal", "fully expanded expression", tuple(top), ("input",),
                            "verified" if not top else "pending"))
    return ObligationGraph(tuple(nodes), evidence_revision=content_digest(expression))


def expression_context(expression: str) -> str:
    cost = expression_cost(expression)
    root = ast.parse(expression, mode="eval").body
    power = any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow) for n in ast.walk(root))
    return ("power" if power else "product") + (":large" if cost["ast_nodes"] > 40 else ":small")


@dataclass(frozen=True, slots=True)
class SearchBudget:
    actions: int = 64
    frontier: int = 64
    depth: int = 32
    model_calls: int = 4
    lean_checks: int = 64

    def __post_init__(self):
        for name, lower, upper in (("actions", 1, 512), ("frontier", 1, 512), ("depth", 1, 64),
                                    ("model_calls", 0, 64), ("lean_checks", 0, 512)):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"invalid {name} budget")


@dataclass
class SearchLearningResult:
    status: str = "limited"
    expression: str | None = None
    proof: tuple[dict, ...] = ()
    actions: int = 0
    generated: int = 0
    duplicates: int = 0
    rejected: int = 0
    model_calls: int = 0
    lean_checks: int = 0
    dropped_actions: int = 0
    identity_checks: int = 0
    cached_state_hits: int = 0
    rule_pattern_checks: int = 0
    indexed_rules: int = 0
    skipped_rules: int = 0
    elapsed_seconds: float = 0.0
    observations: list[StrategyObservation] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    stop_reason: str = "action_budget"


def run_search_learning(problem: PolynomialProblem, *, library: KnowledgeLibrary | None = None,
                        policy: StrategyPolicy | None = None, budget: SearchBudget = SearchBudget(),
                        complete: Callable[[str], str] | None = None,
                        lean_backend: LeanPolynomialBackend | None = None,
                        strategies: tuple[str, ...] = ("primitive", "distribute", "compact", "rule", "model"),
                        use_residual: bool = True, indexed_retrieval: bool = False) -> SearchLearningResult:
    if type(problem) is not PolynomialProblem or type(budget) is not SearchBudget:
        raise ValueError("expected PolynomialProblem and SearchBudget")
    if type(strategies) is not tuple or not strategies or len(set(strategies)) != len(strategies) or not set(strategies) <= {"primitive", "distribute", "compact", "rule", "model"}:
        raise ValueError("unsupported strategies")
    if complete is not None and not callable(complete):
        raise ValueError("complete must be callable")
    if policy is not None and (type(policy) is not StrategyPolicy or not policy.frozen):
        raise ValueError("policy must be frozen")
    if type(use_residual) is not bool:
        raise ValueError("use_residual must be bool")
    if type(indexed_retrieval) is not bool:
        raise ValueError("indexed_retrieval must be bool")
    if lean_backend is not None and not isinstance(lean_backend, LeanPolynomialBackend):
        raise ValueError("expected LeanPolynomialBackend")
    started = time.perf_counter()
    result = SearchLearningResult()
    records = () if library is None else library.lookup(domain="algebra", assumptions=())[:16]
    records = tuple(r for r in records if r.candidate.kind == "polynomial_identity" and not r.candidate.assumptions)
    index = RuleIndex(library) if library is not None and indexed_retrieval else None
    state_cache = {}

    def state_info(expression):
        if expression not in state_cache:
            cost = expression_cost(expression)
            power = any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow)
                        for n in ast.walk(ast.parse(expression, mode="eval")))
            context = ("power" if power else "product") + (":large" if cost["ast_nodes"] > 40 else ":small")
            state_cache[expression] = cost, context, residual_graph(expression) if use_residual else None
        else:
            result.cached_state_hits += 1
        return state_cache[expression]
    seen = {_key(problem.expression)}
    queue = []
    serial = 0
    counts = WorkCounts()

    def finish(expression, proof):
        if not is_expanded(expression):
            return False
        result.identity_checks += 1
        if verify_identity(problem.expression, expression, problem.variables).status != "verified":
            return False
        result.status, result.stop_reason = "solved", "verified_expansion"
        result.expression, result.proof = expression, proof
        return True

    def formal_check(before, after, tactic):
        if result.lean_checks >= budget.lean_checks:
            return False, "lean_budget", None
        result.lean_checks += 1
        source, target, mapping = _source(before, after, problem.variables, tactic, lean_backend.max_heartbeats)
        check = lean_backend.check(before, after, problem.variables, tactic=tactic)
        valid = (type(check) is LeanProofResult and check.status == "verified"
                 and check.binding_digest == polynomial_binding_digest(before, after, problem.variables)
                 and check.source_digest == hashlib.sha256(source.encode()).hexdigest()
                 and check.target == target and check.variable_mapping == mapping
                 and check.tactic == tactic and type(check.proof_digest) is str
                 and len(check.proof_digest) == 64 and all(c in "0123456789abcdef" for c in check.proof_digest)
                 and bool(check.actual_target) and not check.goals and not check.diagnostics
                 and check.lean_version == SUPPORTED_LEAN_VERSION and set(check.axioms) <= APPROVED_AXIOMS)
        feedback = str(check.goals or check.diagnostics)[:2048] if type(check) is LeanProofResult else "malformed response"
        return valid, "lean_verified" if valid else "lean_incomplete: " + feedback, source if valid else None

    def retry_model(before, proof, site, record, context, cost):
        nonlocal serial
        if result.model_calls < budget.model_calls:
            serial += 1
            heapq.heappush(queue, ((cost["remaining_actions"], cost["remaining_work"], 99, len(proof), serial),
                                  before, proof, "model", site, record, context))

    def enqueue(expression, proof):
        nonlocal serial, queue
        if len(proof) >= budget.depth:
            return
        cost, context, graph = state_info(expression)
        applicable = index.lookup(expression) if index is not None else records
        available = tuple(s for s in strategies if (s != "model" or complete is not None)
                          and (s != "rule" or applicable))
        order = available if policy is None or not available else policy.rank(context, available)
        ready = graph.ready() if graph is not None else ()
        targets = tuple(item.id for item in ready if item.id.startswith("ast:"))[:8]
        for rank, strategy in enumerate(order):
            # Goal-directed local rewrites use the dependency-ready leaves.
            sites = targets if targets and strategy in ("primitive", "distribute") else ("whole",)
            choices = applicable if strategy == "rule" else (None,)
            for site in sites:
                for record in choices:
                    serial += 1
                    priority = (cost["remaining_actions"], cost["remaining_work"], rank, len(proof), serial)
                    heapq.heappush(queue, (priority, expression, proof, strategy, site, record, context))
        if len(queue) > budget.frontier:
            result.dropped_actions += len(queue) - budget.frontier
            queue = heapq.nsmallest(budget.frontier, queue)
            heapq.heapify(queue)

    if is_expanded(problem.expression):
        if lean_backend is None:
            finish(problem.expression, ())
        else:
            try:
                valid, reason, source = formal_check(problem.expression, problem.expression, "rfl")
            except Exception as exc:
                valid, reason, source = False, f"proposal_error:{type(exc).__name__}", None
            result.trace.append({"before": problem.expression, "after": problem.expression,
                                 "strategy": "identity", "verified": valid, "reason": reason})
            if valid:
                finish(problem.expression, ({"before": problem.expression, "after": problem.expression,
                    "strategy": "identity", "rule_id": None, "lean_source": source},))
    else:
        enqueue(problem.expression, ())
    while queue and result.status != "solved" and result.actions < budget.actions:
        _, before, proof, strategy, site, record, context = heapq.heappop(queue)
        if strategy == "model" and result.model_calls >= budget.model_calls:
            continue
        result.actions += 1
        before_cost = state_info(before)[0]
        after, reason, verified, lean_source = None, "no_candidate", False, None
        stage = "local_proposal"
        action_started = time.perf_counter()
        tactic = "grind"
        try:
            tree = ast.parse(before, mode="eval").body
            path = () if site == "whole" else tuple(filter(None, site[4:].split("/")))
            target = tree
            for field_name in path:
                target = getattr(target, field_name)
            source = ast.unparse(target)
            if strategy == "primitive":
                after = _primitive(source, counts)
            elif strategy == "distribute":
                after = bounded_distribute(source, problem.variables)
            elif strategy == "compact":
                after = compact_expression(before, problem.variables)
            elif strategy == "rule":
                after = apply_schema(library, record, before, problem.variables, counts)
            else:
                result.model_calls += 1
                feedback = [row for row in result.trace if row["before"] == before][-3:]
                stage = "model_callback"
                raw = complete(json.dumps({"goal": "Expand this rational polynomial", "before": before,
                    "variables": problem.variables, "ready_subgoals": [asdict(item) for item in residual_graph(before).ready()],
                    "feedback": feedback, "reply": {"after": "Python expression with explicit * and **", "tactic": "grind or rfl"}}))
                stage = "model_response_size"
                if type(raw) is not str or len(raw) > 32768:
                    raise ValueError("model_response_size")
                stage = "model_response_json"
                data = json.loads(raw)
                stage = "model_response_schema"
                if type(data) is not dict or not {"after"} <= set(data) <= {"after", "tactic"}:
                    raise ValueError("model_response_schema")
                after, tactic = data["after"], data.get("tactic", "grind")
                stage = "unsupported_tactic_use_grind_or_rfl"
                if tactic not in ("rfl", "grind"):
                    raise ValueError("unsupported_tactic")
            if after is not None:
                stage = "expression_validation_use_bounded_balanced_sums"
                validate_expression(after, problem.variables)
                if path and strategy in ("primitive", "distribute"):
                    parent = tree
                    for field_name in path[:-1]:
                        parent = getattr(parent, field_name)
                    setattr(parent, path[-1], ast.parse(after, mode="eval").body)
                    after = ast.unparse(ast.fix_missing_locations(tree))
                validate_expression(after, problem.variables)
                result.generated += 1
                result.identity_checks += 1
                stage = "identity_verification"
                identity = verify_identity(before, after, problem.variables)
                reason = identity.reason
                verified = identity.status == "verified"
                if verified and lean_backend is not None:
                    stage = "lean_backend"
                    verified, reason, lean_source = formal_check(before, after, tactic)
                if verified and record is not None:
                    stage = "rule_revalidation"
                    current = library.get(record.candidate.id)
                    if current.status != "verified" or current.fingerprint != record.fingerprint:
                        verified, reason = False, "rule_revoked_or_changed"
        except Exception as exc:
            # Application callbacks may fail; the failure stays visible and no
            # candidate is committed. Exception text may contain provider secrets.
            verified, reason = False, f"proposal_error:{stage}:{type(exc).__name__}"
        after_cost = state_info(after)[0] if verified else None
        progress = bool(verified and (is_expanded(after) or after_cost["remaining_work"] < before_cost["remaining_work"]
                                     or after_cost["remaining_actions"] < before_cost["remaining_actions"]))
        work = float(before_cost["ast_nodes"] + (after_cost["ast_nodes"] if after_cost else 0))
        result.observations.append(StrategyObservation(context, strategy, progress, work))
        event = {"before": before, "after": after, "strategy": strategy, "site": site,
                 "verified": verified, "progress": progress, "reason": reason,
                 "diagnosis": asdict(diagnose(reason, verified=verified, progress=progress)),
                 "elapsed_seconds": time.perf_counter() - action_started, "syntax_cost": work}
        result.trace.append(event)
        if not verified:
            result.rejected += 1
            # A model gets bounded opportunities to use concrete proof/error feedback.
            if strategy == "model":
                retry_model(before, proof, site, record, context, before_cost)
            continue
        step = {"before": before, "after": after, "strategy": strategy,
                "rule_id": record.candidate.id if record else None, "lean_source": lean_source}
        path_proof = (*proof, step)
        if finish(after, path_proof):
            break
        key = _key(after)
        if key in seen:
            result.duplicates += 1
            if strategy == "model":
                event["reason"] += "; no_new_state_try_different_rewrite"
                event["diagnosis"] = asdict(diagnose(event["reason"], verified=True, progress=False))
                retry_model(before, proof, site, record, context, before_cost)
            continue
        seen.add(key)
        enqueue(after, path_proof)
    if result.status != "solved" and not queue:
        result.stop_reason = "search_exhausted" if not result.dropped_actions else "frontier_limited"
    result.identity_checks += counts.identity_checks
    if index is not None:
        result.rule_pattern_checks = index.stats.pattern_checks
        result.indexed_rules = index.stats.indexed_rules
        result.skipped_rules = index.stats.skipped_rules
    result.elapsed_seconds = time.perf_counter() - started
    return result
