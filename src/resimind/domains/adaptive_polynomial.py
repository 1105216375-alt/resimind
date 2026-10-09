"""Bounded strategy adaptation around the existing exact polynomial verifier.

Models propose text only. Local edits, recalled rules, bounded distributive
rewrites, compaction and rollback all pass the same Engine verification gate.
Candidate generation never calls the independent identity checker for answers.
"""
from __future__ import annotations

import ast
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
import json
import time

from ..agent import Agent, Task
from ..core import Candidate, Decision, State, Verdict, content_digest
from ..knowledge import KnowledgeLibrary
from ..learning import LearningAgent
from ..integrations.lean import (
    ALLOWED_TACTICS, APPROVED_AXIOMS, SUPPORTED_LEAN_VERSION, LeanPolynomialBackend, LeanProofResult,
    polynomial_binding_digest,
)
from ..strategy import FailureKind, StrategyController
from .algebra import MAX_DERIVATION_STEPS, MAX_SOURCE_LENGTH, _tree as _bounded_tree
from .polynomial_learning import (
    DOMAIN, INPUT_ID, TARGET, RULE_ACTION, PolynomialDomain, PolynomialProblem,
    PolynomialTool, WorkCounts, _StateBoundPolynomialVerifier, _expansion_rules,
    _bounded_macro, _match, _needs_expansion, _normalized_expression, _primitive, _state_fingerprint,
    _text, _tree, _unique_object, distill_expansion, is_expanded,
)
from .polynomial_simplification import bounded_distribute, compact_expression
from .polynomial_scheduling import candidate_rank, cheap_progress, estimated_local_work, expression_cost

