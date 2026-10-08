"""Local transactions must earn progress under an unchanged whole-plan contract."""
from copy import deepcopy
import math

import pytest

from resimind.repair import Edit, RepairProposal, apply_repair, plan_sha256


def propose(plan, *edits):
    return RepairProposal(plan_sha256(plan), tuple(edits))


def edit(path="/x", before=0, value=1, refs=("tool:1",)):
    return Edit(path, before, value, refs)


def apply(plan, proposal, verifier=None, **kwargs):
    return apply_repair(plan, proposal, evidence_ids={"tool:1"},
                        verifier=verifier or (lambda p: {"x": p["x"] == 1}), **kwargs)


def test_acceptance_requires_whole_plan_checks_and_preserves_caller_data():
    original = {"x": 0, "unchanged": [1, {"a": "b"}]}
    snapshot = deepcopy(original)
    seen = []

    def verify(plan):
        seen.append(deepcopy(plan))
        return {"x": plan["x"] == 1, "untouched": plan["unchanged"] == snapshot["unchanged"]}

    result = apply(original, propose(original, edit()), verify)
    assert result.status == "accepted" and result.committed
    assert result.applied_edits == 1
    assert seen == [snapshot, {**snapshot, "x": 1}]
    assert original == snapshot
    result.plan["unchanged"][1]["a"] = "caller owns this result"
    assert original == snapshot


def test_partial_progress_commits_without_claiming_completion():
    plan = {"x": 0}
    result = apply(plan, propose(plan, edit()), lambda p: {"x": p["x"] == 1, "missing": None})
    assert result.status == "repaired" and result.committed
    assert result.plan == {"x": 1}
    assert result.after_checks == {"x": True, "missing": None}


def test_unknown_to_true_is_progress_but_unknown_to_false_is_not():
    plan = {"x": 0}
    proposal = propose(plan, edit())
    assert apply(plan, proposal, lambda p: {"c": True if p["x"] else None}).status == "accepted"
    assert apply(plan, proposal, lambda p: {"c": False if p["x"] else None}).status == "rejected"


@pytest.mark.parametrize("after", [False, None])
def test_regression_rolls_back_even_if_more_other_checks_pass(after):
    plan = {"x": 0, "nested": ["unchanged"]}
    result = apply(plan, propose(plan, edit()), lambda p: {
        "protected": after if p["x"] else True,
        "new1": bool(p["x"]), "new2": bool(p["x"]),
    })
    assert result.status == "rejected" and not result.committed
    assert result.plan == plan and result.applied_edits == 0
    result.plan["nested"].append("detached")
    assert plan["nested"] == ["unchanged"]


def test_nonprogress_and_already_solved_edits_do_not_commit():
    plan = {"x": 0}
    for check in (False, True, None):
        result = apply(plan, propose(plan, edit()), lambda p: {"same": check})
        assert result.status == "rejected" and result.plan == plan


def test_known_failure_becoming_unknown_defers_despite_other_progress():
    plan = {"x": 0}
    result = apply(plan, propose(plan, edit()), lambda p: {
        "improves": p["x"] == 1, "lost_verification": None if p["x"] else False,
    })
    assert result.status == "deferred" and result.plan == plan
    assert result.applied_edits == 0


def test_changed_check_names_defer_even_when_new_mapping_is_all_true():
    plan = {"x": 0}
    result = apply(plan, propose(plan, edit()), lambda p: {"new" if p["x"] else "old": bool(p["x"])})
    assert result.status == "deferred" and result.plan == plan


@pytest.mark.parametrize("bad", [{}, {"x": 1}, {"x": "true"}, {"": True}, {1: True}, [], None])
def test_empty_or_malformed_verifier_output_never_accepts(bad):
    plan = {"x": 0}
    result = apply(plan, propose(plan, edit()), lambda p: bad)
    assert result.status == "deferred" and result.plan == plan


