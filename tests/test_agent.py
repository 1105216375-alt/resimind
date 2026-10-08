"""End-to-end task/tool/model/verifier tests without network or model credentials."""

from dataclasses import FrozenInstanceError
import json
import unittest

from residual_agent.adapters import ModelProposer, ModelResponseError
from residual_agent.agent import Agent, AgentResult, Task, ToolEvent
from residual_agent.core import Candidate, Decision, Evidence, Fact, Residual, State, Verdict
from residual_agent.memory import Route, RouteMemory


class StockTool:
    name = "stock_reader"

    def __init__(self, records=None):
        self.calls = []
        self.records = records if records is not None else (
            Evidence("stock-1", "item-A", "stock", 8, "units", "synthetic-fixture"),
        )

    def collect(self, task):
        self.calls.append(task)
        return self.records


class StockDomain:
    def rebuild(self, state):
        present = any(fact.subject == "item-A" and fact.metric == "stock" for fact in state.facts)
        return Residual() if present else Residual(goals=("verify-stock",))


class StockVerifier:
    """Verify the claim by exact subject/metric/unit/scope and value binding."""

    def __init__(self):
        self.calls = []

    def verify(self, candidate, state, residual, evidence):
        self.calls.append((candidate, state, residual, evidence))
        rows = [row for row in evidence if row.id in candidate.refs
                and row.subject == "item-A" and row.metric == "stock"
                and row.unit == "units" and row.scope == "default"]
        if candidate.action != "verify_stock" or len(rows) != 1 or candidate.claim != f"item-A has {rows[0].value} units":
            return Verdict.for_candidate(
                candidate, state, Decision.REJECT, evidence=evidence, reasons=("unsupported_claim",),
            )
        row = rows[0]
        fact = Fact("fact-1", row.subject, row.metric, row.value, row.unit, (row.id,), row.scope)
        return Verdict.for_candidate(candidate, state, Decision.ACCEPT, evidence=evidence, facts=(fact,))


