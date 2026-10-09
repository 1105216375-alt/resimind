"""Bounded strategy adaptation around the existing exact polynomial verifier.

Models propose text only. Local edits, recalled rules, primitive AST rewrites,
and append-only rollback transitions all pass the same Engine verification gate.
No polynomial coefficient calculator is used to generate answers here.
"""
from __future__ import annotations

import ast
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
import json

from ..agent import Agent, Task
from ..core import Candidate, Decision, State, Verdict, content_digest
from ..knowledge import KnowledgeLibrary
from ..learning import LearningAgent
from ..strategy import FailureKind, StrategyController
from .algebra import MAX_DERIVATION_STEPS, MAX_SOURCE_LENGTH, _tree as _bounded_tree
from .polynomial_learning import (
    DOMAIN, INPUT_ID, TARGET, RULE_ACTION, PolynomialDomain, PolynomialProblem,
    PolynomialTool, WorkCounts, _StateBoundPolynomialVerifier, _expansion_rules,
    _match, _needs_expansion, _normalized_expression, _primitive, _state_fingerprint,
    _text, _tree, _unique_object, distill_expansion, is_expanded,
)

STRATEGIES = ("whole_model", "local_model", "verified_rule", "primitive", "rollback")


@dataclass
class AdaptiveStats:
    """JSON-safe cumulative counters; supplied instances may span task runs.

    Each run gets fresh controller state and per-task budgets. Rollback does not
    reset either budget. ``stop_reason`` describes the most recent run. Events
    are bounded; ``events_omitted`` reports any dropped audit entries.
    """
    model_calls: int = 0
    whole_model_attempts: int = 0
    whole_model_accepts: int = 0
    local_model_attempts: int = 0
    local_model_accepts: int = 0
    model_failures: int = 0
    schema_failures: int = 0
    model_abstentions: int = 0
    technical_failures: int = 0
    strategy_switches: int = 0
    duplicate_suppressions: int = 0
    rollback_attempts: int = 0
    rollback_accepts: int = 0
    rule_attempts: int = 0
    rule_accepts: int = 0
    primitive_attempts: int = 0
    primitive_accepts: int = 0
    action_attempts: int = 0
    failures: int = 0
    model_budget_exhausted: bool = False
    action_budget_exhausted: bool = False
    diagnostic_counts: dict[str, int] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    events_omitted: int = 0
    stop_reason: str = "not_started"


def _semantic(expression: str) -> str:
    return content_digest(_normalized_expression(expression))


def _paths(expression: str) -> list[tuple[tuple[str, ...], str]]:
    """Return actual unexpanded subterms, deepest first; no expected answers."""
    found = []

    def visit(node, path):
        if _needs_expansion(node):
            found.append((path, _text(node)))
        for name in ("left", "right", "operand"):
            child = getattr(node, name, None)
            if isinstance(child, ast.expr):
                visit(child, path + (name,))

    visit(_bounded_tree(expression), ())
    found.sort(key=lambda item: -len(item[0]))
    return found[:16]


def _replace_path(expression: str, path: tuple[str, ...], replacement: str) -> str:
    """Replace exactly a trusted selected path; never execute submitted Python."""
    if (type(path) is not tuple or any(type(part) is not str or part not in ("left", "right", "operand")
                                       for part in path)):
        raise ValueError("unsupported AST path")
    root, new = _bounded_tree(expression), _bounded_tree(replacement)
    if not path:
        result = new
    else:
        result = deepcopy(root)
        parent = result
        for field_name in path[:-1]:
            parent = getattr(parent, field_name, None)
            if not isinstance(parent, ast.expr):
                raise ValueError("AST path does not identify an expression")
        if not isinstance(getattr(parent, path[-1], None), ast.expr):
            raise ValueError("AST path does not identify an expression")
        setattr(parent, path[-1], new)
    rendered = _text(result)
    _bounded_tree(rendered)
    return rendered