@pytest.mark.parametrize("mutate_on", [0, 1])
def test_mutating_checker_cannot_commit_or_modify_inputs(mutate_on):
    plan = {"x": 0, "items": ["original"]}

    def malicious(p):
        check = p["x"] == 1
        if p["x"] == mutate_on:
            p["items"][0] = "tampered"
        return {"check": check}

    result = apply(plan, propose(plan, edit()), malicious)
    assert result.status == "deferred"
    assert result.plan == plan == {"x": 0, "items": ["original"]}


@pytest.mark.parametrize("fail_on", [0, 1])
def test_checker_exceptions_preserve_original(fail_on):
    plan = {"x": 0}

    def broken(p):
        if p["x"] == fail_on:
            raise RuntimeError("unavailable dependency")
        return {"x": p["x"] == 1}

    result = apply(plan, propose(plan, edit()), broken)
    assert result.status == "deferred" and result.plan == plan


def test_checker_returned_mutable_mapping_is_snapshotted_between_calls():
    checks = {"x": False}
    plan = {"x": 0}

    def reused(p):
        checks["x"] = p["x"] == 1
        return checks

    result = apply(plan, propose(plan, edit()), reused)
    assert result.status == "accepted"
    assert result.before_checks == {"x": False}
    checks["x"] = None
    assert result.after_checks == {"x": True}


def test_stale_base_and_stale_preconditions_stop_before_verification():
    plan = {"x": 0}

    def forbidden(_):
        raise AssertionError("verifier should not run")

    for proposal in (RepairProposal("0" * 64, (edit(),)), propose(plan, edit(before=2))):
        result = apply(plan, proposal, forbidden)
        assert result.status == "rejected" and result.plan == plan
        assert result.before_checks == {}


@pytest.mark.parametrize("before", [False, 0.0])
def test_expected_before_is_type_sensitive(before):
    plan = {"x": 0}
    assert apply(plan, propose(plan, edit(before=before))).status == "rejected"


def test_atomicity_when_second_edit_has_stale_precondition():
    plan = {"x": 0, "y": 0}
    result = apply(plan, propose(plan, edit(), edit("/y", 99, 1)))
    assert result.status == "rejected" and result.plan == {"x": 0, "y": 0}
    assert result.applied_edits == 0


@pytest.mark.parametrize("refs", [(), ("made-up",), ("tool:1", "tool:1"), ["tool:1"], (True,)])
def test_patch_cannot_authorize_its_own_evidence(refs):
    plan = {"x": 0}
    assert apply(plan, propose(plan, edit(refs=refs))).status == "rejected"


def test_known_evidence_is_not_proof_of_correctness():
    plan = {"x": 0}
    result = apply(plan, propose(plan, edit(value=999)))
    assert result.status == "rejected" and result.plan == plan


@pytest.mark.parametrize("paths", [("/a", "/a/b"), ("/a/b", "/a"), ("/a/b", "/a/b")])
def test_overlapping_paths_are_rejected(paths):
    plan = {"a": {"b": 0}}
    edits = tuple(edit(path, plan["a"] if path == "/a" else 0, 1) for path in paths)
    assert apply(plan, propose(plan, *edits), lambda p: {"x": True}).status == "rejected"


def test_pointer_escapes_empty_object_keys_and_sibling_prefixes():
    plan = {"a/b": {"~1": 0}, "": 0, "a": 0, "ab": 0}
    proposal = propose(plan, edit("/a~1b/~01"), edit("/"), edit("/a"), edit("/ab"))
    result = apply(plan, proposal, lambda p: {"fixed": all((p["a/b"]["~1"], p[""], p["a"], p["ab"]))})
    assert result.status == "accepted"
    assert result.applied_edits == 4


@pytest.mark.parametrize("path", ["", "x", "/~", "/~2", "/missing", "/x/a", True, 0])
def test_invalid_missing_or_root_paths_rejected(path):
    plan = {"x": 0}
    assert apply(plan, propose(plan, edit(path))).status == "rejected"


