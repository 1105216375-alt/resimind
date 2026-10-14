import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.external_theorem_adapter import (
    PROOF_HOLE,
    SCHEMA,
    ExternalLeanCompiler,
    TheoremManifest,
    TheoremRecord,
    prepare_source,
)


def _manifest(path: Path, *, source: str = "foo.lean", entry_overrides=None):
    source_path = path / source
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("import Lean\ntheorem foo : True := RESIMIND_PROOF_HOLE\n", encoding="utf-8")
    entry = {
        "id": "foo",
        "split": "test",
        "source": source,
        "theorem": "foo",
        "statement": "True",
        "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }
    entry.update(entry_overrides or {})
    manifest = path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema": SCHEMA,
        "dataset": "minif2f",
        "dataset_commit": "minif2f-test-commit",
        "lean_version": "4.29.0",
        "mathlib_commit": "mathlib-test-commit",
        "theorems": [entry],
    }), encoding="utf-8")
    return manifest


def test_manifest_preflight_is_pinned_and_ready(tmp_path):
    manifest = TheoremManifest.load(_manifest(tmp_path))
    report = manifest.preflight(tmp_path)
    assert report["status"] == "ready"
    assert report["dataset_commit"] == "minif2f-test-commit"
    assert report["entries"][0]["proof_hole_count"] == 1


def test_manifest_missing_source_is_blocked_not_zero(tmp_path):
    manifest_path = _manifest(tmp_path)
    manifest_path.unlink()
    manifest_path.write_text(json.dumps({
        "schema": SCHEMA, "dataset": "minif2f", "dataset_commit": "x",
        "lean_version": "4.29.0", "mathlib_commit": "y",
        "theorems": [{"id": "missing", "split": "test", "source": "missing.lean",
                      "theorem": "foo", "statement": "True"}],
    }), encoding="utf-8")
    report = TheoremManifest.load(manifest_path).preflight(tmp_path)
    assert report["status"] == "blocked"
    assert "source file is missing" in report["blocking_reasons"]


@pytest.mark.parametrize("proof", ["by sorry", "by admit", "by\n  run_tac exact 1", "by\n  set_option pp.all true in trivial"])
def test_proof_hole_rejects_non_verifying_commands(tmp_path, proof):
    record = TheoremManifest.load(_manifest(tmp_path)).theorems[0]
    with pytest.raises(ValueError):
        prepare_source(record, tmp_path, proof)


def test_hash_and_marker_are_checked_before_compile(tmp_path):
    manifest = TheoremManifest.load(_manifest(tmp_path)).theorems[0]
    source = tmp_path / manifest.source
    source.write_text("import Lean\ntheorem foo : True := by trivial\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source_sha256"):
        prepare_source(manifest, tmp_path, "by trivial")


def test_external_compiler_kernel_checks_candidate(tmp_path):
    record = TheoremManifest.load(_manifest(tmp_path)).theorems[0]
    compiler = ExternalLeanCompiler()
    if not compiler.lean or not Path(compiler.lean).is_file():
        pytest.skip("Lean unavailable")
    result = compiler.compile(record, tmp_path, "by trivial")
    assert result.status == "verified"
    assert result.proof_digest


def test_external_compiler_rejects_false_candidate(tmp_path):
    source = tmp_path / "foo.lean"
    source.write_text("import Lean\ntheorem foo : False := RESIMIND_PROOF_HOLE\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    entry = {"id": "foo", "split": "test", "source": "foo.lean", "theorem": "foo",
             "statement": "False", "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    manifest_path.write_text(json.dumps({"schema": SCHEMA, "dataset": "minif2f",
        "dataset_commit": "x", "lean_version": "4.29.0", "mathlib_commit": "y",
        "theorems": [entry]}), encoding="utf-8")
    record = TheoremManifest.load(manifest_path).theorems[0]
    compiler = ExternalLeanCompiler()
    if not compiler.lean or not Path(compiler.lean).is_file():
        pytest.skip("Lean unavailable")
    result = compiler.compile(record, tmp_path, "by trivial")
    assert result.status in {"unresolved", "error"}