def response(**changes):
    candidate = {
        "id": "proposal-1", "action": "verify_stock", "target": "verify-stock",
        "claim": "item-A has 8 units", "refs": ["stock-1"],
    }
    candidate.update(changes)
    return json.dumps(candidate)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.task = Task("task-1", "Verify the current stock of item-A", "inventory", (("mode", "audit"),))

    def build_agent(self, *, complete=None, tools=None, memory=None, **budgets):
        self.verifier = StockVerifier()
        self.prompts = []
        self.factory_calls = []

        def model(prompt):
            self.prompts.append(json.loads(prompt))
            return response() if complete is None else complete(prompt)

        def proposers(task, routes, evidence):
            self.factory_calls.append((task, routes, evidence))
            return (ModelProposer(model, allowed_actions=("verify_stock",), evidence=evidence,
                                  instruction=task.instruction),)

        return Agent(
            domain_factory=lambda task: StockDomain(), verifier_factory=lambda task: self.verifier,
            proposer_factory=proposers, tools=(StockTool(),) if tools is None else tools,
            memory=memory, **budgets,
        )

    def test_task_tool_model_verifier_commit_end_to_end(self):
        tool = StockTool()
        agent = self.build_agent(tools=(tool,))
        result = agent.run(self.task)
        self.assertEqual(result.task, self.task)
        self.assertEqual(result.run_result.status, "solved")
        self.assertEqual(result.run_result.steps, 1)
        self.assertEqual(result.run_result.state.facts[0].value, 8)
        self.assertEqual(result.run_result.state.facts[0].evidence_refs, ("stock-1",))
        self.assertTrue(result.run_result.residual.solved)
        self.assertEqual(tool.calls, [self.task])
        self.assertEqual(result.tool_events[0].evidence, tool.records)
        self.assertEqual(len(self.verifier.calls), 1)
        self.assertEqual(self.verifier.calls[0][3], tool.records)
        self.assertEqual(self.factory_calls[0], (self.task, (), tool.records))
        prompt = self.prompts[0]
        self.assertEqual(prompt["task_instruction"], self.task.instruction)
        self.assertEqual(prompt["state"]["facts"], [])
        self.assertEqual(prompt["residual"]["goals"], ["verify-stock"])
        self.assertEqual(prompt["available_evidence_ids"], ["stock-1"])
        self.assertEqual(prompt["candidate_schema"]["properties"]["claim"]["type"], "string")

    def test_schema_valid_model_claim_still_requires_semantic_verification(self):
        result = self.build_agent(complete=lambda _: response(claim="item-A has 999 units"), max_no_progress=1).run(self.task)
        self.assertEqual(result.run_result.status, "stalled")
        self.assertEqual(result.run_result.state.facts, ())
        self.assertEqual(result.run_result.trace[0].decision, Decision.REJECT)
        self.assertEqual(len(self.verifier.calls), 1)
        self.assertFalse(result.run_result.residual.solved)

    def test_model_uses_real_verifier_feedback_to_correct_a_rejected_claim(self):
        def model(prompt):
            data = json.loads(prompt)
            feedback = data["last_feedback"]
            if feedback is None:
                return response(claim="item-A has 999 units")
            self.assertEqual(feedback["decision"], "reject")
            self.assertEqual(feedback["reasons"], ["unsupported_claim"])
            self.assertEqual(feedback["candidate"]["claim"], "item-A has 999 units")
            self.assertEqual(feedback["before_revision"], 0)
            self.assertEqual(feedback["after_revision"], 0)
            return response(id="corrected-proposal")

        agent = self.build_agent(complete=model, max_steps=3, max_no_progress=2)
        result = agent.run(self.task)
        self.assertEqual(result.run_result.status, "solved")
        self.assertEqual(result.run_result.steps, 2)
        self.assertEqual([event.decision for event in result.run_result.trace], [Decision.REJECT, Decision.ACCEPT])
        self.assertEqual(result.run_result.state.facts[0].value, 8)
        self.assertEqual(len(self.verifier.calls), 2)
        # A second task run constructs a new proposer: old feedback cannot turn
        # the first model call into an apparent already-verified continuation.
        again = agent.run(self.task)
        self.assertEqual(again.run_result.steps, 2)
        self.assertIsNone(self.prompts[0]["last_feedback"])
        self.assertIsNone(self.prompts[2]["last_feedback"])

    def test_only_approved_matching_routes_reach_factory_and_never_create_facts(self):
        memory = RouteMemory(("verify_stock",))
        route = Route("route-1", "inventory", (("mode", "audit"),), ("verify_stock",), ("stock",))
        memory.add(route)
        agent = self.build_agent(memory=memory, complete=lambda _: "null", max_no_progress=1)
        self.assertEqual(agent.run(self.task).routes, ())
        memory.review("route-1", reviewer="maintainer", reason="Checked synthetic route")
        result = agent.run(self.task)
        self.assertEqual(result.routes, (route,))
        self.assertEqual(self.factory_calls[-1][1], (route,))
        self.assertEqual(result.run_result.state.facts, ())
        self.assertEqual(result.run_result.status, "stalled")
        memory.revoke("route-1", reviewer="maintainer", reason="Superseded")
        self.assertEqual(agent.run(self.task).routes, ())

    def test_model_protocol_errors_abort_without_verifier_or_facts(self):
        responses = (
            "not JSON", "```json\n{}\n```", "[]", "123", "true", "{}",
            response(verified=True), response(decision="accept"), response(facts=[]),
            response(action="run_arbitrary_code"), response(claim=8),
            response(refs="stock-1"), response(refs=["stock-1", "stock-1"]),
            response().replace('"id": "proposal-1"', '"id": "proposal-1", "id": "duplicate"'),
            response().replace('"claim": "item-A has 8 units"', '"claim": NaN'),
        )
        for text in responses:
            with self.subTest(response=text):
                agent = self.build_agent(complete=lambda _, text=text: text)
                result = agent.run(self.task)
                self.assertEqual(result.run_result.status, "error")
                self.assertEqual(result.run_result.stop_reason, "proposer_error")
                self.assertEqual(result.run_result.state.facts, ())
                self.assertEqual(self.verifier.calls, [])

    def test_unknown_reference_and_target_are_rejected_by_engine(self):
        for update, reason in (({"refs": ["invented"]}, "unknown_reference"),
                               ({"target": "already-solved"}, "unknown_target")):
            with self.subTest(update=update):
                result = self.build_agent(complete=lambda _, update=update: response(**update), max_no_progress=1).run(self.task)
                self.assertEqual(result.run_result.state.facts, ())
                self.assertEqual(result.run_result.trace[0].reasons, (reason,))
                self.assertEqual(self.verifier.calls, [])

    def test_tool_exception_aborts_before_factories_and_preserves_only_evidence_audit(self):
        class BrokenTool:
            name = "broken_tool"

            def collect(self, task):
                raise RuntimeError("secret-token-never-copy-this-message")

        first, last = StockTool(), StockTool()
        last.name = "last_tool"
        agent = self.build_agent(tools=(first, BrokenTool(), last))
        result = agent.run(self.task)
        self.assertEqual(result.run_result.status, "error")
        self.assertEqual(result.run_result.stop_reason, "tool_error")
        self.assertEqual(result.run_result.state.facts, ())
        self.assertFalse(result.run_result.residual.solved)
        self.assertEqual([event.status for event in result.tool_events], ["collected", "error"])
        self.assertEqual(result.tool_events[-1].error, "RuntimeError")
        self.assertEqual(result.run_result.evidence, first.records)
        self.assertNotIn("secret-token", result.to_json())
        self.assertEqual(last.calls, [])
        self.assertEqual(self.factory_calls, [])
        self.assertEqual(self.prompts, [])

    def test_malformed_tool_output_and_duplicate_ids_fail_closed(self):
        row = StockTool().records[0]
        second = StockTool((row,))
        second.name = "another_tool"
        cases = (
            (StockTool([row]),), (StockTool(("untyped",)),),
            (StockTool((row, row)),), (StockTool((row,)), second),
        )
        for tools in cases:
            with self.subTest(tools=tools):
                result = self.build_agent(tools=tools).run(self.task)
                self.assertEqual(result.run_result.stop_reason, "tool_error")
                self.assertEqual(result.run_result.state.facts, ())
                self.assertEqual(self.factory_calls, [])

    def test_factories_are_task_scoped_and_errors_leave_no_verified_state(self):
        agent = self.build_agent()

        def broken(task):
            raise RuntimeError("factory failed")

        agent.domain_factory = broken
        result = agent.run(self.task)
        self.assertEqual(result.run_result.stop_reason, "agent_setup_error")
        self.assertEqual(result.run_result.state.facts, ())
        self.assertFalse(result.run_result.residual.solved)

    def test_each_run_recollects_evidence_and_starts_with_empty_state(self):
        tool = StockTool()
        agent = self.build_agent(tools=(tool,))
        first = agent.run(self.task)
        second = agent.run(self.task)
        self.assertEqual(len(tool.calls), 2)
        self.assertEqual(first.run_result.state.revision, 1)
        self.assertEqual(second.run_result.state.revision, 1)
        self.assertEqual(len(second.run_result.state.facts), 1)
        self.assertEqual(self.prompts[1]["state"]["facts"], [])
        self.assertIsNone(self.prompts[1]["last_feedback"])

    def test_result_json_is_detached_and_contains_no_free_model_answer(self):
        result = self.build_agent().run(self.task)
        document = result.to_dict()
        self.assertEqual(json.loads(result.to_json()), document)
        self.assertEqual(set(document), {"task", "run_result", "tool_events", "routes"})
        document["run_result"]["state"]["facts"][0]["value"] = 999
        document["tool_events"][0]["evidence"][0]["value"] = 999
        self.assertEqual(result.run_result.state.facts[0].value, 8)
        self.assertEqual(result.tool_events[0].evidence[0].value, 8)

    def test_step_budget_and_no_progress_budget_reach_engine(self):
        result = self.build_agent(complete=lambda _: "null", max_steps=2, max_no_progress=3).run(self.task)
        self.assertEqual(result.run_result.stop_reason, "max_steps")
        self.assertEqual(result.run_result.steps, 2)

    def test_task_rejects_mutable_context_and_remains_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            self.task.instruction = "changed"
        invalid = (
            {"context": [("mode", "audit")]},
            {"context": (("mode", "audit"), ("mode", "different"))},
            {"context": (("mode", 1),)}, {"id": ""}, {"domain": " inventory"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    Task(**{"id": "t", "instruction": "task", "domain": "inventory", **changes})

    def test_public_result_contracts_reject_mutable_or_inconsistent_fields(self):
        result = self.build_agent().run(self.task)
        with self.assertRaises(ValueError):
            AgentResult(self.task, result.run_result, list(result.tool_events))
        with self.assertRaises(ValueError):
            AgentResult(self.task, result.run_result, routes=[])
        invalid = (
            {"evidence": list(StockTool().records)},
            {"status": "invented"}, {"error": "RuntimeError"},
            {"status": "error", "error": None},
            {"status": "error", "error": "RuntimeError", "evidence": StockTool().records},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    ToolEvent(**{"tool": "test", "status": "collected", **changes})

    def test_agent_rejects_invalid_registration(self):
        factories = {
            "domain_factory": lambda task: StockDomain(),
            "verifier_factory": lambda task: StockVerifier(),
            "proposer_factory": lambda task, routes, evidence: (),
        }
        for changes in ({"tools": [StockTool()]}, {"tools": (StockTool(), StockTool())},
                        {"max_steps": 0}, {"max_no_progress": True}, {"domain_factory": None}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    Agent(**{**factories, **changes})


class ModelProposerTests(unittest.TestCase):
    def test_observer_requires_immutable_trace_event(self):
        proposer = ModelProposer(lambda _: "null", allowed_actions=("verify_stock",))
        with self.assertRaises(ValueError):
            proposer.observe({"decision": "accept", "verified": True})

    def test_no_candidate_and_model_exception(self):
        state, residual = State(), Residual(goals=("verify-stock",))
        proposer = ModelProposer(lambda _: "null", allowed_actions=("verify_stock",))
        self.assertEqual(proposer.propose(state, residual), ())
        proposer.complete = lambda _: {"id": "not a string"}
        with self.assertRaises(ModelResponseError):
            proposer.propose(state, residual)

    def test_adapter_does_not_execute_claims(self):
        claim = "__import__('os').system('never execute model text')"
        proposer = ModelProposer(lambda _: response(claim=claim), allowed_actions=("verify_stock",))
        candidates = proposer.propose(State(), Residual(goals=("verify-stock",)))
        self.assertEqual(candidates[0].claim, claim)
        self.assertIs(type(candidates[0]), Candidate)

    def test_configuration_requires_registered_actions_and_unique_typed_evidence(self):
        row = StockTool().records[0]
        invalid = (
            {"allowed_actions": "verify_stock"}, {"allowed_actions": ()},
            {"allowed_actions": ("verify_stock", "verify_stock")},
            {"allowed_actions": ("",)}, {"evidence": [row]},
            {"evidence": (row, row)}, {"instruction": None},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    ModelProposer(lambda _: "null", **{"allowed_actions": ("verify_stock",), **changes})


if __name__ == "__main__":
    unittest.main()
