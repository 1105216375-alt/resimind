"""A neuro-symbolic Agent architecture with evidence-bound residual reasoning."""
from .core import (Binding, Candidate, Decision, Evidence, Fact, JSONScalar,
                   JSONValue, Residual, RunResult, State, TraceEvent, Verdict,
                   canonical_json, content_digest)
from .runtime import Domain, Engine, Proposer, Verifier
from .agent import Agent, AgentResult, EvidenceTool, Task, ToolEvent
from .adapters import ModelProposer, ModelResponseError
from .memory import Route, RouteMemory
from .knowledge import (KnowledgeCandidate, KnowledgeLibrary, KnowledgeRecord,
                        KnowledgeVerifier, Verification)
from .learning import LearningAgent, LearningResult
from .strategy import FailureKind, StrategyController, StrategyEvent

__all__ = [
    "Agent", "AgentResult", "EvidenceTool", "Task", "ToolEvent",
    "ModelProposer", "ModelResponseError", "Route", "RouteMemory",
    "KnowledgeCandidate", "KnowledgeLibrary", "KnowledgeRecord", "KnowledgeVerifier",
    "Verification", "LearningAgent", "LearningResult",
    "FailureKind", "StrategyController", "StrategyEvent",
    "Binding", "Candidate", "Decision", "Domain", "Engine", "Evidence", "Fact",
    "JSONScalar", "JSONValue", "Proposer", "Residual", "RunResult", "State",
    "TraceEvent", "Verdict", "Verifier", "canonical_json", "content_digest",
]

__version__ = "0.9.0"
