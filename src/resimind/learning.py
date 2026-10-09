"""Task-level knowledge growth around the existing verified Agent.

The factory and distiller are trusted application plugins. Retrieved knowledge
is proposal guidance, not a fact injected into the new task. A completed task
allows distillation; the library's independent verifier still decides admission.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .agent import Agent, AgentResult, Task
from .knowledge import KnowledgeCandidate, KnowledgeLibrary, KnowledgeRecord


@dataclass(frozen=True, slots=True)
class LearningResult:
    result: AgentResult
    retrieved_ids: tuple[str, ...]
    admissions: tuple[KnowledgeRecord, ...] = ()
    learning_errors: tuple[str, ...] = ()


class LearningAgent:
    """Retrieve -> verified task -> distill -> independently verify -> store.

    ``assumptions`` is a trusted application's query filter, not proof that an
    assumption holds. The task's verifier must check applicability again.
    ``learn=False`` freezes the library during an evaluation or deployment run.
    Failed/incomplete tasks never reach the distiller. Learning errors are
    reported separately and cannot change the completed task's verified facts.
    """

    def __init__(
        self, *, library: KnowledgeLibrary,
        agent_factory: Callable[[Task, tuple[KnowledgeRecord, ...]], Agent],
        distill: Callable[[AgentResult], Iterable[KnowledgeCandidate]],
        assumptions: Callable[[Task], tuple[str, ...]] = lambda task: (),
        max_candidates: int = 16,
    ) -> None:
        if not isinstance(library, KnowledgeLibrary):
            raise ValueError("library must be a KnowledgeLibrary")
        if any(not callable(item) for item in (agent_factory, distill, assumptions)):
            raise ValueError("learning plugins must be callable")
        if type(max_candidates) is not int or max_candidates < 1:
            raise ValueError("max_candidates must be a positive integer")
        self.library = library
        self.agent_factory = agent_factory
        self.distill = distill
        self.assumptions = assumptions
        self.max_candidates = max_candidates

    def run(self, task: Task, *, learn: bool = True) -> LearningResult:
        if type(task) is not Task or type(learn) is not bool:
            raise ValueError("run requires a Task and boolean learn")
        records = self.library.lookup(domain=task.domain, assumptions=self.assumptions(task))
        retrieved_ids = tuple(record.candidate.id for record in records)
        agent = self.agent_factory(task, records)
        if not isinstance(agent, Agent):
            raise TypeError("agent_factory must return an Agent")
        result = agent.run(task)
        run = result.run_result
        if not learn or run.status != "solved" or not run.residual.solved:
            return LearningResult(result, retrieved_ids)
        admissions: list[KnowledgeRecord] = []
        errors: list[str] = []
        try:
            for index, candidate in enumerate(self.distill(result)):
                if index >= self.max_candidates:
                    errors.append("distillation_limit")
                    break
                if (type(candidate) is not KnowledgeCandidate
                        or candidate.source_task_id != task.id
                        or candidate.domain != task.domain):
                    errors.append("distillation_task_binding_mismatch")
                    continue
                admissions.append(self.library.admit(candidate))
        except Exception as exc:
            errors.append(f"learning_error:{type(exc).__name__}")
        return LearningResult(result, retrieved_ids, tuple(admissions), tuple(errors))