STRATEGIES = ("whole_model", "local_model", "proof_model", "verified_rule", "simplify", "distribute", "primitive", "lean_retry", "rollback")
MODEL_STRATEGIES = ("whole_model", "local_model", "proof_model")


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
    proof_model_attempts: int = 0
    proof_model_accepts: int = 0
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
    simplify_attempts: int = 0
    simplify_accepts: int = 0
    distribute_attempts: int = 0
    distribute_accepts: int = 0
    simplification_probes: int = 0
    distribution_probes: int = 0
    peak_expression_nodes: int = 0
    peak_expression_length: int = 0
    lean_checks: int = 0
    lean_verified: int = 0
    lean_unresolved: int = 0
    lean_errors: int = 0
    lean_retry_attempts: int = 0
    lean_retry_accepts: int = 0
    lean_feedback_prompts: int = 0
    lean_budget_exhausted: bool = False
    action_attempts: int = 0
    failures: int = 0
    model_budget_exhausted: bool = False
    action_budget_exhausted: bool = False
    diagnostic_counts: dict[str, int] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    events_omitted: int = 0
    rule_previews: int = 0
    rule_preview_pattern_attempts: int = 0
    scheduling_preview_nodes: int = 0
    scheduling_preview_elapsed_seconds: float = 0.0
    scheduling_decisions: list[dict] = field(default_factory=list)
    scheduling_decisions_omitted: int = 0
    rule_usage: list[dict] = field(default_factory=list)
    rule_usage_omitted: int = 0
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
    if "lean_proof_unresolved" in reasons:
        return FailureKind.PROOF_INCOMPLETE
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
                 max_model_calls, max_steps, max_rollbacks, control_expression_growth,
                 lean_backend, max_lean_checks, cost_aware_scheduling=True,
                 max_local_work=4096, max_rule_previews=16):
        self.problem, self.library, self.records = problem, library, _expansion_rules(records)[:16]
        self.complete, self.counts, self.stats, self.task_id = complete, counts, stats, task_id
        self.max_model_calls, self.max_steps, self.max_rollbacks = max_model_calls, max_steps, max_rollbacks
        self.control_expression_growth = control_expression_growth
        self.cost_aware_scheduling = cost_aware_scheduling
        self.max_local_work, self.max_rule_previews = max_local_work, max_rule_previews
        self.scheduling_previews = {}
        self.scheduled_rules = {}
        self.scheduling_decision = None
        self.rule_strategies = {"verified_rule:" + record.fingerprint: record for record in self.records}
        self.growth_options = {}
        self.lean_backend, self.max_lean_checks = lean_backend, max_lean_checks
        self.lean_checks = 0
        self.lean_feedback = None
        self.pending_proof = None
        self.lean_stop = None
        controller_strategies = STRATEGIES + (tuple(self.rule_strategies) if cost_aware_scheduling else ())
        self.controller = StrategyController(controller_strategies, max_attempts=max_steps,
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
        self._track_size(problem.expression)

    def _track_size(self, expression):
        # Metrics must not impose a stricter algebra grammar than the verifier
        # (for example, a high syntactic degree that disappears after zeroing).
        nodes = sum(1 for _ in ast.walk(_bounded_tree(expression)))
        length = len(expression)
        self.stats.peak_expression_nodes = max(self.stats.peak_expression_nodes, nodes)
        self.stats.peak_expression_length = max(self.stats.peak_expression_length, length)
        return nodes, length

    def _growth_candidates(self, expression, key):
        if not self.control_expression_growth:
            return None, None
        if key not in self.growth_options:
            self.stats.simplification_probes += 1
            try:
                compact = compact_expression(expression, self.problem.variables)
            except ValueError:
                compact = None
            distributed = None
            if compact is None or self.cost_aware_scheduling:
                self.stats.distribution_probes += 1
                try:
                    distributed = bounded_distribute(expression, self.problem.variables)
                except ValueError:
                    distributed = None
            self.growth_options[key] = (compact, distributed)
        return self.growth_options[key]

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

    def _preview_entry(self, current, before, strategy, after, *, record=None, error=None):
        entry = {"strategy": strategy, "rule_id": None if record is None else record.candidate.id,
                 "rule_fingerprint": None if record is None else record.fingerprint,
                 "after_fingerprint": None, "after": None, "rank": None,
                 "estimated_local_work": None, "eligible": False,
                 "reason": error or "no_candidate", "_after": after}
        if after is None:
            return entry
        try:
            cost = expression_cost(after)
            entry.update(after=cost, after_fingerprint=_semantic(after),
                         rank=list(candidate_rank(before, cost, strategy)),
                         estimated_local_work=estimated_local_work(cost), reason="preview_only")
            self.stats.scheduling_preview_nodes += cost["ast_nodes"]
        except (ValueError, RecursionError):
            entry.update(reason="preview_resource_limit", _after=None)
        return entry

    def _cost_previews(self, state, current, key, compact, distributed):
        fingerprint = _state_fingerprint(self.problem, state, current)
        if fingerprint in self.scheduling_previews:
            return deepcopy(self.scheduling_previews[fingerprint]), True
        before = expression_cost(current)
        self.stats.scheduling_preview_nodes += before["ast_nodes"]
        entries = []
        for initial in self.records[:self.max_rule_previews]:
            self.stats.rule_previews += 1
            start = time.perf_counter()
            prior_patterns = self.counts.pattern_attempts
            after, error = None, None
            try:
                current_record = self.library.get(initial.candidate.id)
                if current_record != initial or current_record.status != "verified":
                    error = "rule_unavailable_or_changed"
                else:
                    after = _bounded_macro(current, current_record, self.counts)
                    if after is None:
                        error = "no_matching_rule"
            except KeyError:
                error = "rule_unavailable_or_changed"
            except (ValueError, RecursionError):
                error = "preview_resource_limit"
            work = self.counts.pattern_attempts - prior_patterns
            self.stats.rule_preview_pattern_attempts += work
            entry = self._preview_entry(current, before, "verified_rule", after, record=initial, error=error)
            entry.update(preview_pattern_attempts=work, preview_elapsed_seconds=time.perf_counter() - start)
            entries.append(entry)
        entries.extend((self._preview_entry(current, before, "simplify", compact),
                        self._preview_entry(current, before, "distribute", distributed)))
        # At most 64 action states, each with at most 18 bounded previews.
        self.scheduling_previews[fingerprint] = deepcopy(entries)
        return entries, False

    def _filter_preview(self, entry, before, key):
        if entry["_after"] is None:
            return
        after_key = entry["after_fingerprint"]
        strategy, rule_id = entry["strategy"], entry["rule_id"]
        controller_strategy = "verified_rule:" + entry["rule_fingerprint"] if rule_id is not None else strategy
        if not self.controller.is_eligible(key, controller_strategy):
            entry["reason"] = "strategy_budget_exhausted"
            return
        if rule_id is not None:
            if (key, rule_id) in self.failed_rules:
                entry["reason"] = "previous_rule_failure"
                return
            initial = next((item for item in self.records if item.candidate.id == rule_id), None)
            try:
                current = self.library.get(rule_id)
            except KeyError:
                current = None
            if current is None or current != initial or current.status != "verified":
                entry["reason"] = "rule_unavailable_or_changed"
                return
            candidate_key = "rule:" + entry["rule_fingerprint"]
        else:
            candidate_key = "rewrite:" + after_key
            if self.lean_backend is not None:
                candidate_key += ":lean:grind"
        if (self.controller.is_duplicate(key, candidate_key) or (key, after_key) in self.failed_edges
                or (after_key in self.seen and not (after_key == key and entry["after"]["expanded"]))):
            entry["reason"] = "duplicate_or_cycle_candidate"
            return
        if entry["estimated_local_work"] > self.max_local_work:
            entry["reason"] = "estimated_local_work_exceeds_budget"
            return
        if not cheap_progress(before, entry["after"]) and strategy != "primitive":
            entry["reason"] = "no_estimated_progress"
            return
        entry.update(eligible=True, reason="bounded_local_progress")

    def _available_cost_aware(self, state, current, key):
        start = time.perf_counter()
        previous_nodes = self.stats.scheduling_preview_nodes
        before = expression_cost(current)
        fingerprint = _state_fingerprint(self.problem, state, current)
        paths = _paths(current)
        local = next((item for item in paths if (key, "local_model", item[0]) not in self.failed_paths), None)
        primitive = next((item for item in paths if (key, "primitive", item[0]) not in self.failed_paths), None)
        if primitive is None and not paths:
            primitive = ((), current)
        compact, distributed = self._growth_candidates(current, key)
        entries, cache_hit = self._cost_previews(state, current, key, compact, distributed)
        for entry in entries:
            self._filter_preview(entry, before, key)
        cheap = sorted((item for item in entries if item["eligible"]), key=lambda item: tuple(item["rank"]))
        self.scheduled_rules = {}
        local_names = []
        for entry in cheap:
            if entry["strategy"] == "verified_rule":
                name = "verified_rule:" + entry["rule_fingerprint"]
                self.scheduled_rules[name] = self.rule_strategies[name]
            else:
                name = entry["strategy"]
            local_names.append(name)
        # Primitive rewrites remain a bounded fallback, not additional eager
        # work when a compact/distribute/rule preview already makes progress.
        self.scheduled_primitive = None
        if not cheap and primitive is not None:
            try:
                rewritten = _primitive(primitive[1], self.counts)
                after = (current if rewritten is None and is_expanded(current) else
                         None if rewritten is None else _replace_path(current, primitive[0], rewritten))
                primitive_entry = self._preview_entry(current, before, "primitive", after)
                self._filter_preview(primitive_entry, before, key)
                entries.append(primitive_entry)
                if primitive_entry["eligible"]:
                    self.scheduled_primitive = (after, "certify_expansion" if rewritten is None else "rewrite_polynomial")
                    if cheap_progress(before, primitive_entry["after"]):
                        cheap.append(primitive_entry)
                        local_names.append("primitive")
            except (ValueError, RecursionError):
                pass
        model_available = self.complete is not None and self.model_calls < self.max_model_calls
        models = [name for name in self.priority if name in ("whole_model", "local_model")
                  and (key, name) not in self.disabled and (name != "local_model" or local is not None)
                  and self.controller.is_eligible(key, name)]
        model_enabled = model_available and not cheap and bool(models)
        available = list(local_names)
        reason = "complete_local_candidate" if cheap and cheap[0]["after"]["expanded"] else "lowest_estimated_remaining_work"
        if model_enabled:
            available.extend(models)
            reason = ("local_work_budget_exceeded" if any(item["reason"] == "estimated_local_work_exceeds_budget"
                                                        for item in entries) else "no_cheap_local_progress")
        if not cheap and self.scheduled_primitive is not None:
            available.append("primitive")
            if not model_enabled:
                reason = "bounded_primitive_fallback"
        if not available:
            reason = "no_available_bounded_candidate"
        pending_proof = self.pending_proof is not None and self.pending_proof["state_key"] == key
        proof_names = []
        if pending_proof:
            if (model_available and not self.pending_proof.get("model_feedback_used", False)
                    and self.controller.is_eligible(key, "proof_model")):
                proof_names.append("proof_model")
                model_enabled = True
            if self.pending_proof["tactic"] != "grind" and self.controller.is_eligible(key, "lean_retry"):
                proof_names.append("lean_retry")
        if proof_names:
            available = proof_names + [name for name in available if name not in proof_names]
            reason = "proof_feedback_requires_correction"
        rollback = None
        if (self.rollbacks < self.max_rollbacks and len(self.ancestors) > 1
                and key in self.rollback_requested and not cheap and not proof_names
                and self.controller.is_eligible(key, "rollback")):
            rollback = self.ancestors[-2]
            available = ["rollback"] + available
            reason = "verified_checkpoint_recovery"
        if self.lean_stop:
            available, reason = [], self.lean_stop
        elapsed = time.perf_counter() - start
        self.stats.scheduling_preview_elapsed_seconds += elapsed
        self.scheduling_decision = {
            "task_id": self.task_id, "step": self.attempts, "state_revision": state.revision,
            "state_fingerprint": fingerprint, "state_key": key, "mode": "cost_aware",
            "before": before, "candidates": [{name: value for name, value in entry.items() if not name.startswith("_")}
                                               for entry in entries],
            "selected_strategy": None, "selected_rule_id": None, "selection_reason": reason,
            "max_local_work": self.max_local_work, "model_enabled": model_enabled,
            "preview_cache_hit": cache_hit, "preview_nodes": self.stats.scheduling_preview_nodes - previous_nodes,
            "preview_elapsed_seconds": elapsed,
        }
        return tuple(available), local, primitive, None, rollback, compact, distributed

    def _available(self, state, current, key):
        if self.cost_aware_scheduling:
            return self._available_cost_aware(state, current, key)
        self.scheduling_decision = {
            "task_id": self.task_id, "step": self.attempts, "state_revision": state.revision,
            "state_fingerprint": _state_fingerprint(self.problem, state, current), "state_key": key,
            "mode": "legacy", "before": expression_cost(current), "candidates": [],
            "selected_strategy": None, "selected_rule_id": None, "selection_reason": "legacy_strategy_order",
            "max_local_work": self.max_local_work, "model_enabled": self.complete is not None,
            "preview_cache_hit": False, "preview_nodes": 0, "preview_elapsed_seconds": 0.0,
        }
        return self._available_legacy(state, current, key)

    def _available_legacy(self, state, current, key):
        if self.lean_stop:
            return (), None, None, None, None, None, None
        compact, distributed = self._growth_candidates(current, key)
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
            "simplify": compact is not None,
            "distribute": distributed is not None,
            "primitive": primitive is not None,
            "rollback": rollback is not None,
            "proof_model": (model_available and self.pending_proof is not None
                            and self.pending_proof["state_key"] == key
                            and not self.pending_proof.get("model_feedback_used", False)),
            "lean_retry": (self.pending_proof is not None and self.pending_proof["state_key"] == key
                           and self.pending_proof["tactic"] != "grind"),
        }
        priority = list(self.priority)
        if choices["lean_retry"] or choices["proof_model"]:
            # Give the model one chance to use real proof feedback, then try
            # the fixed stronger tactic within the same action/check budgets.
            priority = ["proof_model", "lean_retry"] + [
                name for name in priority if name not in ("proof_model", "lean_retry")]
        if rollback is not None:
            priority = ["rollback"] + [name for name in priority if name != "rollback"]
        return tuple(name for name in priority if choices[name]), local, primitive, rule, rollback, compact, distributed

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
        if self.lean_backend is not None:
            prompt["proof_tactics"] = list(ALLOWED_TACTICS)
            prompt["response_schema"]["properties"]["tactic"] = {
                "type": "string", "enum": list(ALLOWED_TACTICS)}
            prompt["instruction"] += (
                " You may additionally return tactic: rfl or grind; grind is the default. "
                "Lean must prove each full-expression equality before it can commit. "
                "When proof_feedback is present, inspect its actual goal and diagnostics; "
                "you may retry its proposed_after with a different tactic or propose another rewrite.")
            if self.lean_feedback is not None:
                prompt["proof_feedback"] = deepcopy(self.lean_feedback)
                self.stats.lean_feedback_prompts += 1
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
            fields = ({"after"}, {"after", "tactic"}) if self.lean_backend is not None else ({"after"},)
            if type(result) is not dict or set(result) not in fields or type(result.get("after")) is not str:
                raise ValueError("invalid local response schema")
            tactic = result.get("tactic", "grind")
            if type(tactic) is not str or tactic not in ALLOWED_TACTICS:
                raise ValueError("invalid proof tactic")
            self.pending["lean_tactic"] = tactic
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
        started = time.perf_counter()
        current = self._current(state)
        key = _semantic(current)
        self.attempts += 1
        self.stats.action_attempts += 1
        available, local, primitive, rule, rollback, compact, distributed = self._available(state, current, key)
        controller_strategy = self.controller.select(key, available, checkpoint={"revision": state.revision, "state_key": key})
        strategy = controller_strategy
        if controller_strategy in self.scheduled_rules:
            strategy, rule = "verified_rule", self.scheduled_rules[controller_strategy]
        decision = deepcopy(self.scheduling_decision)
        decision["selected_strategy"] = strategy
        decision["selected_rule_id"] = rule.candidate.id if strategy == "verified_rule" and rule is not None else None
        if strategy is None:
            decision["selection_reason"] = self.lean_stop or "strategy_budget_exhausted"
        if len(self.stats.scheduling_decisions) < 512:
            self.stats.scheduling_decisions.append(decision)
        else:
            self.stats.scheduling_decisions_omitted += 1
        self.pending = {"strategy": strategy, "state_key": key, "revision": state.revision,
                        "controller_strategy": controller_strategy, "started": started,
                        "scheduling_decision": decision,
                        "candidate_key": None, "host_reasons": (), "path": None, "lean_tactic": "grind"}
        if strategy is None:
            self.no_more_strategies = True
            self.pending["host_reasons"] = (self.lean_stop or "strategy_exhausted",)
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
            if strategy in MODEL_STRATEGIES:
                if strategy == "proof_model":
                    self.pending_proof["model_feedback_used"] = True
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
                self.pending["rule_fingerprint"] = rule.fingerprint
                claim.update(rule_id=rule_id, rule_fingerprint=rule.fingerprint)
                self.pending["candidate_key"] = "rule:" + rule.fingerprint
            elif strategy == "primitive":
                self.pending["path"] = primitive[0]
                if self.cost_aware_scheduling and self.scheduled_primitive is not None:
                    after, action = self.scheduled_primitive
                else:
                    rewritten = _primitive(primitive[1], self.counts)
                    if rewritten is None and is_expanded(current):
                        after, action = current, "certify_expansion"
                    elif rewritten is None:
                        self.pending["host_reasons"] = ("no_primitive_rewrite",)
                    else:
                        after = _replace_path(current, primitive[0], rewritten)
            elif strategy in ("simplify", "distribute"):
                after = compact if strategy == "simplify" else distributed
            elif strategy == "lean_retry":
                after = self.pending_proof["after"]
                action = self.pending_proof["action"]
                claim = json.loads(self.pending_proof["claim"])
                rule_id = claim["rule_id"]
                self.pending["rule_id"] = rule_id
                self.pending["rule_fingerprint"] = claim.get("rule_fingerprint")
                self.pending["candidate_key"] = "lean:grind:" + _semantic(after)
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
                if self.lean_backend is not None:
                    candidate_key += ":lean:" + self.pending["lean_tactic"]
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

    def check_lean(self, candidate, state, before, after):
        """Return a separate proof gate result; feedback is never an accepted fact."""
        if self.lean_backend is None:
            return None
        metadata = self.metadata.get(candidate.id, {})
        tactic = metadata.get("lean_tactic", "grind")
        if self.lean_checks >= self.max_lean_checks:
            self.stats.lean_budget_exhausted = True
            self.lean_stop = "lean_check_budget_exhausted"
            return ("lean_check_budget_exhausted",)
        self.lean_checks += 1
        self.stats.lean_checks += 1
        try:
            proof = self.lean_backend.check(before, after, self.problem.variables, tactic=tactic)
        except Exception as exc:
            self.stats.lean_errors += 1
            return ("lean_backend_error", type(exc).__name__)
        bound = polynomial_binding_digest(before, after, self.problem.variables)
        if (type(proof) is not LeanProofResult or proof.binding_digest != bound or proof.tactic != tactic
                or proof.status not in ("verified", "unresolved", "unavailable", "error")):
            self.stats.lean_errors += 1
            return ("lean_proof_binding_mismatch",)
        def strings(value, limit):
            return (type(value) is tuple and len(value) <= limit
                    and all(type(item) is str and len(item) <= 1024 * 1024 for item in value))

        def sha256(value):
            return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

        if (type(proof.target) is not str or type(proof.actual_target) is not str
                or len(proof.actual_target) > 1024 * 1024
                or not strings(proof.goals, 128) or not strings(proof.diagnostics, 128)
                or not strings(proof.axioms, 16) or not sha256(proof.source_digest)
                or proof.variable_mapping != tuple((name, f"v{i}") for i, name in enumerate(self.problem.variables))
                or (proof.proof_digest is not None and not sha256(proof.proof_digest))
                or (proof.lean_version is not None and type(proof.lean_version) is not str)):
            self.stats.lean_errors += 1
            return ("lean_proof_invalid_certificate",)
        feedback = {
            "status": proof.status, "tactic": tactic, "state_revision": state.revision,
            "state_fingerprint": _state_fingerprint(self.problem, state, before),
            "binding_digest": bound, "source_digest": proof.source_digest,
            "proof_digest": proof.proof_digest, "lean_version": proof.lean_version,
            "axioms": list(proof.axioms), "variable_mapping": list(proof.variable_mapping),
            "actual_target": proof.actual_target[:4096],
            "goals": [goal[:2048] for goal in proof.goals[:8]],
            "diagnostics": [item[:1024] for item in proof.diagnostics[:4]],
            "proposed_after": after,
            "feedback_truncated": (len(proof.actual_target) > 4096 or len(proof.goals) > 8
                                   or any(len(g) > 2048 for g in proof.goals)
                                   or len(proof.diagnostics) > 4
                                   or any(len(g) > 1024 for g in proof.diagnostics)),
        }
        self.pending["lean_proof"] = feedback
        if (proof.status == "verified" and sha256(proof.proof_digest)
                and proof.actual_target and not proof.goals and not proof.diagnostics
                and proof.lean_version == SUPPORTED_LEAN_VERSION
                and set(proof.axioms) <= APPROVED_AXIOMS):
            self.stats.lean_verified += 1
            return ()
        self.lean_feedback = feedback
        if proof.status == "unresolved":
            self.stats.lean_unresolved += 1
            self.pending_proof = {"state_key": _semantic(before), "after": after,
                                  "action": candidate.action, "claim": candidate.claim, "tactic": tactic}
        else:
            self.stats.lean_errors += 1
            self.pending_proof = None
            if proof.status == "unavailable":
                self.lean_stop = "lean_unavailable"
        if self.lean_checks >= self.max_lean_checks:
            self.stats.lean_budget_exhausted = True
            self.lean_stop = "lean_check_budget_exhausted"
        return ("lean_proof_" + (proof.status if proof.status != "verified" else "invalid_certificate"),
                *(item[:256] for item in proof.diagnostics[:2]))

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
            self.controller.record(key, pending.get("controller_strategy", strategy), pending["candidate_key"], accepted=accepted,
                                   progress=progress, reasons=tuple(reasons), failure_kind=kind,
                                   revision=event.after.revision)
        if accepted:
            self.pending_proof = None
            self.lean_feedback = None
            counter = "rule" if strategy == "verified_rule" else strategy
            setattr(self.stats, counter + "_accepts", getattr(self.stats, counter + "_accepts") + 1)
            if strategy == "rollback":
                target = pending["rollback_target"]
                index = next(i for i, ancestor in enumerate(self.ancestors) if ancestor["key"] == target["key"])
                child = self.ancestors[index + 1]
                edge = (target["key"], child["key"])
                self.failed_edges.add(edge)
                origin = self.edge_strategies.get(edge)
                if origin in MODEL_STRATEGIES:
                    self.disabled.add((target["key"], origin))
                self.ancestors = self.ancestors[:index + 1]
                self.priority = ["verified_rule", "simplify", "distribute", "primitive", "local_model", "whole_model", "rollback"]
            else:
                self.seen.add(after_key)
                self.edge_strategies[(key, after_key)] = strategy
                self.ancestors.append({"key": after_key, "expression": after, "revision": event.after.revision})
                self.priority = (["simplify", "distribute", "primitive", "verified_rule", "local_model", "whole_model", "rollback"]
                                 if strategy in ("primitive", "simplify", "distribute") else
                                 ["verified_rule", "local_model", "simplify", "distribute", "primitive", "whole_model", "rollback"])
        else:
            self.stats.failures += 1
            if strategy in MODEL_STRATEGIES:
                self.stats.model_failures += 1
            self.stats.diagnostic_counts[kind.value] = self.stats.diagnostic_counts.get(kind.value, 0) + 1
            self.failures_at[key] = self.failures_at.get(key, 0) + 1
            if strategy in ("primitive", "verified_rule", "simplify", "distribute") and (
                    kind is FailureKind.RESOURCE or self.failures_at[key] >= 2):
                self.rollback_requested.add(key)
            if pending.get("path") is not None:
                self.failed_paths.add((key, strategy, pending["path"]))
            if pending.get("rule_id"):
                self.failed_rules.add((key, pending["rule_id"]))
            if kind is FailureKind.IDENTITY_MATH and strategy == "whole_model":
                self.priority = ["local_model", "verified_rule", "simplify", "distribute", "primitive", "whole_model", "rollback"]
            elif kind in (FailureKind.SCHEMA_BINDING, FailureKind.UNKNOWN_ERROR, FailureKind.UNAVAILABLE_RULE):
                self.priority = ["verified_rule", "simplify", "distribute", "primitive", "local_model", "whole_model", "rollback"]
            elif kind is FailureKind.RESOURCE:
                self.priority = ["simplify", "local_model", "rollback", "verified_rule", "distribute", "primitive", "whole_model"]
            else:
                self.priority = ["verified_rule", "simplify", "distribute", "primitive", "local_model", "whole_model", "rollback"]
            self.last_diagnostics.append({"kind": kind.value, "strategy": strategy,
                                          "reasons": [reason[:256] for reason in reasons[:3]]})
            self.last_diagnostics = self.last_diagnostics[-3:]
            if reasons and reasons[0] == "lean_proof_unresolved":
                self.priority = ["whole_model", "lean_retry", "local_model", "verified_rule",
                                 "simplify", "distribute", "primitive", "rollback"]
        nodes, length = self._track_size(after)
        if pending.get("rule_id"):
            observed = {
                "task_id": self.task_id, "step": event.step,
                "state_fingerprint": _state_fingerprint(self.problem, event.before, self._current(event.before)),
                "rule_id": pending["rule_id"], "rule_fingerprint": pending.get("rule_fingerprint"),
                "scenario": {"variables": list(self.problem.variables),
                             "initial_expression_fingerprint": _semantic(self.problem.expression)},
                "before": expression_cost(self._current(event.before)), "after": expression_cost(after),
                "goals_before": list(event.residual_before.pending),
                "goals_after": list(event.residual_after.pending),
                "accepted": accepted, "progress": progress, "decision": event.decision.value,
                "reasons": [reason[:256] for reason in reasons[:3]],
                "elapsed_seconds": time.perf_counter() - pending["started"],
                "preview_elapsed_seconds": pending["scheduling_decision"]["preview_elapsed_seconds"],
            }
            if len(self.stats.rule_usage) < 512:
                self.stats.rule_usage.append(observed)
            else:
                self.stats.rule_usage_omitted += 1
        self._audit({"step": event.step, "strategy": strategy, "decision": event.decision.value,
                     "expression_nodes": nodes, "expression_length": length,
                     "state_key": key, "before_revision": event.before.revision,
                     "after_revision": event.after.revision, "candidate_key": pending["candidate_key"],
                     "failure_kind": None if kind is None else kind.value,
                     "reasons": [reason[:256] for reason in reasons[:3]],
                     "selected_path": None if pending.get("path") is None else "$" + "".join("." + s for s in pending["path"]),
                     "selected_expression": pending.get("selected_expression"),
                     "rollback_target": pending.get("rollback_target"),
                     "lean_proof": pending.get("lean_proof"),
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
                and metadata.get("host_reasons") == (self.proposer.lean_stop or "strategy_exhausted",)):
            return Verdict.for_candidate(candidate, state, Decision.INTERRUPT,
                                         evidence=evidence, reasons=metadata["host_reasons"])
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
        lean_reasons = self.proposer.check_lean(candidate, state, verdict.facts[0].value[0], after)
        if lean_reasons:
            return Verdict.for_candidate(candidate, state, Decision.DEFER, evidence=evidence, reasons=lean_reasons)
        if lean_reasons == ():
            # An external proof check creates a revocation window. Recheck
            # the live rule immediately before authorizing its application.
            rule_id = verdict.facts[0].value[2]
            if rule_id:
                initial = self.verifier.records.get(rule_id)
                try:
                    current = self.proposer.library.get(rule_id)
                except KeyError:
                    current = None
                if current is None or current != initial or current.status != "verified":
                    return Verdict.for_candidate(candidate, state, Decision.REJECT, evidence=evidence,
                                                 reasons=("rule_unavailable_or_changed",))
            return Verdict.for_candidate(candidate, state, Decision.ACCEPT, evidence=evidence,
                                         reasons=verdict.reasons + ("lean_kernel_verified",), facts=verdict.facts)
        return verdict


class _AdaptiveAgent(Agent):
    def __init__(self, *, adaptive_proposer, **kwargs):
        super().__init__(**kwargs)
        self.adaptive_proposer = adaptive_proposer

    def run(self, task):
        result = super().run(task)
        proposer, stats = self.adaptive_proposer, self.adaptive_proposer.stats
        stats.stop_reason = ((proposer.lean_stop or "strategy_exhausted") if proposer.no_more_strategies
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
    control_expression_growth: bool = True,
    lean_backend: LeanPolynomialBackend | None = None, max_lean_checks: int = 64,
    cost_aware_scheduling: bool = True, max_local_work: int = 4096,
    max_rule_previews: int = 16,
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
    Local compaction and bounded distributive batches are enabled by default;
    disable ``control_expression_growth`` to retain the v0.8 primitive strategy.
    With ``lean_backend``, every accepted rewrite also needs a real Lean proof.
    Model replies may include ``tactic`` (``rfl`` or ``grind``); Lean goals and
    diagnostics feed subsequent prompts and bounded proof retries. Failed or
    unavailable Lean checks never silently fall back to exact-only acceptance.
    The default scheduler compares bounded local and recalled-rule previews
    before enabling a model. ``max_local_work`` caps a dimensionless syntax
    estimate, not actual runtime; zero forces escalation when a model exists.
    ``cost_aware_scheduling=False`` retains the previous strategy order, with
    ``control_expression_growth`` remaining a separate switch. Preview and
    rule-use observations are bounded JSON data, never verification evidence.
    """
    if not isinstance(problem, PolynomialProblem) or not isinstance(library, KnowledgeLibrary):
        raise ValueError("expected PolynomialProblem and KnowledgeLibrary")
    if complete is not None and not callable(complete):
        raise ValueError("complete must be callable or None")
    if type(control_expression_growth) is not bool:
        raise ValueError("control_expression_growth must be a bool")
    if type(cost_aware_scheduling) is not bool:
        raise ValueError("cost_aware_scheduling must be a bool")
    if type(max_local_work) is not int or not 0 <= max_local_work <= 100_000:
        raise ValueError("max_local_work must be an integer from 0 through 100000")
    if type(max_rule_previews) is not int or not 1 <= max_rule_previews <= 16:
        raise ValueError("max_rule_previews must be an integer from 1 through 16")
    if lean_backend is not None and not isinstance(lean_backend, LeanPolynomialBackend):
        raise ValueError("lean_backend must be a LeanPolynomialBackend or None")
    if type(max_lean_checks) is not int or not 1 <= max_lean_checks <= MAX_DERIVATION_STEPS:
        raise ValueError("max_lean_checks must be between 1 and 64")
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
                                     max_model_calls, max_steps, max_rollbacks, control_expression_growth,
                                     lean_backend, max_lean_checks, cost_aware_scheduling,
                                     max_local_work, max_rule_previews)
        return _AdaptiveAgent(
            adaptive_proposer=proposer, domain_factory=lambda _: PolynomialDomain(problem, counts),
            verifier_factory=lambda _: _AdaptiveVerifier(proposer),
            proposer_factory=lambda task, routes, evidence: (proposer,),
            tools=(PolynomialTool(problem),), max_steps=max_steps, max_no_progress=max_no_progress)

    return LearningAgent(library=library, agent_factory=factory, distill=distill_expansion)
