"""Offline contract tests: no ChinaTravel data, API, or gold oracle required."""
import json
import unittest

from experiments.chinatravel.resimind_adapter import CheckResult, run_resimind


QUERY = {"query": "Visit two museums", "days": 1}
PLAN = {"days": [{"day": 1, "activities": ["Museum A", "Museum B"]}]}


class AdapterTests(unittest.TestCase):
    def test_reject_then_accept_has_one_atomic_commit_and_feedback(self):
        received = []

        def propose(feedback):
            received.append(feedback)
            return {"draft": len(received), **PLAN}

        result = run_resimind(QUERY, "fixed-sandbox", propose, lambda plan:
                             CheckResult(plan["draft"] == 2, ("inspect itinerary",), {"check": 1}))
        assert result.run_result.status == "solved"
        assert [e.decision.value for e in result.run_result.trace] == ["reject", "accept"]
        assert [e.after.revision for e in result.run_result.trace] == [0, 1]
        assert received[0] is None
        assert received[1]["previous_plan"]["draft"] == 1
        assert received[1]["reasons"] == ["inspect itinerary"]
        assert received[1]["diagnostics"] == {"check": 1}
        assert result.accepted_plan["draft"] == 2
        assert len(result.run_result.state.facts) == 1
        assert json.loads(result.run_result.evidence[0].value) == QUERY
        assert result.run_result.evidence[1].value == "fixed-sandbox"
        assert json.loads(json.dumps(result.to_dict()))["accepted_plan"] == result.accepted_plan

    def test_unaccepted_attempts_never_commit(self):
        for deferred in (False, True):
            with self.subTest(deferred=deferred):
                result = run_resimind(QUERY, "fixed", lambda _: PLAN, lambda _:
                                     CheckResult(False, ("unresolved",), deferred=deferred))
                assert result.run_result.status == "budget_exhausted"
                assert result.run_result.steps == 3
                assert result.run_result.state.facts == ()
                assert result.accepted_plan is None
                assert not result.run_result.residual.solved
                assert all(e.decision.value == ("defer" if deferred else "reject")
                           for e in result.run_result.trace)

    def test_empty_output_stops_and_cannot_succeed(self):
        for empty in (None, {}):
            with self.subTest(empty=empty):
                calls = []

                def propose(feedback):
                    calls.append(feedback)
                    return empty

                def check(_):
                    self.fail("empty proposals must not reach checker")

                result = run_resimind(QUERY, "fixed", propose, check)
                assert len(calls) == result.run_result.steps == 1
                assert result.run_result.status == "deferred"
                assert result.run_result.stop_reason == "no_candidate"
                assert result.accepted_plan is None and not result.run_result.residual.solved

    def test_technical_exception_stops_without_committing(self):
        for where in ("proposer", "verifier", "mutation"):
            with self.subTest(where=where):
                calls = []

                def broken(plan):
                    calls.append(1)
                    if where == "mutation":
                        plan["changed"] = True
                        return CheckResult(True)
                    raise RuntimeError("private failure detail")

                result = run_resimind(QUERY, "fixed", broken if where == "proposer" else lambda _: PLAN,
                                     broken if where != "proposer" else lambda _: CheckResult(True))
                assert calls == [1]
                assert result.run_result.status == "error"
                assert result.run_result.stop_reason == ("proposer_error" if where == "proposer" else "verifier_error")
                assert result.accepted_plan is None and result.run_result.state.facts == ()
                assert "private failure detail" not in json.dumps(result.to_dict())
