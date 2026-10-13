"""Tests for the provider-neutral residual architecture extensions."""

import pytest

from resimind.domains.rule_abstraction import (
    AbstractRule,
    VerifiedExample,
    apply_rule,
    extract_rules,
)
from resimind.obligations import Obligation, ObligationGraph, ObligationUpdate
from resimind.policy import StrategyObservation, StrategyPolicy


def test_obligation_graph_exposes_ready_slice_and_transitive_invalidation():
    graph = ObligationGraph(
        (
            Obligation("input", "collect input", evidence=("e1",)),
            Obligation("derive", "derive result", depends_on=("input",)),
            Obligation("report", "prepare report", depends_on=("derive",)),
        )
    )
    assert tuple(item.id for item in graph.ready()) == ("input",)
    assert tuple(item.id for item in graph.blocked()) == ("derive", "report")
    graph = graph.update(ObligationUpdate("input", "verified", "evidence checked"))
    assert tuple(item.id for item in graph.ready()) == ("derive",)
    assert tuple(item.id for item in graph.plan_slice("report")) == ("derive", "report")

    invalidated = graph.invalidate_evidence(("e1",), revision="r2")
    assert tuple(item.id for item in invalidated.pending()) == ("derive", "input", "report")
    assert all(item.status == "invalidated" for item in invalidated.pending())
    assert invalidated.evidence_revision == "r2"


def test_obligation_graph_rejects_missing_dependencies_and_cycles():
    with pytest.raises(ValueError, match="Obligation values"):
        ObligationGraph((object(),))
    with pytest.raises(ValueError, match="missing dependency"):
        ObligationGraph((Obligation("a", "a", depends_on=("missing",)),))
    with pytest.raises(ValueError, match="dependency cycle"):
        ObligationGraph((Obligation("a", "a", depends_on=("b",)),
                         Obligation("b", "b", depends_on=("a",))))


def test_obligation_graph_digest_is_deterministic_and_empty_graph_is_solved():
    left = ObligationGraph((Obligation("a", "a"), Obligation("b", "b")))
    right = ObligationGraph((Obligation("b", "b"), Obligation("a", "a")))
    assert left.digest() == right.digest()
    assert not left.solved
    assert ObligationGraph().solved


def test_rejected_obligation_is_not_treated_as_solved():
    graph = ObligationGraph((Obligation("a", "a"),))
    graph = graph.update(ObligationUpdate("a", "rejected", "candidate failed"))
    assert not graph.solved
    assert tuple(item.id for item in graph.pending()) == ("a",)


def test_strategy_policy_ranks_known_strategies_and_round_trips_frozen_snapshot():
    observations = (
        StrategyObservation("algebra", "symbolic", True, 1),
        StrategyObservation("algebra", "symbolic", True, 1),
        StrategyObservation("algebra", "model", False, 0.5),
        StrategyObservation("algebra", "model", True, 4),
    )
    policy = StrategyPolicy.fit(observations)
    assert policy.frozen
    assert policy.rank("algebra", ("model", "symbolic", "fallback")) == (
        "symbolic", "model", "fallback"
    )
    restored = StrategyPolicy.from_json(policy.to_json())
    assert restored.frozen
    assert restored.fingerprint() == policy.fingerprint()
    with pytest.raises(RuntimeError):
        restored.observe(StrategyObservation("algebra", "new", True, 1))


def test_unseen_policy_strategies_keep_caller_order():
    policy = StrategyPolicy.fit((), min_evidence=1)
    assert policy.rank("new", ("first", "second")) == ("first", "second")


def test_rule_abstraction_requires_two_verified_examples_and_reverifies_application():
    examples = (
        VerifiedExample("(x+y)*z", "x*z+y*z", ("x", "y", "z"), "case-a"),
        VerifiedExample("(p+q)*r", "p*r+q*r", ("p", "q", "r"), "case-b"),
    )
    rule = extract_rules(examples)
    assert len(rule) == 1
    assert isinstance(rule[0], AbstractRule)
    assert apply_rule(rule[0], "(u+v)*w", ("u", "v", "w"), ("u", "v", "w")) == "(u)*(w)+(v)*(w)"
    with pytest.raises(ValueError, match="does not match"):
        apply_rule(rule[0], "(u-v)*w", ("u", "v", "w"), ("u", "v", "w"))


def test_rule_abstraction_does_not_promote_unverified_or_single_source_examples():
    one = VerifiedExample("(x+y)*z", "x*z+y*z", ("x", "y", "z"), "only")
    bad = VerifiedExample("(p+q)*r", "p*r+q*r+1", ("p", "q", "r"), "bad")
    assert extract_rules((one,)) == ()
    assert extract_rules((one, bad)) == ()


def test_rule_abstraction_does_not_erase_subtraction_signs():
    left = VerifiedExample("(x-y)*z", "x*z-y*z", ("x", "y", "z"), "sub-a")
    right = VerifiedExample("(p-q)*r", "p*r-q*r", ("p", "q", "r"), "sub-b")
    assert extract_rules((left, right)) == ()
