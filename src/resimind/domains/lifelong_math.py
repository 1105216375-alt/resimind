"""Opt-in verified tool routing and bounded learning across math episodes.

Knowledge is updated only with learn=True. Frozen evaluation snapshots reload
rules through the verifier and never learn from evaluation tasks. Sparse exact
algebra is a tool, not a neural theorem prover or a claim of frontier research.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
import json
import os
from pathlib import Path
import tempfile
import time

from ..core import content_digest
from ..knowledge import KnowledgeLibrary
from ..policy import StrategyObservation, StrategyPolicy
from .algebra import AlgebraVerifier, validate_expression, verify_identity
from .compact_algebra import CompactBudget, CompactRun, run_compact
from .polynomial_learning import PolynomialProblem
from .rule_abstraction import VerifiedExample
from .schema_learning import discover_schemas
from .search_learning import SearchBudget, SearchLearningResult, expression_context, run_search_learning


@dataclass
class MathEpisode:
    status: str = 'limited'
    route: str | None = None
    expression: str | None = None
    compact: CompactRun | None = None
    search: SearchLearningResult | None = None
    attempts: list[dict] = field(default_factory=list)
    learned_ids: list[str] = field(default_factory=list)
    learning_checks: int = 0
    learning_rejections: int = 0
    learning_error: str | None = None
    search_actions: int = 0
    model_calls: int = 0
    lean_checks: int = 0
    seconds: float = 0.0


class LifelongMathAgent:
    """Finite verified memory with explicit train/freeze/save/load boundaries."""
    MAX_EXAMPLES = 32
    MAX_OBSERVATIONS = 4096
    MAX_RULES = 256

    def __init__(self, *, library=None, policy=None, frozen=False):
        if library is not None and type(library) is not KnowledgeLibrary:
            raise ValueError('expected KnowledgeLibrary')
        if policy is not None and (type(policy) is not StrategyPolicy or not policy.frozen):
            raise ValueError('route policy must be frozen')
        if type(frozen) is not bool: raise ValueError('frozen must be bool')
        self.library = library if library is not None else KnowledgeLibrary({'algebra': AlgebraVerifier()})
        if self.library.record_count > self.MAX_RULES: raise ValueError('memory record bound exceeded')
        self.policy = policy
        self.frozen = frozen
        self._examples = []
        self._observations = []

    def solve(self, problem: PolynomialProblem, *, learn=False, source_id=None,
              routes=('recall', 'compact', 'search'), budget=SearchBudget(), compact_budget=CompactBudget(),
              complete=None, lean_backend=None, use_policy=True) -> MathEpisode:
        if type(problem) is not PolynomialProblem: raise ValueError('expected PolynomialProblem')
        if type(learn) is not bool or type(use_policy) is not bool: raise ValueError('flags must be bool')
        if learn and self.frozen: raise RuntimeError('evaluation snapshot is frozen')
        if type(budget) is not SearchBudget or type(compact_budget) is not CompactBudget:
            raise ValueError('expected explicit bounded budget objects')
        if (type(routes) is not tuple or not routes or len(set(routes)) != len(routes)
                or not set(routes) <= {'recall', 'compact', 'search'}):
            raise ValueError('invalid routes')
        if source_id is not None and (type(source_id) is not str or not source_id.strip()):
            raise ValueError('source_id must be nonempty')
        started = time.perf_counter(); episode = MathEpisode()
        context = 'route:' + expression_context(problem.expression)
        order = self.policy.rank(context, routes) if self.policy is not None and use_policy else routes
        observations = []
        for route in order:
            if route == 'compact' and lean_backend is not None:
                episode.attempts.append({'route': route, 'status': 'unavailable',
                    'diagnosis': 'formal_proof_required', 'seconds': 0.0})
                continue
            route_started = time.perf_counter()
            solved = False; reason = 'budget_exhausted'
            if route == 'compact':
                result = run_compact(problem.expression, problem.variables, budget=compact_budget)
                episode.compact = result
                solved = result.status == 'solved'; reason = result.verification.reason
                if solved:
                    try: episode.expression = result.certificate.render()
                    except ValueError: pass  # The verified sparse terms remain the authoritative result.
            else:
                remaining = budget.actions - episode.search_actions
                if remaining < 1:
                    episode.attempts.append({'route': route, 'status': 'limited',
                        'diagnosis': reason, 'seconds': 0.0})
                    continue
                local_budget = SearchBudget(actions=min(4, remaining) if route == 'recall' else remaining,
                    frontier=budget.frontier, depth=budget.depth,
                    model_calls=0 if route == 'recall' else budget.model_calls-episode.model_calls,
                    lean_checks=budget.lean_checks-episode.lean_checks)
                result = run_search_learning(problem, library=self.library, budget=local_budget,
                    complete=complete, lean_backend=lean_backend, indexed_retrieval=True,
                    strategies=('rule',) if route == 'recall' else ('primitive', 'distribute', 'compact', 'rule', 'model'))
                episode.search = result
                episode.search_actions += result.actions
                episode.model_calls += result.model_calls
                episode.lean_checks += result.lean_checks
                solved = result.status == 'solved'; reason = result.stop_reason
                if solved: episode.expression = result.expression
            elapsed = time.perf_counter() - route_started
            observations.append(StrategyObservation(context, route, solved, elapsed * 1000))
            episode.attempts.append({'route': route, 'status': 'solved' if solved else 'limited',
                                     'diagnosis': reason, 'seconds': elapsed})
            if solved:
                episode.status, episode.route = 'solved', route
                break
        if learn:
            self._observations = (self._observations + observations)[-self.MAX_OBSERVATIONS:]
            self.policy = StrategyPolicy.fit(self._observations)
            if episode.status == 'solved' and episode.expression is not None:
                try:
                    self._learn(problem, episode, source_id)
                except (ValueError, TypeError, RecursionError) as exc:
                    # A result can remain verified when its printed form is too
                    # large for schema learning. Do not pretend knowledge grew.
                    episode.learning_error = type(exc).__name__
        episode.seconds = time.perf_counter() - started
        return episode

    def _learn(self, problem, episode, source_id):
        validate_expression(episode.expression, problem.variables)
        example = VerifiedExample(problem.expression, episode.expression, problem.variables,
            source_id or 'episode:' + content_digest((problem.expression, problem.variables)))
        if any((e.before, e.after, e.variables) == (example.before, example.after, example.variables)
               for e in self._examples): return
        self._examples = (self._examples + [example])[-self.MAX_EXAMPLES:]
        # Include every pair with the newly solved example. Bounded incremental
        # discovery avoids repeatedly paying for all old-old pairs.
        for previous in self._examples[:-1]:
            found = discover_schemas((previous, example), max_pairs=1)
            episode.learning_checks += found.input_checks + found.schema_checks
            episode.learning_rejections += found.rejected_examples + found.rejected_generalizations
            for candidate in found.candidates:
                try: self.library.get(candidate.id)
                except KeyError: pass
                else: continue  # Includes terminally revoked identities.
                if self.library.record_count >= self.MAX_RULES: return
                record = self.library.admit(candidate)
                episode.learning_checks += 1
                if record.status == 'verified': episode.learned_ids.append(candidate.id)
                else: episode.learning_rejections += 1

    def freeze(self):
        library = KnowledgeLibrary({'algebra': AlgebraVerifier()})
        for record in self.library.lookup('algebra'):
            if library.admit(record.candidate).status != 'verified':
                raise ValueError('snapshot revalidation failed')
        policy = StrategyPolicy.from_json(self.policy.to_json()) if self.policy else None
        snapshot = LifelongMathAgent(library=library, policy=policy, frozen=True)
        snapshot._examples = list(self._examples)
        snapshot._observations = list(self._observations)
        return snapshot

    def fingerprint(self):
        return content_digest({'rules': [(r.candidate.id, r.fingerprint) for r in self.library.lookup('algebra')],
            'policy': self.policy.fingerprint() if self.policy else None,
            'examples': [asdict(e) for e in self._examples],
            'observations': [asdict(o) for o in self._observations], 'frozen': self.frozen})

    def save(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.library.save(directory / 'rules.json')
        data = {'version': 1, 'frozen': self.frozen,
                'rules_digest': content_digest((directory / 'rules.json').read_text(encoding='utf-8')),
                'policy': self.policy.to_json() if self.policy else None,
                'examples': [asdict(e) for e in self._examples],
                'observations': [asdict(o) for o in self._observations]}
        # Publish metadata atomically. A mixed generation fails its digest check
        # rather than being loaded as a coherent snapshot. One writer per store.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                             prefix='.learning-', delete=False) as handle:
                temporary = handle.name
                handle.write(json.dumps(data, sort_keys=True)); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, directory / 'learning.json'); temporary = None
        finally:
            if temporary is not None: os.unlink(temporary)

    @classmethod
    def load(cls, directory):
        directory = Path(directory)
        if (directory / 'learning.json').stat().st_size > 4 * 1024 * 1024: raise ValueError('learning snapshot too large')
        text = (directory / 'learning.json').read_text(encoding='utf-8')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('duplicate snapshot field')
                result[key] = value
            return result
        data = json.loads(text, object_pairs_hook=unique)
        if (type(data) is not dict or set(data) != {'version','frozen','policy','examples','observations','rules_digest'}
                or type(data['version']) is not int or data['version'] != 1
                or type(data['examples']) is not list or len(data['examples']) > cls.MAX_EXAMPLES
                or type(data['observations']) is not list or len(data['observations']) > cls.MAX_OBSERVATIONS):
            raise ValueError('invalid learning snapshot')
        if (directory/'rules.json').stat().st_size > 16 * 1024 * 1024: raise ValueError('rules snapshot too large')
        if content_digest((directory/'rules.json').read_text(encoding='utf-8')) != data['rules_digest']:
            raise ValueError('mixed or modified memory generation')
        agent = cls(library=KnowledgeLibrary.load(directory/'rules.json', {'algebra': AlgebraVerifier()}),
                    policy=StrategyPolicy.from_json(data['policy']) if data['policy'] is not None else None,
                    frozen=data['frozen'])
        for row in data['examples']:
            if type(row) is not dict or set(row) != {'before','after','variables','source_id'} or type(row['variables']) is not list:
                raise ValueError('invalid history example')
            example = VerifiedExample(**{**row, 'variables': tuple(row['variables'])})
            if verify_identity(example.before, example.after, example.variables).status != 'verified':
                raise ValueError('unverified learning history')
            agent._examples.append(example)
        agent._observations = [StrategyObservation(**row) for row in data['observations']]
        return agent
