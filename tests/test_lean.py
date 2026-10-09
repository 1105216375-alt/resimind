"""Boundary tests plus real optional Lean kernel acceptance checks."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from resimind.integrations import lean
from resimind.integrations.lean import (
    APPROVED_AXIOMS, LeanPolynomialBackend, polynomial_binding_digest,
)


def _message(data, *, kind="[anonymous]", severity="information"):
    return {"data": data, "kind": kind, "severity": severity}


def _fake_compiler(monkeypatch, tmp_path, *, messages=None, returncode=0,
                   artifact=True, stderr="", failure=None, version="4.29.0"):
    executable = tmp_path / "lean"
    executable.write_text("fixture executable is never run")
    actual = messages if messages is not None else [
        _message("v0 : Rat\n⊢ v0 = v0", kind="trace"),
        _message("'resimind_checked' depends on axioms: [propext, Classical.choice, Quot.sound]"),
    ]

    def run(command, *, cwd, **kwargs):
        if command[-1] == "--version":
            return lean._ProcessResult(0, f"Lean (version {version}, test build)\n")
        if artifact:
            (cwd / "Proof.olean").write_bytes(b"test-compiled-proof")
        return lean._ProcessResult(returncode, "\n".join(json.dumps(item) for item in actual), stderr, failure)

    monkeypatch.setattr(lean, "_run_process", run)
    return LeanPolynomialBackend(executable)


@pytest.mark.parametrize("before,after,variables,tactic", [
    ("__import__('os').system('echo bad')", "0", ("x",), "grind"),
    ("x; run_tac", "x", ("x",), "grind"),
    ("x", "x", ("x) : Rat := by sorry\naxiom injected : False",), "grind"),
    ("x", "x", ("x",), "grind\n  sorry"),
    ("x", "x", ("x",), "ring"),
    ("x", "x", ("x",), "run_tac"),
    ("x", "x", ("x",), "unsafe"),
    ("x", "x", ("x",), "axiom"),
    ("x", "x", ("x",), "sorry"),
    ("x/0", "x", ("x",), "grind"),
    ("x/y", "x", ("x", "y"), "grind"),
    ("x**17", "x", ("x",), "grind"),
    ("z", "z", ("x",), "grind"),
    ("True", "1", (), "grind"),
    ("x", "x", None, "grind"),
])
def test_invalid_requests_never_start_a_process(monkeypatch, before, after, variables, tactic):
    monkeypatch.setattr(lean, "_run_process", lambda *args, **kwargs: pytest.fail("must not launch Lean"))
    result = LeanPolynomialBackend().check(before, after, variables, tactic=tactic)
    assert result.status == "error"
    assert result.proof_digest is None


def test_source_rewrites_names_and_interpolates_only_validated_ast():
    source, target, mapping = lean._source("theorem + sorry", "sorry + theorem", ("theorem", "sorry"), "grind", 200000)
    assert mapping == (("theorem", "v0"), ("sorry", "v1"))
    assert "sorry" not in source
    assert "v0 + v1" in target
    assert "import Lean\n" in source
    assert "  grind\n" in source
    assert source.count("theorem ") == 1


def test_feedback_binding_changes_for_target_and_variable_order():
    value = polynomial_binding_digest("x+y", "y+x", ("x", "y"))
    assert value != polynomial_binding_digest("x+y", "y-x", ("x", "y"))
    assert value != polynomial_binding_digest("x+y", "y+x", ("y", "x"))


@pytest.mark.parametrize("options", [
    {"timeout_seconds": 0}, {"timeout_seconds": True}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": 121}, {"max_output_bytes": 10},
    {"max_output_bytes": 2**30}, {"max_heartbeats": True}, {"max_heartbeats": 0},
    {"memory_megabytes": 0}, {"required_version": "4.28.0"},
])
def test_configuration_is_bounded(options):
    with pytest.raises(ValueError):
        LeanPolynomialBackend(**options)


def test_missing_tool_is_unavailable(tmp_path):
    result = LeanPolynomialBackend(tmp_path / "missing").check("x", "x", ("x",))
    assert result.status == "unavailable"
    assert result.binding_digest and result.source_digest
    assert result.proof_digest is None


@pytest.mark.parametrize("shim_is_symlink", [False, True])
def test_symlinked_elan_shim_without_pinned_toolchain_never_starts(monkeypatch, tmp_path, shim_is_symlink):
    elan_bin = tmp_path / "home" / ".elan" / "bin"
    elan_bin.mkdir(parents=True)
    shim = elan_bin / "lean"
    if shim_is_symlink:
        executable = elan_bin / "elan"
        executable.write_text("elan shim must not run")
        shim.symlink_to(executable)
    else:
        shim.write_text("elan shim must not run")
    alias = tmp_path / "lean"
    alias.symlink_to(shim)
    monkeypatch.setattr(lean, "_run_process", lambda *args, **kwargs: pytest.fail("elan shim must not start"))
    backend = LeanPolynomialBackend(alias)
    assert backend.lean_path == str(shim.resolve())
    result = backend.check("x", "x", ("x",))
    assert result.status == "unavailable"
    assert "4.29.0 is not installed" in result.diagnostics[0]
    assert result.proof_digest is None


def test_symlinked_elan_shim_uses_only_an_already_installed_pinned_binary(monkeypatch, tmp_path):
    elan_home = tmp_path / ".elan"
    elan_bin = elan_home / "bin"
    elan_bin.mkdir(parents=True)
    shim = elan_bin / "elan"
    shim.write_text("elan shim must not run")
    alias = tmp_path / "lean"
    alias.symlink_to(shim)
    compiler = elan_home / "toolchains" / "leanprover--lean4---v4.29.0" / "bin" / "lean"
    compiler.parent.mkdir(parents=True)
    compiler.write_text("pinned compiler fixture")
    calls = []

    def run(command, *, cwd, **kwargs):
        calls.append(command)
        assert command[0] == str(compiler)
        if command[-1] == "--version":
            return lean._ProcessResult(0, "Lean (version 4.29.0, test build)\n")
        (cwd / "Proof.olean").write_bytes(b"test-compiled-proof")
        return lean._ProcessResult(0, "\n".join(json.dumps(item) for item in [
            _message("v0 : Rat\n⊢ v0 = v0", kind="trace"),
            _message("'resimind_checked' does not depend on any axioms"),
        ]))

    monkeypatch.setattr(lean, "_run_process", run)
    result = LeanPolynomialBackend(alias).check("x", "x", ("x",))
    assert result.status == "verified"
    assert len(calls) == 2


def test_unsupported_version_is_unavailable(monkeypatch, tmp_path):
    result = _fake_compiler(monkeypatch, tmp_path, version="4.28.0").check("x", "x", ("x",))
    assert result.status == "unavailable"
    assert result.lean_version == "4.28.0"


def test_complete_evidence_is_bound_and_hashes_compiled_artifact(monkeypatch, tmp_path):
    result = _fake_compiler(monkeypatch, tmp_path).check("x", "x", ("x",), tactic="rfl")
    assert result.status == "verified"
    assert result.binding_digest == polynomial_binding_digest("x", "x", ("x",))
    assert result.actual_target == "v0 : Rat\n⊢ v0 = v0"
    assert set(result.axioms) == APPROVED_AXIOMS
    assert result.proof_digest and result.proof_digest != result.source_digest
    assert not result.goals


@pytest.mark.parametrize("axioms", ["sorryAx", "propext, sorryAx", "Lean.ofReduceBool", "user.injectedAxiom"])
def test_unapproved_axioms_reject_even_successful_process(monkeypatch, tmp_path, axioms):
    result = _fake_compiler(monkeypatch, tmp_path, messages=[
        _message("v0 : Rat\n⊢ v0 = v0", kind="trace"),
        _message(f"'resimind_checked' depends on axioms: [{axioms}]"),
    ]).check("x", "x", ("x",))
    assert result.status == "error"
    assert result.proof_digest is None


@pytest.mark.parametrize("kind", ["missing_axioms", "duplicate_axioms", "other_theorem", "missing_target",
                                 "multiple_targets", "malformed_message", "warning", "no_artifact"])
def test_incomplete_or_ambiguous_success_evidence_fails_closed(monkeypatch, tmp_path, kind):
    target = _message("v0 : Rat\n⊢ v0 = v0", kind="trace")
    axioms = _message("'resimind_checked' does not depend on any axioms")
    messages = [target, axioms]
    if kind == "missing_axioms":
        messages = [target]
    elif kind == "duplicate_axioms":
        messages.append(axioms)
    elif kind == "other_theorem":
        messages[1] = _message("'other_theorem' does not depend on any axioms")
    elif kind == "missing_target":
        messages = [axioms]
    elif kind == "multiple_targets":
        messages.append(target)
    elif kind == "malformed_message":
        messages.append({"data": 1, "severity": "information"})
    elif kind == "warning":
        messages.append(_message("declaration uses sorry", severity="warning"))
    result = _fake_compiler(monkeypatch, tmp_path, messages=messages, artifact=kind != "no_artifact").check("x", "x", ("x",))
    assert result.status == "error"
    assert result.proof_digest is None


@pytest.mark.parametrize("failure", ["Lean check timed out", "Lean output exceeded the byte limit"])
def test_failed_transport_does_not_accept_existing_proof_bytes(monkeypatch, tmp_path, failure):
    result = _fake_compiler(monkeypatch, tmp_path, failure=failure).check("x", "x", ("x",))
    assert result.status == "error"
    assert result.proof_digest is None


def test_failed_tactic_preserves_real_goal_but_never_reuses_recovery_sorry(monkeypatch, tmp_path):
    result = _fake_compiler(monkeypatch, tmp_path, returncode=1, messages=[
        _message("v0 : Rat\n⊢ v0 + 1 = v0 + 2", kind="trace"),
        _message("Tactic `rfl` failed\n\nv0 : Rat\n⊢ v0 + 1 = v0 + 2", severity="error"),
        _message("'resimind_checked' depends on axioms: [sorryAx]"),
    ]).check("x+1", "x+2", ("x",), tactic="rfl")
    assert result.status == "unresolved"
    assert result.goals == ("v0 : Rat\n⊢ v0 + 1 = v0 + 2",)
    assert result.proof_digest is None


def test_process_timeout_and_output_limits_are_real(tmp_path):
    timed = lean._run_process([sys.executable, "-c", "import time; time.sleep(2)"],
                              cwd=tmp_path, timeout=0.05, max_output_bytes=1024)
    assert "timed out" in timed.failure
    flooded = lean._run_process([sys.executable, "-c", "print('x' * 20000)"],
                                cwd=tmp_path, timeout=2, max_output_bytes=1024)
    assert "byte limit" in flooded.failure
    assert len(flooded.stdout.encode()) <= 1024


def test_process_rejects_invalid_utf8(tmp_path):
    result = lean._run_process([sys.executable, "-c", "import os; os.write(1, bytes([255]))"],
                              cwd=tmp_path, timeout=2, max_output_bytes=1024)
    assert "invalid UTF-8" in result.failure


@pytest.fixture(scope="module")
def real_lean():
    backend = LeanPolynomialBackend()
    if backend.lean_path is None or not Path(backend.lean_path).is_file():
        pytest.skip("optional Lean 4.29.0 installation is unavailable")
    available = backend.check("0", "0", (), tactic="rfl")
    if available.status == "unavailable":
        pytest.skip("optional Lean 4.29.0 installation is unavailable")
    assert available.status == "verified", available
    return backend


def test_real_lean_feedback_then_different_tactic_proves_same_goal(real_lean):
    failed = real_lean.check("(x+1)*(x+2)", "x*x+3*x+2", ("x",), tactic="rfl")
    assert failed.status == "unresolved", failed
    assert failed.goals and "⊢" in failed.actual_target
    assert "sorryAx" in failed.axioms
    assert failed.proof_digest is None
    proved = real_lean.check("(x+1)*(x+2)", "x*x+3*x+2", ("x",), tactic="grind")
    assert proved.status == "verified", proved
    assert proved.binding_digest == failed.binding_digest
    assert proved.source_digest != failed.source_digest
    assert proved.proof_digest and set(proved.axioms) <= APPROVED_AXIOMS
    assert not proved.goals


@pytest.mark.parametrize("before,after,variables", [
    ("(x/2+1)*(x-3)", "x**2/2-x/2-3", ("x",)),
    ("x**16", "x**8*x**8", ("x",)),
    ("x/(-2)", "-x/2", ("x",)),
    ("(x+y)*(x-y)", "x**2-y**2", ("x", "y")),
    ("theorem+sorry", "sorry+theorem", ("theorem", "sorry")),
    ("1/2+1/2", "1", ()),
])
def test_real_lean_validates_exact_rational_grammar(real_lean, before, after, variables):
    result = real_lean.check(before, after, variables)
    assert result.status == "verified", result
    assert result.proof_digest and "sorryAx" not in result.axioms


def test_real_lean_does_not_certify_false_identity(real_lean):
    result = real_lean.check("x+1", "x+2", ("x",))
    assert result.status == "unresolved", result
    assert result.goals and result.proof_digest is None
