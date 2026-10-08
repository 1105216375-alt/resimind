"""Integrity and failure-path tests; these do not prove a domain verifier sound."""
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from residual_agent import (Candidate, Decision, Engine, Evidence, Fact,
                               Residual, State, Verdict)


INPUT = Evidence("e1", "sample", "length", 2, "m", "test-fixture", "run-1")
OTHER_INPUT = Evidence("e2", "other", "length", 999, "m", "test-fixture", "run-1")
GOOD = Candidate("c1", "read_length", "length-known", "length is 2 m", ("e1",))
FACT = Fact("f1", "sample", "length", 2, "m", ("e1",), "run-1")


class Once:
    """Stateless proposal source: returning the same candidate exposes replay bugs."""

    def __init__(self, candidate=GOOD):
        self.candidate = candidate
        self.calls = 0

    def propose(self, state, residual):
        self.calls += 1
        return (self.candidate,)


class LengthDomain:
    def rebuild(self, state):
        found = any(f.subject == "sample" and f.metric == "length" and
                    f.value == 2 and f.unit == "m" and f.scope == "run-1"
                    for f in state.facts)
        return Residual() if found else Residual(unknowns=("length-known",))


class ExactLengthVerifier:
    """A deliberately narrow, deterministic domain verifier."""

    def __init__(self):
        self.calls = 0

    def verify(self, candidate, state, residual, evidence):
        self.calls += 1
        inputs = {item.id: item for item in evidence}
        supported = (
            candidate.action == "read_length"
            and candidate.claim == "length is 2 m"
            and candidate.target == "length-known"
            and "e1" in candidate.refs
            and inputs.get("e1") == INPUT
        )
        return Verdict.for_candidate(
            candidate, state, Decision.ACCEPT if supported else Decision.REJECT,
            evidence=evidence, facts=(FACT,) if supported else (),
            reasons=() if supported else ("unsupported_claim",),
        )


class FixedVerifier:
    def __init__(self, decision=Decision.ACCEPT, facts=(FACT,), change=None):
        self.decision, self.facts, self.change = decision, facts, change

    def verify(self, candidate, state, residual, evidence):
        verdict = Verdict.for_candidate(candidate, state, self.decision,
                                        evidence=evidence, facts=self.facts)
        return self.change(verdict) if self.change else verdict


class FixedDomain:
    def __init__(self, residual):
        self.residual = residual

    def rebuild(self, state):
        return self.residual


def engine(*, candidate=GOOD, verifier=None, domain=None, evidence=(INPUT,),
           proposers=None, max_steps=3, max_no_progress=3):
    return Engine(domain or LengthDomain(), verifier or ExactLengthVerifier(),
                  (Once(candidate),) if proposers is None else proposers,
                  evidence=evidence, max_steps=max_steps,
                  max_no_progress=max_no_progress)


