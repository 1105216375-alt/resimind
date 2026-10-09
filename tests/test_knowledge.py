"""Offline lifecycle tests; arithmetic here is a deliberately narrow verifier."""

from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from resimind.knowledge import KnowledgeCandidate, KnowledgeLibrary, KnowledgeRecord, Verification


class ArithmeticVerifier:
    def __init__(self, version="arithmetic-v1"):
        self.calls = []
        self.version = version

    def verify(self, candidate):
        self.calls.append(candidate.id)
        statement = candidate.statement
        if set(statement) != {"left", "right", "sum"}:
            return Verification("unknown", "unsupported claim shape", self.version)
        if not all(type(value) is int for value in statement.values()):
            return Verification("unknown", "integer claims required", self.version)
        valid = statement["left"] + statement["right"] == statement["sum"]
        return Verification("verified" if valid else "rejected", "checked integer addition", self.version)


def claim(id="sum-v1", **changes):
    values = {
        "id": id, "domain": "arithmetic", "kind": "identity",
        "statement": {"left": 2, "right": 3, "sum": 5},
        "derivation": ({"operation": "addition", "inputs": [2, 3]},),
        "source_task_id": "discovery-task-1", "evidence_refs": ("trace:1/step:2",),
    }
    values.update(changes)
    return KnowledgeCandidate(**values)


class KnowledgeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.verifier = ArithmeticVerifier()
        self.library = KnowledgeLibrary({"arithmetic": self.verifier})
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "knowledge.json"

    def test_all_verdicts_are_recorded_but_only_verified_is_retrievable(self):
        good = self.library.admit(claim())
        bad = self.library.admit(claim("bad", statement={"left": 2, "right": 3, "sum": 99}))
        unknown = self.library.admit(claim("unknown", domain="unsupported"))
        self.assertEqual((good.status, bad.status, unknown.status), ("verified", "rejected", "unknown"))
        self.assertEqual(self.library.get("bad"), bad)
        self.assertEqual(self.library.get("unknown"), unknown)
        self.assertEqual(self.library.lookup("arithmetic"), (good,))
        self.assertEqual(self.verifier.calls, ["sum-v1", "bad"])

    def test_missing_provenance_cannot_be_admitted_even_with_evidence_refs(self):
        record = self.library.admit(claim(source_task_id="", evidence_refs=("a-proof-I-promise",)))
        self.assertEqual(record.status, "unknown")
        self.assertEqual(self.verifier.calls, [])
        self.assertEqual(self.library.lookup("arithmetic"), ())

    def test_proof_flags_and_references_cannot_approve_a_false_statement(self):
        record = self.library.admit(claim(
            statement={"left": 2, "right": 3, "sum": 99},
            derivation=({"verified": True, "confidence": 1.0},),
            evidence_refs=("formal-proof:claimed",),
        ))
        self.assertEqual(record.status, "rejected")

    def test_unknown_or_exceptional_verifiers_fail_closed(self):
        class Broken:
            def verify(self, candidate):
                raise RuntimeError("private service failure details")

        class Malformed:
            def verify(self, candidate):
                return {"status": "verified", "reason": "trust me"}

        class Corrupted:
            def verify(self, candidate):
                result = Verification("verified", "checked", "v1")
                object.__setattr__(result, "status", True)
                return result

        for verifier in (Broken(), Malformed(), Corrupted()):
            with self.subTest(verifier=type(verifier).__name__):
                library = KnowledgeLibrary({"arithmetic": verifier})
                record = library.admit(claim())
                self.assertEqual(record.status, "unknown")
                self.assertEqual(library.lookup("arithmetic"), ())
                self.assertNotIn("private service", record.verification.reason)

    def test_conditions_domain_and_kind_all_gate_lookup(self):
        conditional = self.library.admit(claim(assumptions=("a != 0", "a is real")))
        self.assertEqual(self.library.lookup("arithmetic"), ())
        self.assertEqual(self.library.lookup("arithmetic", assumptions=("a != 0",)), ())
        query = ("a is real", "a != 0", "extra condition")
        self.assertEqual(self.library.lookup("arithmetic", "identity", query), (conditional,))
        self.assertEqual(self.library.lookup("other", "identity", query), ())
        self.assertEqual(self.library.lookup("arithmetic", "algorithm", query), ())
        self.assertEqual(self.library.lookup("arithmetic", assumptions=("a!=0", "a is real")), ())

    def test_every_mutable_boundary_is_detached(self):
        statement = {"left": 2, "right": 3, "sum": 5}
        step = {"inputs": [2, 3]}
        candidate = claim(statement=statement, derivation=(step,))
        statement["sum"] = 99
        step["inputs"].append(99)
        self.assertEqual(candidate.statement["sum"], 5)
        self.assertEqual(candidate.derivation[0]["inputs"], [2, 3])
        record = self.library.admit(candidate)
        candidate.statement["sum"] = 100
        candidate.derivation[0]["inputs"].append(100)
        record.candidate.statement["sum"] = 101
        record.candidate.derivation[0]["inputs"].append(101)
        fetched = self.library.get(candidate.id)
        fetched.candidate.statement["sum"] = 102
        result = self.library.lookup("arithmetic")[0]
        result.candidate.derivation[0]["inputs"].append(102)
        stored = self.library.get(candidate.id)
        self.assertEqual(stored.candidate.statement["sum"], 5)
        self.assertEqual(stored.candidate.derivation[0]["inputs"], [2, 3])
        with self.assertRaises(FrozenInstanceError):
            stored.status = "revoked"

    def test_verifier_input_is_detached_and_mutation_invalidates_approval(self):
        class Mutator:
            def verify(self, candidate):
                candidate.statement["sum"] = 5
                return Verification("verified", "checked altered claim", "mutating-v1")

        library = KnowledgeLibrary({"arithmetic": Mutator()})
        record = library.admit(claim(statement={"left": 2, "right": 3, "sum": 99}))
        self.assertEqual(record.status, "unknown")
        self.assertEqual(library.get(record.candidate.id).candidate.statement["sum"], 99)

    def test_record_construction_validates_and_detaches_its_values(self):
        original = self.library.admit(claim())
        copied = KnowledgeRecord(
            original.candidate, original.verification, original.status, original.fingerprint,
        )
        original.candidate.statement["sum"] = 99
        self.assertEqual(copied.candidate.statement["sum"], 5)
        with self.assertRaises(ValueError):
            KnowledgeRecord(copied.candidate, copied.verification, "approved", copied.fingerprint)
        with self.assertRaises(ValueError):
            KnowledgeRecord(copied.candidate, copied.verification, "verified", "forged")
        with self.assertRaises(TypeError):
            KnowledgeRecord(copied.candidate, {"status": "verified"}, "verified", copied.fingerprint)

    def test_duplicate_ids_are_refused_before_verifier_runs(self):
        original = self.library.admit(claim())
        with self.assertRaises(ValueError):
            self.library.admit(claim(statement={"left": 2, "right": 3, "sum": 99}))
        self.assertEqual(self.verifier.calls, ["sum-v1"])
        self.assertEqual(self.library.get("sum-v1"), original)

    def test_revocation_is_terminal_and_survives_reverification(self):
        self.library.admit(claim())
        revoked = self.library.revoke("sum-v1", "superseded by a narrower rule")
        self.assertEqual(revoked.status, "revoked")
        self.assertEqual(revoked.verification.status, "verified")
        self.assertEqual(self.library.lookup("arithmetic"), ())
        with self.assertRaises(ValueError):
            self.library.revoke("sum-v1", "again")
        with self.assertRaises(ValueError):
            self.library.admit(claim())
        self.library.save(self.path)
        fresh = ArithmeticVerifier("arithmetic-v2")
        restored = KnowledgeLibrary.load(self.path, {"arithmetic": fresh})
        self.assertEqual(fresh.calls, ["sum-v1"])
        self.assertEqual(restored.get("sum-v1").status, "revoked")
        self.assertEqual(restored.get("sum-v1").verification.verifier_id, "arithmetic-v2")
        self.assertEqual(restored.lookup("arithmetic"), ())
        restored.save(self.path)
        row = json.loads(self.path.read_text())["records"][0]
        self.assertEqual(row["revocation_reason"], "superseded by a narrower rule")

    def test_load_reverifies_all_outcomes_with_current_verifier(self):
        original = KnowledgeLibrary({})
        original.admit(claim("formerly-unknown"))
        original.admit(claim("wrong", statement={"left": 2, "right": 3, "sum": 99}))
        original.save(self.path)
        restored = KnowledgeLibrary.load(self.path, {"arithmetic": self.verifier})
        self.assertEqual(self.verifier.calls, ["formerly-unknown", "wrong"])
        self.assertEqual(restored.get("formerly-unknown").status, "verified")
        self.assertEqual(restored.get("wrong").status, "rejected")
        restored.save(self.path)
        no_verifier = KnowledgeLibrary.load(self.path, {})
        self.assertEqual(no_verifier.get("formerly-unknown").status, "unknown")
        self.assertEqual(no_verifier.lookup("arithmetic"), ())

    def test_tampered_approval_flags_are_not_trusted_on_reload(self):
        self.library.admit(claim(statement={"left": 2, "right": 3, "sum": 99}))
        self.library.save(self.path)
        payload = json.loads(self.path.read_text())
        payload["records"][0]["status"] = "verified"
        payload["records"][0]["verification"] = {
            "status": "verified", "reason": "forged approval", "verifier_id": "trusted-v999",
        }
        self.path.write_text(json.dumps(payload))
        restored = KnowledgeLibrary.load(self.path, {"arithmetic": self.verifier})
        self.assertEqual(restored.get("sum-v1").status, "rejected")
        self.assertEqual(restored.get("sum-v1").verification.verifier_id, "arithmetic-v1")

    def test_tampered_candidate_and_provenance_fail_fingerprint_validation(self):
        self.library.admit(claim())
        self.library.save(self.path)
        saved = self.path.read_text()
        for field, changed in (("source_task_id", "another-task"), ("statement", {"sum": 99})):
            payload = json.loads(saved)
            payload["records"][0]["candidate"][field] = changed
            self.path.write_text(json.dumps(payload))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "fingerprint"):
                KnowledgeLibrary.load(self.path, {"arithmetic": self.verifier})

    def test_recomputed_fingerprint_does_not_prove_a_tampered_claim(self):
        self.library.admit(claim())
        self.library.save(self.path)
        payload = json.loads(self.path.read_text())
        row = payload["records"][0]
        row["candidate"]["statement"]["sum"] = 99
        canonical = json.dumps(row["candidate"], sort_keys=True, separators=(",", ":"))
        row["fingerprint"] = sha256(canonical.encode("utf-8")).hexdigest()
        self.path.write_text(json.dumps(payload))
        restored = KnowledgeLibrary.load(self.path, {"arithmetic": self.verifier})
        self.assertEqual(restored.get("sum-v1").status, "rejected")
        self.assertEqual(restored.lookup("arithmetic"), ())

    def test_fingerprint_is_canonical_and_includes_provenance(self):
        first = self.library.admit(claim())
        other = KnowledgeLibrary({"arithmetic": ArithmeticVerifier()})
        reordered = other.admit(claim(statement={"sum": 5, "right": 3, "left": 2}))
        self.assertEqual(first.fingerprint, reordered.fingerprint)
        another = KnowledgeLibrary({"arithmetic": ArithmeticVerifier()})
        revised = another.admit(claim(source_task_id="task-2"))
        self.assertNotEqual(first.fingerprint, revised.fingerprint)

    def test_malformed_fields_and_non_json_content_are_rejected(self):
        bad_changes = (
            {"id": ""}, {"domain": True}, {"kind": " theorem"}, {"source_task_id": None},
            {"statement": []}, {"statement": {1: "bad key"}},
            {"statement": {"value": float("nan")}}, {"statement": {"value": float("inf")}},
            {"statement": {"value": (1, 2)}}, {"statement": {"value": object()}},
            {"assumptions": ["a != 0"]}, {"assumptions": (True,)},
            {"derivation": []}, {"derivation": ("proof",)}, {"evidence_refs": "proof"},
        )
        for changes in bad_changes:
            with self.subTest(changes=changes), self.assertRaises((TypeError, ValueError)):
                claim(**changes)
        cyclic = {}
        cyclic["self"] = cyclic
        with self.assertRaises(ValueError):
            claim(statement=cyclic)
        candidate = claim()
        candidate.statement["invalid"] = object()
        with self.assertRaises(TypeError):
            self.library.admit(candidate)
        self.assertEqual(self.library.lookup("arithmetic"), ())

    def test_malformed_serialized_schema_never_returns_a_library(self):
        self.library.admit(claim())
        self.library.save(self.path)
        saved = self.path.read_text()
        changes = (
            lambda p: p.update(schema_version=True),
            lambda p: p.update(schema_version=999),
            lambda p: p.update(records={}),
            lambda p: p["records"][0]["candidate"].update(assumptions="a != 0"),
            lambda p: p["records"][0].update(status=True),
            lambda p: p["records"][0].update(revocation_reason="hidden revocation"),
            lambda p: p["records"].append(p["records"][0]),
        )
        for change in changes:
            payload = json.loads(saved)
            change(payload)
            self.path.write_text(json.dumps(payload))
            with self.subTest(change=change), self.assertRaises((TypeError, ValueError)):
                KnowledgeLibrary.load(self.path, {"arithmetic": self.verifier})
        for raw in ('{"schema_version":1,"schema_version":1,"records":[]}', '{"schema_version":1,"records":[NaN]}'):
            self.path.write_text(raw)
            with self.assertRaises(ValueError):
                KnowledgeLibrary.load(self.path, {})

    def test_atomic_write_failure_preserves_previous_file_and_cleans_temporary(self):
        self.library.admit(claim())
        self.library.save(self.path)
        prior = self.path.read_text()
        self.library.admit(claim("second"))
        with patch("resimind.knowledge.os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaises(OSError):
                self.library.save(self.path)
        self.assertEqual(self.path.read_text(), prior)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_queries_registry_and_revocation_do_not_change_records(self):
        record = self.library.admit(claim())
        for call in (
            lambda: self.library.lookup("arithmetic", assumptions="a != 0"),
            lambda: self.library.lookup("arithmetic", kind=False),
            lambda: self.library.revoke("sum-v1", ""),
            lambda: KnowledgeLibrary({"arithmetic": object()}),
            lambda: KnowledgeLibrary({True: self.verifier}),
        ):
            with self.assertRaises((TypeError, ValueError)):
                call()
        with self.assertRaises(KeyError):
            self.library.get("missing")
        self.assertEqual(self.library.get("sum-v1"), record)


if __name__ == "__main__":
    unittest.main()