def _diagnose(reasons: tuple[str, ...]) -> FailureKind:
    text = " ".join(reasons)
    if any(word in text for word in ("schema", "json", "state_mismatch", "evidence", "reference", "binding",
                                    "rewrite_before_mismatch")):
        return FailureKind.SCHEMA_BINDING
    if any(word in text for word in ("resource", "unsupported", "limit", "budget", "too_long", "strategy_exhausted")):
        return FailureKind.RESOURCE
    if "identity" in text or "coefficient" in text:
        return FailureKind.IDENTITY_MATH
    if any(word in text for word in ("rule_", "no_matching_rule")):
        return FailureKind.UNAVAILABLE_RULE
    if any(word in text for word in ("duplicate", "cycle", "no_progress", "no_primitive", "abstain")):
        return FailureKind.NO_PROGRESS
    return FailureKind.UNKNOWN_ERROR


class _AdaptiveProposer:
    def __init__(self, problem, library, records, complete, counts, stats, task_id,
                 max_model_calls, max_steps, max_rollbacks):
        self.problem, self.library, self.records = problem, library, _expansion_rules(records)[:16]
        self.complete, self.counts, self.stats, self.task_id = complete, counts, stats, task_id
        self.max_model_calls, self.max_steps, self.max_rollbacks = max_model_calls, max_steps, max_rollbacks
        self.controller = StrategyController(STRATEGIES, max_attempts=max_steps,
                                             max_attempts_per_strategy=3, max_failures_per_strategy=2)
        self.model_calls = self.attempts = self.rollbacks = 0
        initial = _semantic(problem.expression)
        self.seen = {initial}
        self.ancestors = [{"key": initial, "expression": problem.expression, "revision": 0}]
        self.failed_edges: set[tuple[str, str]] = set()
        self.edge_strategies = {}
        self.disabled = set()
        self.failed_paths = set()
        self.failures_at = {}
        self.failed_rules = set()
        self.rollback_requested = set()
        self.priority = list(STRATEGIES)
        self.last_strategy = None
        self.last_diagnostics = []
        self.pending = None
        self.metadata = {}
        self.no_more_strategies = False

    def _audit(self, value):
        if len(self.stats.events) < 512:
            self.stats.events.append({"task_id": self.task_id, **value})
        else:
            self.stats.events_omitted += 1

    def _current(self, state):
        return state.facts[-1].value[1] if state.facts else self.problem.expression

    def _find_rule(self, expression, state_key):
        for initial in self.records:
            if (state_key, initial.candidate.id) in self.failed_rules:
                continue
            try:
                record = self.library.get(initial.candidate.id)
            except KeyError:
                continue
            if record != initial or record.status != "verified":
                continue
            pattern = _tree(record.candidate.statement["lhs"])
            variables = set(record.candidate.statement["variables"])
            for node in ast.walk(_tree(expression)):
                if not isinstance(node, ast.expr):
                    continue
                self.counts.pattern_attempts += 1
                if _match(pattern, node, variables, {}):
                    return record
        return None

    def _available(self, state, current, key):
        paths = _paths(current)
        local = next((item for item in paths if (key, "local_model", item[0]) not in self.failed_paths), None)
        primitive = next((item for item in paths if (key, "primitive", item[0]) not in self.failed_paths), None)
        if primitive is None and not paths:
            primitive = ((), current)
        rule = self._find_rule(current, key)
        rollback = None
        if (self.rollbacks < self.max_rollbacks and len(self.ancestors) > 1
                and key in self.rollback_requested):
            rollback = self.ancestors[-2]
        model_available = self.complete is not None and self.model_calls < self.max_model_calls
        choices = {
            "whole_model": model_available and (key, "whole_model") not in self.disabled,
            "local_model": model_available and local is not None and (key, "local_model") not in self.disabled,
            "verified_rule": rule is not None,
            "primitive": primitive is not None,
            "rollback": rollback is not None,
        }
        priority = (["rollback"] + [name for name in self.priority if name != "rollback"]
                    if rollback is not None else self.priority)
        return tuple(name for name in priority if choices[name]), local, primitive, rule, rollback

    def _model_after(self, state, current, strategy, selected):
        self.model_calls += 1
        self.stats.model_calls += 1
        self.counts.proposal_calls += 1
        self.stats.model_budget_exhausted |= self.model_calls >= self.max_model_calls
        selected_path = None if selected is None else "$" + "".join("." + part for part in selected[0])
        prompt = {
            "protocol": "adaptive-polynomial-proposal.v1", "strategy": strategy,
            "current_expression": current, "variables": list(self.problem.variables),
            "state_revision": state.revision,
            "state_fingerprint": _state_fingerprint(self.problem, state, current),
            "selected_subexpression": None if selected is None else {
                "path": selected_path, "expression": selected[1]},
            "response_schema": {"type": "object", "additionalProperties": False,
                                "required": ["after"], "properties": {"after": {"type": "string"}}},
            "recent_diagnostics": self.last_diagnostics[-3:],
            "instruction": (
                "Return exactly one JSON object {\"after\": \"your polynomial expression\"}, or null. "
                "Use declared variables, integers, +, -, *, powers 0 through 16, and division by nonzero integer literals. "
                "Do not return a path, rule, code, or self-verification. A separate verifier checks the identity. "
                "Apply coefficient feedback only to the named monomial. "
                + ("Expand only selected_subexpression. Your after replaces exactly that selected AST path; "
                   "do not include or modify its siblings or the whole expression."
                   if selected is not None else
                   "Propose an equivalent expansion or useful intermediate rewrite of the whole current_expression.")),
        }
        try:
            text = self.complete(json.dumps(prompt, ensure_ascii=False, sort_keys=True))
        except Exception as exc:
            self.stats.technical_failures += 1
            self.pending["host_reasons"] = ("model_transport_error", type(exc).__name__)
            return None
        try:
            if type(text) is not str or len(text) > 32768:
                raise ValueError("invalid response size")
            result = json.loads(text, object_pairs_hook=_unique_object)
            if result is None:
                self.stats.model_abstentions += 1
                self.pending["host_reasons"] = ("model_abstained",)
                return None
            if type(result) is not dict or set(result) != {"after"} or type(result["after"]) is not str:
                raise ValueError("invalid local response schema")
            if len(result["after"]) > MAX_SOURCE_LENGTH:
                self.pending["host_reasons"] = ("model_after_resource_limit",)
                return None
            # Syntax-only bounds here. Domain verification remains the truth gate.
            _bounded_tree(result["after"])
            return result["after"]
        except (ValueError, TypeError, RecursionError):
            self.stats.schema_failures += 1
            self.pending["host_reasons"] = ("model_response_schema_or_syntax",)
            return None

    def propose(self, state, residual):
        current = self._current(state)
        key = _semantic(current)
        self.attempts += 1
        self.stats.action_attempts += 1
        available, local, primitive, rule, rollback = self._available(state, current, key)
        strategy = self.controller.select(key, available, checkpoint={"revision": state.revision, "state_key": key})
        self.pending = {"strategy": strategy, "state_key": key, "revision": state.revision,
                        "candidate_key": None, "host_reasons": (), "path": None}
        if strategy is None:
            self.no_more_strategies = True
            self.pending["host_reasons"] = ("strategy_exhausted",)
            candidate = Candidate(f"adaptive:{self.attempts}", "adaptive_stop", TARGET,
                                  "All available bounded strategies are exhausted.",
                                  (INPUT_ID,) + ((state.facts[-1].id,) if state.facts else ()))
            self.metadata[candidate.id] = dict(self.pending)
            return (candidate,)
        if self.last_strategy is not None and strategy != self.last_strategy:
            self.stats.strategy_switches += 1
        self.last_strategy = strategy
        counter = "rule" if strategy == "verified_rule" else strategy
        setattr(self.stats, counter + "_attempts", getattr(self.stats, counter + "_attempts") + 1)
        after, rule_id, action = None, "", "rewrite_polynomial"
        claim = {"state_revision": state.revision,
                 "state_fingerprint": _state_fingerprint(self.problem, state, current)}
        try:
            if strategy in ("whole_model", "local_model"):
                selected = local if strategy == "local_model" else None
                if selected is not None:
                    self.pending["path"] = selected[0]
                    self.pending["selected_expression"] = selected[1][:512]
                after = self._model_after(state, current, strategy, selected)
                if after is not None and selected is not None:
                    after = _replace_path(current, selected[0], after)
            elif strategy == "verified_rule":
                rule_id, action = rule.candidate.id, RULE_ACTION
                self.pending["rule_id"] = rule_id
                claim.update(rule_id=rule_id, rule_fingerprint=rule.fingerprint)
                self.pending["candidate_key"] = "rule:" + rule.fingerprint
            elif strategy == "primitive":
                self.pending["path"] = primitive[0]
                rewritten = _primitive(primitive[1], self.counts)
                if rewritten is None and is_expanded(current):
                    after, action = current, "certify_expansion"
                elif rewritten is None:
                    self.pending["host_reasons"] = ("no_primitive_rewrite",)
                else:
                    after = _replace_path(current, primitive[0], rewritten)
            else:
                self.rollbacks += 1
                after = rollback["expression"]
                self.pending["rollback_target"] = dict(rollback)
            if action != RULE_ACTION:
                if after is None:
                    return ()
                after_key = _semantic(after)
                self.pending["after_key"] = after_key
                candidate_key = ("rollback:" if strategy == "rollback" else "rewrite:") + after_key
                self.pending["candidate_key"] = candidate_key
                if strategy != "rollback" and (
                    self.controller.is_duplicate(key, candidate_key) or (key, after_key) in self.failed_edges
                    or (after_key in self.seen and not (after_key == key and is_expanded(after)))):
                    self.stats.duplicate_suppressions += 1
                    self.pending["host_reasons"] = ("duplicate_or_cycle_candidate",)
                    return ()
                claim.update(after=after, rule_id=rule_id)
            elif self.controller.is_duplicate(key, self.pending["candidate_key"]):
                self.stats.duplicate_suppressions += 1
                self.pending["host_reasons"] = ("duplicate_rule_candidate",)
                return ()
        except (ValueError, RecursionError):
            self.pending["host_reasons"] = ("proposal_resource_or_syntax_limit",)
            return ()
        candidate = Candidate(f"adaptive:{self.attempts}", action, TARGET,
                              json.dumps(claim, sort_keys=True),
                              (INPUT_ID,) + ((state.facts[-1].id,) if state.facts else ()))
        self.metadata[candidate.id] = dict(self.pending)
        return (candidate,)

    def observe(self, event):
        pending = self.pending
        if pending is None:
            return
        strategy, key = pending["strategy"], pending["state_key"]
        accepted = event.decision is Decision.ACCEPT
        reasons = pending["host_reasons"] or event.reasons
        kind = None if accepted else _diagnose(reasons)
        after = self._current(event.after)
        after_key = _semantic(after)
        progress = accepted and strategy != "rollback" and (after_key != key or event.residual_after.solved)
        if strategy is not None:
            self.controller.record(key, strategy, pending["candidate_key"], accepted=accepted,
                                   progress=progress, reasons=tuple(reasons), failure_kind=kind,
                                   revision=event.after.revision)
        if accepted:
            counter = "rule" if strategy == "verified_rule" else strategy
            setattr(self.stats, counter + "_accepts", getattr(self.stats, counter + "_accepts") + 1)
            if strategy == "rollback":
                target = pending["rollback_target"]
                index = next(i for i, ancestor in enumerate(self.ancestors) if ancestor["key"] == target["key"])
                child = self.ancestors[index + 1]
                edge = (target["key"], child["key"])
                self.failed_edges.add(edge)
                origin = self.edge_strategies.get(edge)
                if origin in ("whole_model", "local_model"):
                    self.disabled.add((target["key"], origin))
                self.ancestors = self.ancestors[:index + 1]
                self.priority = ["verified_rule", "primitive", "local_model", "whole_model", "rollback"]
            else:
                self.seen.add(after_key)
                self.edge_strategies[(key, after_key)] = strategy
                self.ancestors.append({"key": after_key, "expression": after, "revision": event.after.revision})
                self.priority = (["primitive", "verified_rule", "local_model", "whole_model", "rollback"]
                                 if strategy == "primitive" else
                                 ["verified_rule", "local_model", "primitive", "whole_model", "rollback"])
        else:
            self.stats.failures += 1
            if strategy in ("whole_model", "local_model"):
                self.stats.model_failures += 1
            self.stats.diagnostic_counts[kind.value] = self.stats.diagnostic_counts.get(kind.value, 0) + 1
            self.failures_at[key] = self.failures_at.get(key, 0) + 1
            if strategy in ("primitive", "verified_rule") and (
                    kind is FailureKind.RESOURCE or self.failures_at[key] >= 2):
                self.rollback_requested.add(key)
            if pending.get("path") is not None:
                self.failed_paths.add((key, strategy, pending["path"]))
            if pending.get("rule_id"):
                self.failed_rules.add((key, pending["rule_id"]))
            if kind is FailureKind.IDENTITY_MATH and strategy == "whole_model":
                self.priority = ["local_model", "verified_rule", "primitive", "whole_model", "rollback"]
            elif kind in (FailureKind.SCHEMA_BINDING, FailureKind.UNKNOWN_ERROR, FailureKind.UNAVAILABLE_RULE):
                self.priority = ["verified_rule", "primitive", "local_model", "whole_model", "rollback"]
            elif kind is FailureKind.RESOURCE:
                self.priority = ["local_model", "rollback", "verified_rule", "primitive", "whole_model"]
            else:
                self.priority = ["verified_rule", "primitive", "local_model", "whole_model", "rollback"]
            self.last_diagnostics.append({"kind": kind.value, "strategy": strategy,
                                          "reasons": [reason[:256] for reason in reasons[:3]]})
            self.last_diagnostics = self.last_diagnostics[-3:]
        self._audit({"step": event.step, "strategy": strategy, "decision": event.decision.value,
                     "state_key": key, "before_revision": event.before.revision,
                     "after_revision": event.after.revision, "candidate_key": pending["candidate_key"],
                     "failure_kind": None if kind is None else kind.value,
                     "reasons": [reason[:256] for reason in reasons[:3]],
                     "selected_path": None if pending.get("path") is None else "$" + "".join("." + s for s in pending["path"]),
                     "selected_expression": pending.get("selected_expression"),
                     "rollback_target": pending.get("rollback_target"),
                     "next_strategy_order": list(self.priority), "model_calls_in_task": self.model_calls})
        self.pending = None


