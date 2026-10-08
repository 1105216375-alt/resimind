"""Offline tests of route approval, applicability gates, and data isolation."""

from dataclasses import FrozenInstanceError, fields
import json
import unittest

from residual_agent.memory import Route, RouteMemory


def sample_route(route_id="inventory-v1", **changes):
    values = {
        "route_id": route_id,
        "domain": "inventory",
        "context": (("mode", "reorder"), ("location", "warehouse")),
        "actions": ("read_stock", "compare_threshold"),
        "required_metrics": ("stock", "reorder_point"),
    }
    values.update(changes)
    return Route(**values)


class RouteMemoryTests(unittest.TestCase):
    def setUp(self):
        self.memory = RouteMemory(("read_stock", "compare_threshold", "request_review"))
        self.query = {
            "domain": "inventory",
            "context": {"mode": "reorder", "location": "warehouse", "extra": "allowed"},
            "available_metrics": ("stock", "reorder_point"),
        }

    def approve(self, route=None):
        route = route or sample_route()
        self.memory.add(route)
        self.memory.review(route.route_id, reviewer="maintainer", reason="Checked with synthetic data")
        return route

    def test_pending_routes_do_not_match_and_review_enables_suggestion(self):
        route = sample_route()
        self.memory.add(route)
        self.assertEqual(self.memory.status(route.route_id), "pending")
        self.assertEqual(self.memory.suggest(**self.query), ())
        self.memory.review(route.route_id, reviewer="reviewer", reason="Action sequence checked")
        self.assertEqual(self.memory.status(route.route_id), "approved")
        self.assertEqual(self.memory.suggest(**self.query), (route,))

    def test_all_hard_gates_must_match(self):
        self.approve()
        cases = (
            {"domain": "different-domain"},
            {"context": {"mode": "reorder"}},
            {"context": {"mode": "audit", "location": "warehouse"}},
            {"context": {"mode": "reorder", "location": "store"}},
            {"available_metrics": ("stock",)},
            {"available_metrics": ()},
        )
        for change in cases:
            with self.subTest(change=change):
                self.assertEqual(self.memory.suggest(**{**self.query, **change}), ())

    def test_revocation_takes_effect_immediately_and_is_terminal(self):
        route = self.approve()
        self.memory.revoke(route.route_id, reviewer="maintainer", reason="Found an invalid precondition")
        self.assertEqual(self.memory.status(route.route_id), "revoked")
        self.assertEqual(self.memory.suggest(**self.query), ())
        with self.assertRaises(ValueError):
            self.memory.review(route.route_id, reviewer="maintainer", reason="Cannot resurrect this ID")
        with self.assertRaises(ValueError):
            self.memory.revoke(route.route_id, reviewer="maintainer", reason="Already revoked")
        self.assertEqual(len(self.memory.audit_log()), 3)
        replacement = self.approve(sample_route("inventory-v2"))
        self.assertEqual(self.memory.suggest(**self.query), (replacement,))

    def test_pending_route_can_be_rejected_by_revocation(self):
        route = sample_route()
        self.memory.add(route)
        self.memory.revoke(route.route_id, reviewer="reviewer", reason="Route is not applicable")
        self.assertEqual(self.memory.status(route.route_id), "revoked")
        self.assertEqual(self.memory.suggest(**self.query), ())

    def test_route_ids_cannot_be_overwritten_or_double_approved(self):
        route = self.approve()
        with self.assertRaises(ValueError):
            self.memory.add(sample_route(actions=("request_review",)))
        with self.assertRaises(ValueError):
            self.memory.review(route.route_id, reviewer="reviewer", reason="Duplicate approval")
        self.assertEqual(self.memory.suggest(**self.query), (route,))
        self.assertEqual(len(self.memory.audit_log()), 2)

    def test_audit_is_json_serializable_complete_and_detached(self):
        route = self.approve()
        self.memory.revoke(route.route_id, reviewer="reviewer-2", reason="Superseded")
        audit = self.memory.audit_log()
        self.assertEqual(json.loads(json.dumps(audit)), audit)
        self.assertEqual([row["sequence"] for row in audit], [1, 2, 3])
        self.assertEqual([row["event"] for row in audit], ["add", "review", "revoke"])
        self.assertEqual([row["status"] for row in audit], ["pending", "approved", "revoked"])
        self.assertEqual([row["previous_status"] for row in audit], [None, "pending", "approved"])
        self.assertEqual(audit[-1]["reviewer"], "reviewer-2")
        self.assertEqual(audit[-1]["reason"], "Superseded")
        audit[0]["route"]["actions"].append("invented_action")
        audit[1]["route"]["context"][0][1] = "changed"
        audit.clear()
        original = self.memory.audit_log()
        self.assertEqual(len(original), 3)
        self.assertEqual(original[0]["route"]["actions"], list(route.actions))
        self.assertEqual(original[1]["route"]["context"], [list(pair) for pair in route.context])

    def test_routes_are_immutable_and_detached_from_submission_and_each_suggestion(self):
        route = self.approve()
        first = self.memory.suggest(**self.query)[0]
        second = self.memory.suggest(**self.query)[0]
        self.assertIsNot(first, route)
        self.assertIsNot(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.actions = ("request_review",)
        self.query["context"]["mode"] = "changed"
        self.assertEqual(route.context[0], ("mode", "reorder"))

    def test_unknown_actions_are_rejected_without_mutation(self):
        with self.assertRaises(ValueError):
            self.memory.add(sample_route(actions=("unregistered",)))
        self.assertEqual(self.memory.audit_log(), [])
        with self.assertRaises(KeyError):
            self.memory.status("inventory-v1")

    def test_action_allowlist_is_detached_and_routes_can_repeat_actions(self):
        names = ["read_stock"]
        memory = RouteMemory(names)
        names.append("unregistered")
        with self.assertRaises(ValueError):
            memory.add(sample_route(actions=("unregistered",)))
        memory.add(sample_route(actions=("read_stock", "read_stock")))
        self.assertEqual(memory.status("inventory-v1"), "pending")

    def test_route_fields_do_not_contain_facts_answers_or_model_confidence(self):
        self.assertEqual(
            {field.name for field in fields(Route)},
            {"route_id", "domain", "context", "actions", "required_metrics"},
        )
        route = self.approve()
        audit_before = self.memory.audit_log()
        suggested = self.memory.suggest(**self.query)
        self.assertEqual(suggested, (route,))
        self.assertEqual(self.memory.audit_log(), audit_before)

    def test_empty_requirements_and_multiple_routes_preserve_insertion_order(self):
        first = self.approve(sample_route("route-z", context=(), required_metrics=()))
        second = self.approve(sample_route("route-a", context=(), required_metrics=()))
        self.assertEqual(
            self.memory.suggest(domain="inventory", context={}, available_metrics=()),
            (first, second),
        )

    def test_reviewer_and_reason_are_required_and_invalid_review_cannot_change_state(self):
        route = sample_route()
        self.memory.add(route)
        for method in (self.memory.review, self.memory.revoke):
            for fields_ in (
                {"reviewer": "", "reason": "reason"},
                {"reviewer": "reviewer", "reason": " "},
                {"reviewer": None, "reason": "reason"},
            ):
                with self.subTest(method=method.__name__, fields=fields_):
                    with self.assertRaises((ValueError, TypeError)):
                        method(route.route_id, **fields_)
                    self.assertEqual(self.memory.status(route.route_id), "pending")
                    self.assertEqual(len(self.memory.audit_log()), 1)

    def test_unknown_ids_fail_without_audit_events(self):
        for method in (self.memory.review, self.memory.revoke):
            with self.assertRaises(KeyError):
                method("unknown", reviewer="reviewer", reason="test")
        self.assertEqual(self.memory.audit_log(), [])

    def test_route_validation_rejects_mutable_or_malformed_fields(self):
        invalid = (
            {"route_id": ""},
            {"domain": " inventory"},
            {"context": [("mode", "reorder")]},
            {"context": (["mode", "reorder"],)},
            {"context": (("mode", "reorder", "extra"),)},
            {"context": (("mode", 1),)},
            {"context": (("mode", "reorder"), ("mode", "audit"))},
            {"actions": ["read_stock"]},
            {"actions": ()},
            {"actions": ("",)},
            {"actions": ({"name": "read_stock"},)},
            {"required_metrics": ["stock"]},
            {"required_metrics": ("stock", "stock")},
        )
        for change in invalid:
            with self.subTest(change=change):
                with self.assertRaises((ValueError, TypeError)):
                    sample_route(**change)

    def test_suggest_and_registry_reject_strings_in_place_of_name_sequences(self):
        with self.assertRaises(TypeError):
            RouteMemory("read_stock")
        with self.assertRaises(TypeError):
            RouteMemory({"read_stock": "not an action registry"})
        with self.assertRaises(TypeError):
            self.memory.suggest(**{**self.query, "available_metrics": "stock"})
        with self.assertRaises(TypeError):
            self.memory.suggest(**{**self.query, "context": (("mode", "reorder"),)})


if __name__ == "__main__":
    unittest.main()
