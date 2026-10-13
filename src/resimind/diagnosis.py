"""Conservative typed diagnoses of observed verifier/tool feedback.

These labels describe the observed failure boundary and suggest a recovery;
they are not learned causal explanations, truth judgments, or authorizations.
"""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Diagnosis:
    kind: str
    observation: str
    suggested_action: str


def diagnose(reason: str, *, verified: bool, progress: bool) -> Diagnosis:
    if 'no_new_state' in reason or (verified and not progress):
        kind, action = 'goal_progress', 'propose a different decomposition or unfinished subgoal'
    elif verified:
        kind, action = 'progress', 'continue with the remaining obligations'
    elif 'coefficients differ' in reason or 'coefficients do not match' in reason:
        kind, action = 'identity_mismatch', 'repair the reported algebraic mismatch or change method'
    elif 'unsupported_tactic' in reason:
        kind, action = 'tool_contract', 'use an allowed tactic: grind or rfl'
    elif 'lean' in reason or 'formal_proof' in reason:
        kind, action = 'proof_obligation', 'inspect the Lean goal, diagnostics and proof budget'
    elif 'expression_validation' in reason or 'resource' in reason or 'budget' in reason:
        kind, action = 'representation_or_budget', 'use a compact representation or an available bounded tool'
    elif 'model_callback' in reason:
        kind, action = 'tool_failure', 'inspect provider availability; do not treat this as a wrong proof'
    elif 'model_response' in reason:
        kind, action = 'tool_contract', 'return the required bounded JSON schema'
    elif 'rule_revoked' in reason:
        kind, action = 'knowledge_invalidated', 'retrieve a current verified rule or recompute'
    else:
        kind, action = 'method_unresolved', 'try another available strategy within the remaining budget'
    return Diagnosis(kind, reason[:2048], action)
