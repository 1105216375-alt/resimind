"""A neuro-symbolic Agent architecture with evidence-bound residual reasoning."""
from .core import (Binding, Candidate, Decision, Evidence, Fact, JSONScalar,
                   JSONValue, Residual, RunResult, State, TraceEvent, Verdict,
                   canonical_json, content_digest)
from .runtime import Domain, Engine, Proposer, Verifier
from .agent import Agent, AgentResult, EvidenceTool, Task, ToolEvent
from .adapters import ModelProposer, ModelResponseError
from .memory import Route, RouteMemory

__all__ = [
    "Agent", "AgentResult", "EvidenceTool", "Task", "ToolEvent",
    "ModelProposer", "ModelResponseError", "Route", "RouteMemory",
    "Binding", "Candidate", "Decision", "Domain", "Engine", "Evidence", "Fact",
    "JSONScalar", "JSONValue", "Proposer", "Residual", "RunResult", "State",
    "TraceEvent", "Verdict", "Verifier", "canonical_json", "content_digest",
]

__version__ = "0.5.0"