class RuntimeTests(unittest.TestCase):
    def test_verified_fact_is_committed_and_domain_completes(self):
        result = engine().run()
        self.assertEqual(result.status, "solved")
        self.assertEqual(result.state, State(1, (FACT,)))
        self.assertEqual(result.steps, 1)
        event = result.trace[0]
        self.assertEqual(event.before, State())
        self.assertEqual(event.after, result.state)
        self.assertEqual(event.decision, Decision.ACCEPT)

    def test_direct_solution_action_has_no_bypass(self):
        malicious = replace(GOOD, action="direct_solution", claim="length is 999 m")
        verifier = ExactLengthVerifier()
        result = engine(candidate=malicious, verifier=verifier, max_steps=1).run()
        self.assertEqual(verifier.calls, 1)
        self.assertEqual(result.trace[0].decision, Decision.REJECT)
        self.assertEqual(result.state, State())
        self.assertFalse(result.residual.solved)

    def test_candidate_cannot_claim_verified_or_solved(self):
        with self.assertRaises(TypeError):
            Candidate("bad", "direct_solution", "length-known", "999", verified=True)
        with self.assertRaises(TypeError):
            Candidate("bad", "direct_solution", "length-known", "999", solved=True)

    def test_binding_includes_candidate_content_not_just_id(self):
        def stale(verdict):
            different = replace(GOOD, claim="different claim, same id")
            return replace(verdict, binding=Verdict.for_candidate(
                different, State(), Decision.ACCEPT, evidence=(INPUT,)).binding)
        result = engine(verifier=FixedVerifier(change=stale), max_steps=1).run()
        self.assertEqual(result.state, State())
        self.assertIn("verdict_binding_mismatch", result.trace[0].reasons)

    def test_binding_includes_state_revision_and_content(self):
        states = (State(3), State(0, (FACT,)))
        for wrong_state in states:
            with self.subTest(state=wrong_state):
                def stale(verdict):
                    binding = Verdict.for_candidate(GOOD, wrong_state, Decision.ACCEPT,
                                                    evidence=(INPUT,)).binding
                    return replace(verdict, binding=binding)
                result = engine(verifier=FixedVerifier(change=stale), max_steps=1).run()
                self.assertEqual(result.state, State())
                self.assertIn("verdict_binding_mismatch", result.trace[0].reasons)

    def test_binding_includes_evidence_contents(self):
        def stale(verdict):
            evidence = (replace(INPUT, value=999),)
            binding = Verdict.for_candidate(GOOD, State(), Decision.ACCEPT,
                                            evidence=evidence).binding
            return replace(verdict, binding=binding)
        result = engine(verifier=FixedVerifier(change=stale), max_steps=1).run()
        self.assertEqual(result.state, State())
        self.assertIn("verdict_binding_mismatch", result.trace[0].reasons)

    def test_unknown_candidate_references_fail_before_verifier(self):
        verifier = ExactLengthVerifier()
        result = engine(candidate=replace(GOOD, refs=("unregistered",)),
                        verifier=verifier, max_steps=1).run()
        self.assertEqual(verifier.calls, 0)
        self.assertEqual(result.state, State())
        self.assertIn("unknown_reference", result.trace[0].reasons)

    def test_target_must_be_an_outstanding_obligation(self):
        verifier = ExactLengthVerifier()
        result = engine(candidate=replace(GOOD, target="made-up"),
                        verifier=verifier, max_steps=1).run()
        self.assertEqual(verifier.calls, 0)
        self.assertIn("unknown_target", result.trace[0].reasons)

    def test_fact_cannot_reference_unknown_evidence(self):
        fact = replace(FACT, evidence_refs=("missing",))
        result = engine(verifier=FixedVerifier(facts=(fact,)), max_steps=1).run()
        self.assertEqual(result.state, State())
        self.assertIn("unknown_evidence_reference", result.trace[0].reasons)

    def test_fact_cannot_graft_unreferenced_input(self):
        fact = replace(FACT, evidence_refs=("e2",))
        result = engine(verifier=FixedVerifier(facts=(fact,)),
                        evidence=(INPUT, OTHER_INPUT), max_steps=1).run()
        self.assertEqual(result.state, State())
        self.assertIn("evidence_not_bound_to_candidate", result.trace[0].reasons)

    def test_fact_references_can_resolve_transitive_provenance(self):
        derived = Fact("f2", "sample", "double_length", 4, "m", ("e1",), "run-1")
        candidate = replace(GOOD, refs=("f1",), target="double-known")
        class DerivedDomain:
            def rebuild(self, state):
                return Residual() if any(f.id == "f2" for f in state.facts) else Residual(goals=("double-known",))
        result = engine(candidate=candidate, domain=DerivedDomain(),
                        verifier=FixedVerifier(facts=(derived,))).run(State(1, (FACT,)))
        self.assertEqual(result.status, "solved")
        self.assertEqual(result.state.facts, (FACT, derived))
        self.assertEqual(result.state.revision, 2)

    def test_reject_defer_interrupt_never_commit(self):
        for decision in (Decision.REJECT, Decision.DEFER, Decision.INTERRUPT):
            with self.subTest(decision=decision):
                result = engine(verifier=FixedVerifier(decision, ()), max_steps=1).run()
                self.assertEqual(result.state, State())
                self.assertEqual(result.trace[0].decision, decision)
                self.assertFalse(result.residual.solved)
                if decision is Decision.INTERRUPT:
                    self.assertEqual(result.status, "interrupted")

    def test_nonaccept_verdict_cannot_smuggle_facts(self):
        result = engine(verifier=FixedVerifier(Decision.DEFER, (FACT,)), max_steps=1).run()
        self.assertEqual(result.state, State())
        self.assertIn("nonaccept_verdict_contains_facts", result.trace[0].reasons)

    def test_conflicting_batch_is_atomic(self):
        conflict = replace(FACT, id="f2", value=999)
        result = engine(verifier=FixedVerifier(facts=(FACT, conflict)), max_steps=1).run()
        self.assertEqual(result.state, State())
        self.assertIn("fact_value_or_unit_conflict", result.trace[0].reasons)

    def test_existing_fact_cannot_be_overwritten(self):
        existing = replace(FACT, value=1)
        result = engine(verifier=FixedVerifier(), max_steps=1).run(State(4, (existing,)))
        self.assertEqual(result.state, State(4, (existing,)))
        self.assertIn("fact_id_conflict", result.trace[0].reasons)

    def test_units_and_boolean_number_conflicts_are_not_silently_merged(self):
        for conflict in (replace(FACT, id="f2", unit="cm"),
                         replace(FACT, id="f2", value=True)):
            with self.subTest(conflict=conflict):
                result = engine(verifier=FixedVerifier(facts=(FACT, conflict)), max_steps=1).run()
                self.assertEqual(result.state, State())
                self.assertIn("fact_value_or_unit_conflict", result.trace[0].reasons)
        one = replace(FACT, value=1)
        boolean = replace(FACT, id="f2", value=True)
        result = engine(verifier=FixedVerifier(facts=(one, boolean)), max_steps=1).run()
        self.assertEqual(result.state, State())
        self.assertIn("fact_value_or_unit_conflict", result.trace[0].reasons)

    def test_fact_id_cannot_overlap_input_id(self):
        result = engine(verifier=FixedVerifier(facts=(replace(FACT, id="e1"),)), max_steps=1).run()
        self.assertEqual(result.state, State())
        self.assertIn("fact_id_overlaps_evidence", result.trace[0].reasons)

    def test_duplicate_fact_commit_does_not_advance_revision(self):
        pending = Residual(unknowns=("length-known",))
        result = engine(verifier=FixedVerifier(), domain=FixedDomain(pending), max_steps=2,
                        max_no_progress=3).run(State(7, (FACT,)))
        self.assertEqual(result.state, State(7, (FACT,)))
        self.assertEqual(result.status, "budget_exhausted")

    def test_domain_exception_rolls_back_prospective_commit(self):
        class FailingDomain:
            def rebuild(self, state):
                if state.facts:
                    raise RuntimeError("failure after facts were proposed")
                return Residual(unknowns=("length-known",))
        result = engine(domain=FailingDomain()).run()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.stop_reason, "domain_error")
        self.assertEqual(result.state, State())
        self.assertEqual(result.trace[0].after, result.trace[0].before)
        self.assertEqual(result.trace[0].decision, Decision.INTERRUPT)

    def test_initial_domain_exception_cannot_report_solved(self):
        class BrokenDomain:
            def rebuild(self, state):
                raise RuntimeError("no residual")
        result = engine(domain=BrokenDomain()).run()
        self.assertEqual(result.status, "error")
        self.assertFalse(result.residual.solved)
        self.assertEqual(result.steps, 0)

    def test_invalid_domain_result_is_closed(self):
        result = engine(domain=FixedDomain({"solved": True})).run()
        self.assertEqual(result.status, "error")
        self.assertFalse(result.residual.solved)

    def test_verifier_and_proposer_exceptions_do_not_commit(self):
        class BrokenVerifier:
            def verify(self, *args):
                raise RuntimeError("broken")
        class BrokenProposer:
            def propose(self, *args):
                raise RuntimeError("broken")
        for runtime, reason in ((engine(verifier=BrokenVerifier()), "verifier_error"),
                                (engine(proposers=(BrokenProposer(),)), "proposer_error")):
            with self.subTest(reason=reason):
                result = runtime.run()
                self.assertEqual(result.status, "error")
                self.assertEqual(result.stop_reason, reason)
                self.assertEqual(result.state, State())
                self.assertEqual(result.steps, 1)

    def test_arbitrary_verifier_output_fails_closed(self):
        class BrokenVerifier:
            def verify(self, *args):
                return {"decision": "accept", "safe": True, "verified": True}
        result = engine(verifier=BrokenVerifier()).run()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.state, State())

    def test_hard_constraints_block_completion(self):
        class ConstrainedDomain:
            def rebuild(self, state):
                unknowns = () if state.facts else ("length-known",)
                return Residual(unknowns=unknowns, hard_constraints=("safety-review",))
        result = engine(domain=ConstrainedDomain(), max_steps=1).run()
        self.assertEqual(result.state.facts, (FACT,))
        self.assertFalse(result.residual.solved)
        self.assertEqual(result.residual.hard_constraints, ("safety-review",))
        self.assertEqual(result.status, "budget_exhausted")

    def test_repeated_accepts_without_residual_progress_stall(self):
        pending = Residual(unknowns=("length-known",))
        result = engine(domain=FixedDomain(pending), max_steps=9, max_no_progress=2).run()
        self.assertEqual(result.status, "stalled")
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.state.revision, 1)
        self.assertTrue(all(event.decision is Decision.ACCEPT for event in result.trace))

    def test_empty_proposals_consume_attempt_budget(self):
        class Empty:
            def propose(self, state, residual):
                return ()
        result = engine(proposers=(Empty(),), max_steps=2, max_no_progress=5).run()
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.status, "budget_exhausted")
        self.assertTrue(all(event.decision is Decision.DEFER for event in result.trace))

    def test_rejected_attempts_consume_budget(self):
        result = engine(candidate=replace(GOOD, claim="unsupported"), max_steps=2,
                        max_no_progress=5).run()
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(result.state.revision, 0)

    def test_round_robin_gives_another_proposer_a_turn(self):
        bad = Once(replace(GOOD, id="bad", claim="unsupported"))
        good = Once(GOOD)
        result = engine(proposers=(bad, good)).run()
        self.assertEqual(result.status, "solved")
        self.assertEqual((bad.calls, good.calls), (1, 1))
        self.assertEqual([event.decision for event in result.trace],
                         [Decision.REJECT, Decision.ACCEPT])

    def test_observer_receives_rejection_and_repairs_the_next_proposal(self):
        class FeedbackProposer:
            def __init__(self):
                self.feedback = []

            def propose(self, state, residual):
                # The correction depends on the actual verifier feedback.
                if self.feedback and "unsupported_claim" in self.feedback[-1].reasons:
                    return (GOOD,)
                return (replace(GOOD, claim="length is 999 m"),)

            def observe(self, event):
                self.feedback.append(event)

        proposer = FeedbackProposer()
        result = engine(proposers=(proposer,)).run()
        self.assertEqual(result.status, "solved")
        self.assertEqual(result.steps, 2)
        self.assertEqual(proposer.feedback, list(result.trace))
        self.assertEqual(proposer.feedback[0].decision, Decision.REJECT)
        self.assertEqual(proposer.feedback[0].reasons, ("unsupported_claim",))
        self.assertEqual(proposer.feedback[1].decision, Decision.ACCEPT)

    def test_observer_receives_defer_and_empty_attempts_only_for_its_proposer(self):
        class ObservingProposer:
            def __init__(self, candidates):
                self.candidates = candidates
                self.feedback = []

            def propose(self, state, residual):
                return self.candidates

            def observe(self, event):
                self.feedback.append(event)

        empty = ObservingProposer(())
        deferred = ObservingProposer((GOOD,))
        result = engine(proposers=(empty, deferred),
                        verifier=FixedVerifier(Decision.DEFER, ()),
                        max_steps=2, max_no_progress=3).run()
        self.assertEqual(result.status, "budget_exhausted")
        self.assertEqual(empty.feedback, [result.trace[0]])
        self.assertEqual(deferred.feedback, [result.trace[1]])
        self.assertEqual(empty.feedback[0].reasons, ("no_candidate",))
        self.assertEqual(deferred.feedback[0].decision, Decision.DEFER)

    def test_observer_failure_preserves_committed_state_and_records_diagnostic(self):
        class FailingObserver(Once):
            def observe(self, event):
                self.observed = event
                raise RuntimeError("private diagnostic text must not be copied")

        proposer = FailingObserver()
        result = engine(proposers=(proposer,)).run()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.stop_reason, "observer_error")
        self.assertEqual(result.steps, 1)
        self.assertEqual(result.state, State(1, (FACT,)))
        self.assertTrue(result.residual.solved)
        attempt, diagnostic = result.trace
        self.assertEqual(attempt.decision, Decision.ACCEPT)
        self.assertEqual(proposer.observed, attempt)
        self.assertEqual(diagnostic.step, 0)
        self.assertEqual(diagnostic.decision, Decision.INTERRUPT)
        self.assertEqual(diagnostic.reasons, ("observer_error", "RuntimeError"))
        self.assertEqual(diagnostic.before, result.state)
        self.assertEqual(diagnostic.after, result.state)
        self.assertNotIn("private diagnostic text", result.to_json())

    def test_observer_failure_after_rejection_never_creates_facts(self):
        class FailingObserver(Once):
            def observe(self, event):
                raise RuntimeError("failed to consume rejection")

        proposer = FailingObserver(replace(GOOD, claim="length is 999 m"))
        result = engine(proposers=(proposer,)).run()
        self.assertEqual(result.stop_reason, "observer_error")
        self.assertEqual(result.state, State())
        self.assertEqual(result.trace[0].decision, Decision.REJECT)
        self.assertEqual(result.trace[-1].before, result.trace[-1].after)

    def test_runtime_can_be_reused_without_trace_or_budget_carryover(self):
        runtime = engine()
        first, second = runtime.run(), runtime.run()
        self.assertEqual(first, second)

    def test_snapshots_and_json_export_do_not_alias_mutable_data(self):
        result = engine().run()
        with self.assertRaises(FrozenInstanceError):
            result.state.revision = 99
        with self.assertRaises(FrozenInstanceError):
            result.trace[0].candidate.claim = "tampered"
        data = result.to_dict()
        data["state"]["facts"][0]["value"] = 999
        data["trace"][0]["candidate"]["refs"].append("unregistered")
        self.assertEqual(result.state.facts[0].value, 2)
        self.assertEqual(result.trace[0].candidate.refs, ("e1",))
        decoded = json.loads(result.to_json())
        self.assertEqual(decoded["trace"][0]["decision"], "accept")
        self.assertEqual(decoded["state"]["revision"], 1)
        self.assertEqual(decoded["evidence"][0]["source"], "test-fixture")

    def test_mutable_values_nonfinite_numbers_and_mutable_refs_are_rejected(self):
        for value in ({"key": 1}, [1], float("nan"), float("inf"), (1, [2])):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                replace(INPUT, value=value)
        with self.assertRaises(ValueError):
            replace(GOOD, refs=["e1"])
        with self.assertRaises(ValueError):
            replace(FACT, evidence_refs=())
        self.assertEqual(replace(INPUT, value=(1, "two", (3, None))).to_dict()["value"],
                         [1, "two", [3, None]])

    def test_invalid_registry_initial_state_and_config_rejected(self):
        with self.assertRaises(ValueError):
            engine(evidence=(INPUT, INPUT))
        with self.assertRaises(ValueError):
            engine().run(State(facts=(replace(FACT, evidence_refs=("missing",)),)))
        with self.assertRaises(ValueError):
            engine().run(State(facts=(FACT, replace(FACT, id="f2", value=999))))
        for kwargs in ({"max_steps": 0}, {"max_no_progress": 0}, {"max_steps": True}, {"proposers": ()}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                engine(**kwargs)
        with self.assertRaises(ValueError):
            Residual(goals=("same",), unknowns=("same",))

    def test_empty_residual_is_solved_by_domain_contract_without_attempts(self):
        result = engine(domain=FixedDomain(Residual())).run()
        self.assertEqual(result.status, "solved")
        self.assertEqual(result.steps, 0)
        self.assertEqual(result.state, State())


if __name__ == "__main__":
    unittest.main()
