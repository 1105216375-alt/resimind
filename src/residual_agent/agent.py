"""Task-level agent orchestration around the verified reasoning runtime.

The agent collects current evidence with registered Python tools, retrieves
reviewed action routes, constructs task-scoped plugins, and runs the engine.
Tools and factories are trusted application code. Calls are synchronous: step
limits do not interrupt a hanging tool, model call, or verifier and do not
measure tokens. Applications must enforce those resource limits externally.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .core import Decision, Evidence, Residual, RunResult, Serializable, State, TraceEvent
from .memory import Route, RouteMemory
from .runtime import Domain, Engine, Proposer, Verifier


def _text(value: object, field: str) -> None:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be a nonempty string without surrounding whitespace")


@dataclass(frozen=True, slots=True)
class Task(Serializable):
    """A task and explicit context used to select its domain and reviewed routes.

    The instruction is a request, never evidence. Context keys and values are
    exact-match strings; domain adapters decide the actual proof obligations.
    """

    id: str
    instruction: str
    domain: str
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name in ("id", "instruction", "domain"):
            _text(getattr(self, name), name)
        if type(self.context) is not tuple:
            raise ValueError("context must be a tuple of (key, value) tuples")
        keys: set[str] = set()
        for pair in self.context:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError("each context item must be a (key, value) tuple")
            key, value = pair
            _text(key, "context key")
            _text(value, "context value")
            if key in keys:
                raise ValueError(f"duplicate context key: {key}")
            keys.add(key)


class EvidenceTool(Protocol):
    """Trusted collector registered by the application, not chosen by model code.

    Collection is invoked once per run before reasoning. The returned records
    are input evidence; collecting them does not make them verified facts.
    """

    name: str

    def collect(self, task: Task) -> tuple[Evidence, ...]:
        ...


@dataclass(frozen=True, slots=True)
class ToolEvent(Serializable):
    """A collection result; errors record exception types, not raw tool messages."""

    tool: str
    status: str
    evidence: tuple[Evidence, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        _text(self.tool, "tool")
        if self.status not in ("collected", "error"):
            raise ValueError("tool status must be collected or error")
        if type(self.evidence) is not tuple or any(type(item) is not Evidence for item in self.evidence):
            raise ValueError("event evidence must be a tuple of Evidence")
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError("event evidence IDs must be unique")
        if self.status == "collected" and self.error is not None:
            raise ValueError("a collected event cannot contain an error")
        if self.status == "error":
            _text(self.error, "error")
            if self.evidence:
                raise ValueError("an error event cannot expose unvalidated tool evidence")


@dataclass(frozen=True, slots=True)
class AgentResult(Serializable):
    """Verified facts, unresolved obligations, and the evidence/decision audit.

    This object deliberately contains no independently generated final answer.
    A caller can render the committed facts and residual directly. A suggested
    route is planning guidance only; it is never a pre-approved conclusion.
    """

    task: Task
    run_result: RunResult
    tool_events: tuple[ToolEvent, ...] = ()
    routes: tuple[Route, ...] = ()

    def __post_init__(self) -> None:
        if type(self.task) is not Task or type(self.run_result) is not RunResult:
            raise ValueError("AgentResult requires a Task and RunResult")
        if type(self.tool_events) is not tuple or any(type(item) is not ToolEvent for item in self.tool_events):
            raise ValueError("tool_events must be a tuple of ToolEvent")
        if type(self.routes) is not tuple or any(type(item) is not Route for item in self.routes):
            raise ValueError("routes must be a tuple of Route")


class Agent:
    """Collect -> recall reviewed routes -> propose -> verify -> commit -> stop.

    Factories create plugins for each run. ``proposer_factory`` receives the
    task, matching routes, and the complete evidence tuple so a model or rule
    proposer can reason over current inputs. The verifier receives those same
    inputs through the engine. Route selection checks metric names, domain,
    and declared context only; domain truth remains the verifier's job.

    Tool failure aborts before factories or reasoning run. Already collected
    evidence remains in the audit but is never promoted to facts. This does
    not roll back external side effects of a trusted collector; read-only
    collectors are appropriate for this reference agent.
    """

    def __init__(
        self,
        *,
        domain_factory: Callable[[Task], Domain],
        verifier_factory: Callable[[Task], Verifier],
        proposer_factory: Callable[[Task, tuple[Route, ...], tuple[Evidence, ...]], tuple[Proposer, ...]],
        tools: tuple[EvidenceTool, ...] = (),
        memory: RouteMemory | None = None,
        max_steps: int = 50,
        max_no_progress: int = 5,
    ) -> None:
        for name, factory in (
            ("domain_factory", domain_factory),
            ("verifier_factory", verifier_factory),
            ("proposer_factory", proposer_factory),
        ):
            if not callable(factory):
                raise ValueError(f"{name} must be callable")
        if type(tools) is not tuple:
            raise ValueError("tools must be a tuple of registered EvidenceTool objects")
        names: set[str] = set()
        for tool in tools:
            name = getattr(tool, "name", None)
            _text(name, "tool name")
            if name in names:
                raise ValueError(f"duplicate tool name: {name}")
            if not callable(getattr(tool, "collect", None)):
                raise ValueError("each tool must provide collect(task)")
            names.add(name)
        if memory is not None and not isinstance(memory, RouteMemory):
            raise ValueError("memory must be a RouteMemory or None")
        for name, budget in (("max_steps", max_steps), ("max_no_progress", max_no_progress)):
            if type(budget) is not int or budget < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.domain_factory = domain_factory
        self.verifier_factory = verifier_factory
        self.proposer_factory = proposer_factory
        self.tools = tools
        self.memory = memory
        self.max_steps = max_steps
        self.max_no_progress = max_no_progress

    def run(self, task: Task) -> AgentResult:
        """Run one task from empty verified state using freshly collected inputs."""
        if type(task) is not Task:
            raise ValueError("task must be a Task")
        collected: list[Evidence] = []
        known_ids: set[str] = set()
        events: list[ToolEvent] = []
        for tool in self.tools:
            try:
                records = tool.collect(task)
                if type(records) is not tuple or any(type(item) is not Evidence for item in records):
                    raise TypeError("collect must return a tuple of Evidence")
                new_ids = {item.id for item in records}
                if len(new_ids) != len(records) or new_ids & known_ids:
                    raise ValueError("tool evidence IDs must be unique across the run")
            except Exception as exc:
                events.append(ToolEvent(tool.name, "error", error=type(exc).__name__))
                return self._failure(task, events, (), "tool_error", type(exc).__name__)
            collected.extend(records)
            known_ids.update(new_ids)
            events.append(ToolEvent(tool.name, "collected", records))

        evidence = tuple(collected)
        routes: tuple[Route, ...] = ()
        try:
            if self.memory is not None:
                routes = self.memory.suggest(
                    domain=task.domain,
                    context=dict(task.context),
                    available_metrics=tuple(item.metric for item in evidence),
                )
            domain = self.domain_factory(task)
            verifier = self.verifier_factory(task)
            proposers = self.proposer_factory(task, routes, evidence)
            if not callable(getattr(domain, "rebuild", None)):
                raise TypeError("domain factory must return a Domain")
            if not callable(getattr(verifier, "verify", None)):
                raise TypeError("verifier factory must return a Verifier")
            if type(proposers) is not tuple or any(
                not callable(getattr(proposer, "propose", None)) for proposer in proposers
            ):
                raise TypeError("proposer factory must return a tuple of Proposer objects")
            engine = Engine(
                domain, verifier, proposers, evidence=evidence,
                max_steps=self.max_steps, max_no_progress=self.max_no_progress,
            )
        except Exception as exc:
            return self._failure(task, events, routes, "agent_setup_error", type(exc).__name__)
        return AgentResult(task, engine.run(), tuple(events), routes)

    @staticmethod
    def _failure(
        task: Task,
        events: list[ToolEvent],
        routes: tuple[Route, ...],
        reason: str,
        error_type: str,
    ) -> AgentResult:
        state = State()
        residual = Residual(hard_constraints=(reason,))
        event = TraceEvent(
            0, "agent", Decision.INTERRUPT, (reason, error_type),
            state, state, residual, residual,
        )
        evidence = tuple(item for tool_event in events for item in tool_event.evidence)
        result = RunResult(state, residual, "error", reason, (event,), evidence)
        return AgentResult(task, result, tuple(events), routes)
