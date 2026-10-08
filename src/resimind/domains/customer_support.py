"""A synthetic return recommendation, checked against a declared snapshot.

This adapter checks a fictional merchant policy, not consumer law. It neither
authenticates customers nor executes refunds or sends messages. Matching IDs
establishes consistency of supplied records only. The caller must authenticate
and authorize access before supplying an order. The full audit is internal
application data; ``verified_resolution`` is a bounded structured recommendation.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from datetime import date
import json
import re

from ..adapters import ModelProposer
from ..agent import Agent, AgentResult, Task
from ..core import Candidate, Decision, Evidence, Fact, Residual, State, Verdict, canonical_json, content_digest

DOMAIN = "customer_support"
ACTIONS = ("check_return_eligibility", "calculate_refund", "recommend_resolution")
TARGETS = ("return:eligibility", "return:refund_amount", "return:resolution")
FACT_IDS = ("return:eligibility_checked", "return:amount_checked", "return:resolution_checked")
ELIGIBILITY_FACT, AMOUNT_FACT, RESOLUTION_FACT = FACT_IDS
DELIVERY_UNKNOWN = "return:delivery_date"
STATE_CONSTRAINT = "return:unverified_state_facts"
_CASE_SOURCE = "declared-order-snapshot"
_POLICY_SOURCE = "fictional-merchant-policy"


def _identifier(value: object) -> None:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", value):
        raise ValueError("identifiers must be bounded ASCII identifiers")


def _integer(value: object, maximum: int = 10**12) -> None:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError("require a bounded nonnegative integer; booleans are not amounts")


def _date(value: object) -> date:
    if type(value) is not str or len(value) != 10:
        raise ValueError("dates must be YYYY-MM-DD strings")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("dates must be YYYY-MM-DD strings")
    return parsed


@dataclass(frozen=True, slots=True)
class ReturnPolicy:
    """Fictional inclusive delivery-to-request window; shipping is excluded."""

    policy_id: str = "fictional-returns-v1"
    window_days: int = 14

    def __post_init__(self):
        _identifier(self.policy_id)
        _integer(self.window_days, 365)


@dataclass(frozen=True, slots=True)
class ReturnCase:
    """One immutable, full-item return request; all money is integer CNY cents.

    Dates are local calendar dates, not timestamps. The as-of date is supplied
    explicitly: future delivery/request dates and reversed chronology are
    invalid snapshots. Missing delivery is allowed and remains an obligation.
    Prior item refunds must already be accounted for in this snapshot. This
    example does not coordinate concurrent requests or reserve any funds.
    """

    case_id: str
    order_id: str
    customer_id: str
    request_order_id: str
    request_customer_id: str
    snapshot_date: str
    requested_on: str
    delivered_on: str | None
    item_paid_cents: int
    shipping_paid_cents: int
    prior_item_refund_cents: int = 0
    list_price_cents: int = 29900
    item_returnable: bool = True
    currency: str = "CNY"
    policy: ReturnPolicy = ReturnPolicy()

    def __post_init__(self):
        for name in ("case_id", "order_id", "customer_id", "request_order_id", "request_customer_id"):
            _identifier(getattr(self, name))
        for name in ("item_paid_cents", "shipping_paid_cents", "prior_item_refund_cents", "list_price_cents"):
            _integer(getattr(self, name))
        if self.prior_item_refund_cents > self.item_paid_cents:
            raise ValueError("prior item refunds exceed the actual item payment")
        if type(self.item_returnable) is not bool or self.currency != "CNY" or type(self.currency) is not str:
            raise ValueError("require a boolean returnability flag and currency CNY")
        if type(self.policy) is not ReturnPolicy:
            raise ValueError("require a ReturnPolicy")
        snapshot, requested = _date(self.snapshot_date), _date(self.requested_on)
        if requested > snapshot:
            raise ValueError("request date is after the declared snapshot")
        if self.delivered_on is not None:
            delivered = _date(self.delivered_on)
            if delivered > snapshot or delivered > requested:
                raise ValueError("delivery date is after the request or declared snapshot")


_CASE_FIELDS = tuple(field.name for field in fields(ReturnCase) if field.name != "policy")
_POLICY_FIELDS = tuple(field.name for field in fields(ReturnPolicy))
EVIDENCE_IDS = tuple("return:input:" + name for name in _CASE_FIELDS) + tuple(
    "return:policy:" + name for name in _POLICY_FIELDS)


def _scope(case: ReturnCase) -> str:
    return "return-snapshot:" + content_digest(case)


@dataclass(frozen=True, slots=True)
class ReturnCaseTool:
    case: ReturnCase
    name: str = "declared_return_case_reader"

    def collect(self, task: Task) -> tuple[Evidence, ...]:
        if task.domain != DOMAIN:
            raise ValueError(f"ReturnCaseTool requires domain={DOMAIN!r}")
        scope = _scope(self.case)
        case_records = tuple(Evidence(
            "return:input:" + name, self.case.order_id, name, getattr(self.case, name),
            "cents" if name.endswith("_cents") else "date" if name in (
                "snapshot_date", "requested_on", "delivered_on") else "", _CASE_SOURCE, scope)
            for name in _CASE_FIELDS)
        policy_records = tuple(Evidence(
            "return:policy:" + name, self.case.policy.policy_id, name, getattr(self.case.policy, name),
            "days" if name == "window_days" else "", _POLICY_SOURCE, scope) for name in _POLICY_FIELDS)
        return case_records + policy_records


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_claim_key")
        result[key] = value
    return result


def _parse(claim: str) -> dict:
    if type(claim) is not str or len(claim) > 8192:
        raise ValueError("claim_must_be_a_bounded_json_object")
    payload = json.loads(claim, object_pairs_hook=_unique,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non_json_constant")))
    if type(payload) is not dict:
        raise ValueError("claim_must_be_a_json_object")
    return payload


def _eligibility(case: ReturnCase) -> dict:
    if case.delivered_on is None:
        raise ValueError("delivery_date_missing")
    days = (_date(case.requested_on) - _date(case.delivered_on)).days
    if case.request_order_id != case.order_id or case.request_customer_id != case.customer_id:
        reason = "request_identity_mismatch"
    elif not case.item_returnable:
        reason = "item_not_returnable"
    elif days > case.policy.window_days:
        reason = "outside_return_window"
    else:
        reason = "within_return_window"
    return {"case_id": case.case_id, "order_id": case.order_id, "customer_id": case.customer_id,
            "policy_id": case.policy.policy_id, "days_since_delivery": days,
            "eligible": reason == "within_return_window", "reason": reason}


def _amount(case: ReturnCase) -> dict:
    return {"item_paid_cents": case.item_paid_cents, "prior_item_refund_cents": case.prior_item_refund_cents,
            "excluded_shipping_cents": case.shipping_paid_cents,
            "refund_cents": case.item_paid_cents - case.prior_item_refund_cents, "currency": case.currency}


def _resolution(case: ReturnCase, eligibility: dict, amount: dict | None) -> dict:
    eligible = eligibility["eligible"]
    mismatched = eligibility["reason"] == "request_identity_mismatch"
    return {"case_id": case.case_id, "order_id": case.request_order_id, "customer_id": case.request_customer_id,
            "policy_id": case.policy.policy_id, "outcome": "refund_recommended" if eligible else "human_review",
            "refund_cents": amount["refund_cents"] if eligible else None, "currency": case.currency,
            "excluded_shipping_cents": None if mismatched else case.shipping_paid_cents,
            "reason": eligibility["reason"],
            "next_action": "merchant_refund_review" if eligible else "manual_policy_review",
            "payment_executed": False}


def _checked(case: ReturnCase, stage: int, payload: dict, previous: dict) -> dict:
    if stage == 0:
        expected = _eligibility(case)
    elif stage == 1:
        if not previous[0]["eligible"]:
            raise ValueError("refund_not_supported_by_policy_assessment")
        expected = _amount(case)
    else:
        expected = _resolution(case, previous[0], previous.get(1))
    if set(payload) != set(expected):
        raise ValueError("unexpected_claim_fields")
    # Canonical JSON preserves booleans vs integers: Python's True == 1 must
    # never turn a forged amount, day count, or execution flag into a fact.
    if canonical_json(payload) != canonical_json(expected):
        raise ValueError(("eligibility_claim_mismatch", "refund_amount_mismatch", "resolution_claim_mismatch")[stage])
    return expected


def _fact(case: ReturnCase, stage: int, payload: dict) -> Fact:
    return Fact(FACT_IDS[stage], case.order_id, ACTIONS[stage], canonical_json(payload),
                "", EVIDENCE_IDS, _scope(case))


def _chain(case: ReturnCase, state: State) -> dict:
    previous = {}
    for stage in range(3):
        if stage == 1 and not previous[0]["eligible"]:
            continue
        expected_key = (case.order_id, ACTIONS[stage], _scope(case))
        matches = [fact for fact in state.facts if fact.id == FACT_IDS[stage] or fact.key == expected_key]
        if len(matches) != 1:
            break
        try:
            payload = _checked(case, stage, _parse(matches[0].value), previous)
            if canonical_json(matches[0]) != canonical_json(_fact(case, stage, payload)):
                break
        except (ValueError, TypeError, KeyError, RecursionError):
            break
        previous[stage] = payload
    return previous


@dataclass(frozen=True, slots=True)
class CustomerSupportDomain:
    case: ReturnCase

    def rebuild(self, state: State) -> Residual:
        previous = _chain(self.case, state)
        needs_amount = 1 not in previous and (0 not in previous or previous[0]["eligible"])
        unaccounted = {fact.id for fact in state.facts} != {FACT_IDS[stage] for stage in previous}
        return Residual(goals=() if 2 in previous else (TARGETS[2],),
                        unknowns=((DELIVERY_UNKNOWN,) if self.case.delivered_on is None else ())
                        + ((TARGETS[1],) if needs_amount else ()),
                        hard_constraints=(() if 0 in previous else (TARGETS[0],))
                        + ((STATE_CONSTRAINT,) if unaccounted else ()))


@dataclass(frozen=True, slots=True)
class CustomerSupportVerifier:
    case: ReturnCase

    def verify(self, candidate: Candidate, state: State, residual: Residual,
               evidence: tuple[Evidence, ...]) -> Verdict:
        def verdict(decision, reason, facts=()):
            return Verdict.for_candidate(candidate, state, decision, evidence=evidence, reasons=(reason,), facts=facts)
        if candidate.action not in ACTIONS:
            return verdict(Decision.REJECT, "unsupported_action_or_target")
        stage = ACTIONS.index(candidate.action)
        if candidate.target != TARGETS[stage] or candidate.target not in residual.pending:
            return verdict(Decision.REJECT, "unsupported_action_or_target")
        expected = ReturnCaseTool(self.case).collect(Task("check", "check", DOMAIN))
        if len(evidence) != len(expected) or {item.id: canonical_json(item) for item in evidence} != {
                item.id: canonical_json(item) for item in expected}:
            return verdict(Decision.REJECT, "declared_snapshot_mismatch")
        if not set(EVIDENCE_IDS) <= set(candidate.refs):
            return verdict(Decision.REJECT, "missing_snapshot_refs")
        if not set(candidate.refs) <= set(EVIDENCE_IDS) | {fact.id for fact in state.facts}:
            return verdict(Decision.REJECT, "unknown_reference")
        previous = _chain(self.case, state)
        required = () if stage == 0 else (0,) if stage == 1 or (0 in previous and not previous[0]["eligible"]) else (0, 1)
        if any(index not in previous for index in required):
            return verdict(Decision.DEFER, "missing_verified_prerequisite")
        if {fact.id for fact in state.facts} != {FACT_IDS[index] for index in previous}:
            return verdict(Decision.REJECT, "unverified_state_facts")
        if not {FACT_IDS[index] for index in required} <= set(candidate.refs):
            return verdict(Decision.REJECT, "missing_verified_fact_refs")
        if self.case.delivered_on is None:
            return verdict(Decision.DEFER, "delivery_date_missing")
        try:
            payload = _checked(self.case, stage, _parse(candidate.claim), previous)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            return verdict(Decision.REJECT, str(exc) or "invalid_claim")
        return verdict(Decision.ACCEPT, ("return_eligibility_checked", "refund_amount_checked",
                                        "resolution_checked")[stage], (_fact(self.case, stage, payload),))


def _offline_completion(case: ReturnCase):
    """An untrusted scripted proposer; it does not invoke verifier helpers."""
    attempt, corrected = 0, False

    def complete(prompt):
        nonlocal attempt, corrected
        attempt += 1
        data = json.loads(prompt)
        feedback = data["last_feedback"]
        if feedback and feedback["decision"] == "reject" and "refund_amount_mismatch" in feedback["reasons"]:
            corrected = True
        facts = {fact["id"]: json.loads(fact["value"]) for fact in data["state"]["facts"]}
        if RESOLUTION_FACT in facts:
            return "null"
        if ELIGIBILITY_FACT not in facts:
            stage = 0
            days = None if case.delivered_on is None else (
                date.fromisoformat(case.requested_on) - date.fromisoformat(case.delivered_on)).days
            reason = ("request_identity_mismatch" if (case.request_order_id, case.request_customer_id) != (
                case.order_id, case.customer_id) else "item_not_returnable" if not case.item_returnable
                else "delivery_date_missing" if days is None else "outside_return_window"
                if days > case.policy.window_days else "within_return_window")
            payload = {"case_id": case.case_id, "order_id": case.order_id, "customer_id": case.customer_id,
                       "policy_id": case.policy.policy_id, "days_since_delivery": days,
                       "eligible": reason == "within_return_window", "reason": reason}
        elif facts[ELIGIBILITY_FACT]["eligible"] and AMOUNT_FACT not in facts:
            stage = 1
            payload = {"item_paid_cents": case.item_paid_cents,
                       "prior_item_refund_cents": case.prior_item_refund_cents,
                       "excluded_shipping_cents": case.shipping_paid_cents,
                       "refund_cents": case.item_paid_cents - case.prior_item_refund_cents
                       + (0 if corrected else case.shipping_paid_cents), "currency": case.currency}
        else:
            stage = 2
            assessment = facts[ELIGIBILITY_FACT]
            eligible = assessment["eligible"]
            payload = {"case_id": case.case_id, "order_id": case.request_order_id,
                       "customer_id": case.request_customer_id, "policy_id": case.policy.policy_id,
                       "outcome": "refund_recommended" if eligible else "human_review",
                       "refund_cents": facts[AMOUNT_FACT]["refund_cents"] if eligible else None,
                       "currency": case.currency, "excluded_shipping_cents": None
                       if assessment["reason"] == "request_identity_mismatch" else case.shipping_paid_cents,
                       "reason": assessment["reason"], "next_action": "merchant_refund_review"
                       if eligible else "manual_policy_review", "payment_executed": False}
        refs = EVIDENCE_IDS + tuple(identifier for identifier in FACT_IDS[:stage] if identifier in facts)
        return json.dumps({"id": f"return-proposal:{attempt}", "action": ACTIONS[stage], "target": TARGETS[stage],
                           "claim": json.dumps(payload), "refs": list(refs)})
    return complete


def build_agent(case: ReturnCase, complete: Callable[[str], str] | None = None) -> Agent:
    """Create a fresh bounded agent; an injected callback supplies candidates only."""
    if type(case) is not ReturnCase:
        raise ValueError("case must be a ReturnCase")
    if complete is not None and not callable(complete):
        raise ValueError("complete must be callable or None")

    def proposers(task, routes, evidence):
        instruction = task.instruction + (
            "\nAssess only this declared snapshot under its fictional merchant policy. Do not infer law, "
            "authenticate a customer, execute a payment, promise arrival times, or produce customer-facing prose. "
            "Dates use YYYY-MM-DD local calendar days; window_days is inclusive. IDs must match the request. "
            "A missing delivered_on must remain unknown; never invent one. Money is exact integer CNY cents. "
            "Return claim as a JSON object encoded inside the candidate claim STRING, without Markdown or prose. "
            f"Stages/actions/targets: {list(zip(ACTIONS, TARGETS))}. Work on the earliest unverified stage. "
            "1. {case_id,order_id,customer_id,policy_id,days_since_delivery,eligible,reason}. Check identity first, "
            "then item_returnable, then days <= window_days. Reasons: request_identity_mismatch, "
            "item_not_returnable, outside_return_window, within_return_window. Only within_return_window is eligible. "
            "2. For eligible requests only: {item_paid_cents,prior_item_refund_cents,excluded_shipping_cents,"
            "refund_cents,currency}. refund_cents=item_paid_cents-prior_item_refund_cents; exclude ALL shipping; "
            "list_price_cents is never the refund basis. "
            "3. {case_id,order_id,customer_id,policy_id,outcome,refund_cents,currency,excluded_shipping_cents,"
            "reason,next_action,payment_executed}. Use requested order/customer IDs. Eligible: outcome="
            "refund_recommended, next_action=merchant_refund_review, amount from verified stage 2. Otherwise "
            "human_review, next_action=manual_policy_review, refund_cents=null. For identity mismatch also "
            "excluded_shipping_cents=null; otherwise it equals shipping_paid_cents. payment_executed is always false. "
            f"Cite every evidence ID and the necessary earlier verified facts {FACT_IDS}. "
            "Reject/defer feedback creates no facts; repair the failed stage before advancing."
        )
        return (ModelProposer(complete if complete is not None else _offline_completion(case),
                              allowed_actions=ACTIONS, evidence=evidence, instruction=instruction),)

    return Agent(domain_factory=lambda task: CustomerSupportDomain(case),
                 verifier_factory=lambda task: CustomerSupportVerifier(case), proposer_factory=proposers,
                 tools=(ReturnCaseTool(case),), max_steps=12, max_no_progress=3)


def demo_case(scenario: str = "refund") -> ReturnCase:
    """Synthetic CNY 249 item plus CNY 10 shipping; no private customer data."""
    case = ReturnCase(case_id="demo-return-001", order_id="demo-order-001", customer_id="demo-customer-001",
                      request_order_id="demo-order-001", request_customer_id="demo-customer-001",
                      snapshot_date="2026-10-08", requested_on="2026-10-08", delivered_on="2026-10-01",
                      item_paid_cents=24900, shipping_paid_cents=1000)
    if scenario == "refund":
        return case
    if scenario == "missing-delivery":
        return replace(case, delivered_on=None)
    if scenario == "expired":
        return replace(case, delivered_on="2026-09-01")
    raise ValueError("scenario must be refund, missing-delivery, or expired")


def run_demo(scenario: str = "refund") -> AgentResult:
    return build_agent(demo_case(scenario)).run(Task(
        "customer-support-demo", "Check this synthetic return request and recommend a bounded next step.", DOMAIN))


def verified_resolution(result: AgentResult) -> dict:
    """Render a completed canonical fact chain; never render partial model prose.

    Raises ValueError on incomplete or inconsistent results. This is an
    integrity check within a trusted Python application, not a signature or
    external proof that an order snapshot is authentic or still current.
    """
    error = "customer support resolution is not verified"
    if type(result) is not AgentResult or result.task.domain != DOMAIN:
        raise ValueError(error)
    run = result.run_result
    if run.status != "solved" or not run.residual.solved:
        raise ValueError(error)
    try:
        values = {item.id: item.value for item in run.evidence}
        case = ReturnCase(**{name: values["return:input:" + name] for name in _CASE_FIELDS},
                          policy=ReturnPolicy(**{name: values["return:policy:" + name] for name in _POLICY_FIELDS}))
        expected = ReturnCaseTool(case).collect(result.task)
        if len(run.evidence) != len(expected) or {item.id: canonical_json(item) for item in run.evidence} != {
                item.id: canonical_json(item) for item in expected}:
            raise ValueError(error)
        previous = _chain(case, run.state)
        if 2 not in previous or not CustomerSupportDomain(case).rebuild(run.state).solved:
            raise ValueError(error)
        return dict(previous[2])
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ValueError(error) from exc