class _AdaptiveVerifier:
    """Add cycle/ancestor guards to, never bypass, the existing domain verifier."""
    def __init__(self, proposer):
        self.proposer = proposer
        self.verifier = _StateBoundPolynomialVerifier(
            proposer.problem, proposer.records, proposer.counts,
            rule_lookup=proposer.library.get, allow_rule_execution=True)

    def verify(self, candidate, state, residual, evidence):
        metadata = self.proposer.metadata.get(candidate.id, {})
        if (candidate.action == "adaptive_stop" and self.proposer.no_more_strategies
                and metadata.get("host_reasons") == ("strategy_exhausted",)):
            return Verdict.for_candidate(candidate, state, Decision.INTERRUPT,
                                         evidence=evidence, reasons=("strategy_exhausted",))
        verdict = self.verifier.verify(candidate, state, residual, evidence)
        if verdict.decision is not Decision.ACCEPT:
            return verdict
        after = verdict.facts[0].value[1]
        key = _semantic(after)
        if metadata.get("strategy") == "rollback":
            target = metadata.get("rollback_target")
            allowed = target is not None and any(
                ancestor == target and ancestor["expression"] == after for ancestor in self.proposer.ancestors[:-1])
            reason = "invalid_rollback_checkpoint"
        else:
            current_key = _semantic(self.proposer._current(state))
            allowed = ((key not in self.proposer.seen and (current_key, key) not in self.proposer.failed_edges)
                       or (key == current_key and not state.facts and is_expanded(after)))
            reason = "adaptive_cycle_detected"
        if not allowed:
            return Verdict.for_candidate(candidate, state, Decision.REJECT, evidence=evidence, reasons=(reason,))
        return verdict


