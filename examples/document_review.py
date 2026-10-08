"""Verify required sections in a synthetic document, fully offline.

This example checks section presence only. It makes no claim about factual,
legal or stylistic quality. The first proposal omits a reference and is deferred.
Run with ``python -m examples.document_review``.
"""

from __future__ import annotations

from resimind import (
    Candidate, Decision, Engine, Evidence, Fact, Residual, RunResult, State,
    Verdict,
)

SUBJECT = "synthetic-handbook"
SCOPE = "draft-2"
UNIT = "text"
ACTION = "check_required_sections"
TARGET = "document:required_sections_present"
REQUIRED = ("section:purpose", "section:owner", "section:review_date")


def make_evidence(*, include_review_date: bool = True) -> tuple[Evidence, ...]:
    texts = ("Describe the fictional team's workflow.",
             "Fictional operations team", "2030-01-01")
    evidence = tuple(
        Evidence(f"evidence:{metric}", SUBJECT, metric, text, UNIT,
                 "synthetic-document-fixture", SCOPE)
        for metric, text in zip(REQUIRED, texts)
    )
    return evidence if include_review_date else evidence[:-1]


class DocumentDomain:
    """Only the specified document revision can discharge these obligations."""

    def rebuild(self, state: State) -> Residual:
        present = {
            fact.metric for fact in state.facts
            if fact.subject == SUBJECT and fact.scope == SCOPE
            and fact.unit == UNIT and type(fact.value) is str and fact.value.strip()
        }
        missing = tuple(f"document:missing:{metric}" for metric in REQUIRED
                        if metric not in present)
        return Residual(goals=(TARGET,) if missing else (), unknowns=missing)


class DocumentVerifier:
    def verify(
        self, candidate: Candidate, state: State, residual: Residual,
        evidence: tuple[Evidence, ...],
    ) -> Verdict:
        def verdict(decision: Decision, reason: str, facts=()) -> Verdict:
            return Verdict.for_candidate(
                candidate, state, decision, evidence=evidence,
                facts=facts, reasons=(reason,),
            )

        if candidate.action != ACTION or candidate.target != TARGET:
            return verdict(Decision.REJECT, "unsupported_action_or_target")
        by_id = {item.id: item for item in evidence}
        sections: dict[str, Evidence] = {}
        for reference in candidate.refs:
            item = by_id.get(reference)
            if item is None:
                return verdict(Decision.REJECT, "unknown_evidence_reference")
            if (item.subject != SUBJECT or item.scope != SCOPE
                    or item.unit != UNIT or item.metric not in REQUIRED):
                return verdict(Decision.REJECT, "evidence_binding_mismatch")
            if type(item.value) is not str or not item.value.strip():
                return verdict(Decision.REJECT, "empty_or_invalid_section")
            if item.metric in sections:
                return verdict(Decision.REJECT, "ambiguous_section")
            sections[item.metric] = item
        if any(metric not in sections for metric in REQUIRED):
            return verdict(Decision.DEFER, "missing_required_section_evidence")
        if candidate.claim != "required_sections_present":
            return verdict(Decision.REJECT, "unsupported_claim")
        facts = tuple(
            Fact(f"fact:{metric}", SUBJECT, metric, sections[metric].value,
                 UNIT, (sections[metric].id,), SCOPE)
            for metric in REQUIRED
        )
        return verdict(Decision.ACCEPT, "required_sections_verified", facts)


class DocumentProposer:
    def __init__(self, evidence: tuple[Evidence, ...]) -> None:
        self.evidence = evidence
        self.attempt = 0

    def propose(self, state: State, residual: Residual) -> tuple[Candidate, ...]:
        self.attempt += 1
        refs = tuple(item.id for item in self.evidence)
        if self.attempt == 1:
            refs = refs[:2]
        return (Candidate(f"document-proposal:{self.attempt}", ACTION, TARGET,
                          "required_sections_present", refs),)


def run_demo(*, include_review_date: bool = True) -> RunResult:
    evidence = make_evidence(include_review_date=include_review_date)
    return Engine(
        DocumentDomain(), DocumentVerifier(), (DocumentProposer(evidence),),
        evidence=evidence, max_steps=5, max_no_progress=3,
    ).run()


if __name__ == "__main__":
    print(run_demo().to_json())