@pytest.mark.parametrize("index", ["-", "-1", "+0", "00", "01", "1.0", "True", " 0", "2", "9" * 5000])
def test_array_paths_reject_append_noncanonical_and_out_of_bounds(index):
    plan = {"items": [0, 0]}
    result = apply(plan, propose(plan, edit("/items/" + index)), lambda p: {"x": True})
    assert result.status == "rejected"


def test_array_replacements_leave_indices_and_other_values_unchanged():
    plan = {"items": [0, 4, 0]}
    proposal = propose(plan, edit("/items/0"), edit("/items/2"))
    result = apply(plan, proposal, lambda p: {"ends": p["items"] == [1, 4, 1]})
    assert result.status == "accepted" and result.plan == {"items": [1, 4, 1]}


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, (1, 2), {1: "bad"}, {"nested": [math.nan]}])
def test_deep_json_validation_rejects_non_json(value):
    with pytest.raises(ValueError):
        plan_sha256(value)
    plan = {"x": 0}
    assert apply(plan, propose(plan, edit(value=value))).status == "rejected"


def test_cycles_excessive_depth_and_scalar_subclasses_are_not_json():
    cycle = []
    cycle.append(cycle)
    deep = 0
    for _ in range(66):
        deep = [deep]

    class FakeInt(int):
        pass

    for value in (cycle, deep, FakeInt(1)):
        with pytest.raises(ValueError):
            plan_sha256(value)


def test_canonical_hash_is_order_independent_type_sensitive_and_detaches_aliases():
    assert plan_sha256({"a": 1, "b": 2}) == plan_sha256({"b": 2, "a": 1})
    assert len({plan_sha256(x) for x in (False, 0, 0.0)}) == 3
    shared = {"x": 0}
    plan = {"a": shared, "b": shared}
    result = apply(plan, propose(plan, edit("/a/x")), lambda p: {"a": p["a"]["x"] == 1})
    assert result.status == "accepted" and result.plan["b"] == {"x": 0}
    assert shared == {"x": 0}


def test_edit_and_combined_plan_byte_budgets_are_enforced():
    plan = {"x": 0, "y": 0}
    proposal = propose(plan, edit(value="x" * 20), edit("/y", value="y" * 20))
    assert apply(plan, proposal, max_edits=1).status == "rejected"
    assert apply(plan, proposal, max_json_bytes=40).status == "rejected"
    with pytest.raises(ValueError):
        apply(plan, proposal, max_json_bytes=3)


@pytest.mark.parametrize("value", [True, 0, -1, 1.0])
def test_limit_values_are_positive_integers_not_bools(value):
    plan = {"x": 0}
    with pytest.raises(ValueError):
        apply(plan, propose(plan, edit()), max_edits=value)
    with pytest.raises(ValueError):
        apply(plan, propose(plan, edit()), max_json_bytes=value)


@pytest.mark.parametrize("evidence", ["tool:1", [True], [""], None])
def test_trusted_configuration_must_be_explicit_and_valid(evidence):
    plan = {"x": 0}
    with pytest.raises(ValueError):
        apply_repair(plan, propose(plan, edit()), evidence_ids=evidence, verifier=lambda p: {"x": True})


@pytest.mark.parametrize("proposal", [None, {}, RepairProposal("0" * 64, ()), RepairProposal(True, ())])
def test_malformed_proposal_rejected(proposal):
    assert apply({"x": 0}, proposal).status == "rejected"


def test_replacement_and_expected_values_are_snapshotted_before_checker():
    plan = {"x": 0}
    replacement = [1]
    proposal = propose(plan, edit(value=replacement))

    def verify(p):
        replacement.append("changed outside the candidate")
        return {"x": p["x"] == [1]}

    result = apply(plan, proposal, verify)
    assert result.status == "accepted" and result.plan == {"x": [1]}