class _AdaptiveAgent(Agent):
    def __init__(self, *, adaptive_proposer, **kwargs):
        super().__init__(**kwargs)
        self.adaptive_proposer = adaptive_proposer

    def run(self, task):
        result = super().run(task)
        proposer, stats = self.adaptive_proposer, self.adaptive_proposer.stats
        stats.stop_reason = ("strategy_exhausted" if proposer.no_more_strategies
                             else result.run_result.stop_reason)
        stats.action_budget_exhausted |= (result.run_result.status != "solved"
                                          and proposer.attempts >= proposer.max_steps)
        proposer._audit({"event": "finished", "status": result.run_result.status,
                          "stop_reason": stats.stop_reason, "model_calls_in_task": proposer.model_calls,
                          "actions_in_task": proposer.attempts, "model_call_limit": proposer.max_model_calls,
                          "action_limit": proposer.max_steps})
        return result


def build_adaptive_learning_agent(
    problem: PolynomialProblem, library: KnowledgeLibrary, *,
    complete: Callable[[str], str] | None = None, counts: WorkCounts | None = None,
    stats: AdaptiveStats | None = None, max_model_calls: int = 8, max_steps: int = 64,
    max_no_progress: int | None = None, max_rollbacks: int = 2,
) -> LearningAgent:
    """Build an opt-in adaptive learner; legacy adapters remain unchanged.

    Whole/local model replies are exactly ``{"after": "..."}`` or ``null``.
    A local reply replaces only the selected AST path. Technical/schema/model
    failures are audited and may fall through to verified rules or primitives.
    Complete callbacks must enforce their own network timeout and token limits.
    ``complete=None`` is a symbolic-only control, with zero model calls.

    Every run has fresh strategy state and the stated per-task budgets; supplied
    counts/stats accumulate across runs. No branch or rollback resets a budget.
    The action cap cannot exceed the library's 64-step certificate limit.
    """
    if not isinstance(problem, PolynomialProblem) or not isinstance(library, KnowledgeLibrary):
        raise ValueError("expected PolynomialProblem and KnowledgeLibrary")
    if complete is not None and not callable(complete):
        raise ValueError("complete must be callable or None")
    for name, value in (("max_model_calls", max_model_calls), ("max_rollbacks", max_rollbacks)):
        if type(value) is not int or value < 0 or value > MAX_DERIVATION_STEPS:
            raise ValueError(f"{name} must be an integer from 0 through {MAX_DERIVATION_STEPS}")
    if type(max_steps) is not int or not 1 <= max_steps <= MAX_DERIVATION_STEPS:
        raise ValueError(f"max_steps must be an integer from 1 through {MAX_DERIVATION_STEPS}")
    max_no_progress = max_steps if max_no_progress is None else max_no_progress
    if type(max_no_progress) is not int or not 1 <= max_no_progress <= max_steps:
        raise ValueError("max_no_progress must be between 1 and max_steps")
    counts = WorkCounts() if counts is None else counts
    stats = AdaptiveStats() if stats is None else stats
    if not isinstance(counts, WorkCounts) or not isinstance(stats, AdaptiveStats):
        raise ValueError("counts and stats must be WorkCounts and AdaptiveStats")

    def factory(task, records):
        proposer = _AdaptiveProposer(problem, library, records, complete, counts, stats, task.id,
                                     max_model_calls, max_steps, max_rollbacks)
        return _AdaptiveAgent(
            adaptive_proposer=proposer, domain_factory=lambda _: PolynomialDomain(problem, counts),
            verifier_factory=lambda _: _AdaptiveVerifier(proposer),
            proposer_factory=lambda task, routes, evidence: (proposer,),
            tools=(PolynomialTool(problem),), max_steps=max_steps, max_no_progress=max_no_progress)

    return LearningAgent(library=library, agent_factory=factory, distill=distill_expansion)
