"""Pinned external Lean theorem manifests and a bounded compile adapter.

This module is deliberately a *dataset boundary*, not a general theorem
prover.  A manifest identifies a pinned MiniF2F/LeanDojo checkout and a
source file with exactly one ``RESIMIND_PROOF_HOLE`` marker.  The adapter
replaces that marker with a candidate ``by ...`` proof and asks an already
installed Lean binary to compile the file.  It never downloads data, calls a
model, or treats a missing dataset as a failed theorem.

The source file and its hash are part of the measurement record.  This keeps
future MiniF2F/LeanDojo runs reproducible while making the current support
boundary explicit: the source must be prepared in the pinned Lean project;
this adapter does not parse arbitrary Lean declarations or infer theorem
statements from an untrusted checkout.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from resimind.integrations.lean import _run_process


SCHEMA = "resimind.external-theorem-manifest.v1"
PROOF_HOLE = "RESIMIND_PROOF_HOLE"
SUPPORTED_DATASETS = frozenset({"minif2f", "leandojo"})
MAX_ENTRIES = 10_000
MAX_STATEMENT_BYTES = 256 * 1024
MAX_PROOF_BYTES = 256 * 1024
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_source(value: Any) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise ValueError("source must be a non-empty POSIX-relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.name != value.rsplit("/", 1)[-1]:
        raise ValueError("source must stay below the pinned dataset root")
    if path.suffix != ".lean":
        raise ValueError("source must be a .lean file")
    return value


@dataclass(frozen=True, slots=True)
class TheoremRecord:
    """One externally pinned theorem prepared with a proof hole."""

    id: str
    split: str
    source: str
    theorem: str
    statement: str
    imports: tuple[str, ...] = ()
    source_sha256: str | None = None
    proof_hole: str = PROOF_HOLE

    @classmethod
    def from_json(cls, item: Any) -> "TheoremRecord":
        if type(item) is not dict:
            raise ValueError("theorem entry must be an object")
        required = {"id", "split", "source", "theorem", "statement"}
        optional = {"imports", "source_sha256", "proof_hole"}
        if set(item) - required - optional or not required <= set(item):
            raise ValueError("invalid theorem entry fields")
        ident = item["id"]
        split = item["split"]
        theorem = item["theorem"]
        statement = item["statement"]
        if type(ident) is not str or not _ID.fullmatch(ident):
            raise ValueError("invalid theorem id")
        if type(split) is not str or not _ID.fullmatch(split) or len(split) > 32:
            raise ValueError("invalid theorem split")
        if type(theorem) is not str or not _IDENT.fullmatch(theorem):
            raise ValueError("invalid theorem name")
        if type(statement) is not str or not statement.strip() or len(statement.encode()) > MAX_STATEMENT_BYTES:
            raise ValueError("invalid theorem statement")
        imports = item.get("imports", [])
        if type(imports) is not list or any(type(value) is not str or not _IDENT.fullmatch(value)
                                             for value in imports) or len(imports) > 64:
            raise ValueError("invalid theorem imports")
        source_sha256 = item.get("source_sha256")
        if source_sha256 is not None and (type(source_sha256) is not str or not _SHA.fullmatch(source_sha256)):
            raise ValueError("source_sha256 must be lowercase hexadecimal")
        proof_hole = item.get("proof_hole", PROOF_HOLE)
        if type(proof_hole) is not str or not 1 <= len(proof_hole) <= 128 or "\n" in proof_hole:
            raise ValueError("invalid proof_hole")
        return cls(ident, split, _relative_source(item["source"]), theorem,
                   statement, tuple(imports), source_sha256, proof_hole)


@dataclass(frozen=True, slots=True)
class TheoremManifest:
    dataset: str
    dataset_commit: str
    lean_version: str
    mathlib_commit: str
    theorems: tuple[TheoremRecord, ...]
    schema: str = SCHEMA

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "TheoremManifest":
        path = Path(path)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("manifest is too large")
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
        if type(data) is not dict:
            raise ValueError("manifest must be an object")
        expected = {"schema", "dataset", "dataset_commit", "lean_version", "mathlib_commit", "theorems"}
        if set(data) != expected or data["schema"] != SCHEMA:
            raise ValueError("unsupported external theorem manifest schema")
        dataset = data["dataset"]
        if type(dataset) is not str or dataset not in SUPPORTED_DATASETS:
            raise ValueError("dataset must be minif2f or leandojo")
        values = (data["dataset_commit"], data["lean_version"], data["mathlib_commit"])
        if any(type(value) is not str or not value or len(value) > 256 for value in values):
            raise ValueError("manifest pins must be non-empty strings")
        entries = data["theorems"]
        if type(entries) is not list or not entries or len(entries) > MAX_ENTRIES:
            raise ValueError("manifest must contain between 1 and 10000 theorems")
        theorems = tuple(TheoremRecord.from_json(item) for item in entries)
        if len({item.id for item in theorems}) != len(theorems):
            raise ValueError("duplicate theorem id")
        return cls(dataset, data["dataset_commit"], data["lean_version"],
                   data["mathlib_commit"], theorems)

    def preflight(self, root: str | os.PathLike[str], *, lean: str | None = None) -> dict[str, Any]:
        """Check pinned files without downloading or compiling them."""
        root = Path(root).expanduser().resolve()
        entries = []
        for theorem in self.theorems:
            path = (root / theorem.source).resolve()
            reasons: list[str] = []
            try:
                path.relative_to(root)
            except ValueError:
                reasons.append("source escapes dataset root")
            if not path.is_file():
                reasons.append("source file is missing")
            digest = _sha256(path) if path.is_file() else None
            if theorem.source_sha256 and digest != theorem.source_sha256:
                reasons.append("source_sha256 does not match")
            marker_count = 0
            if path.is_file():
                try:
                    marker_count = path.read_text(encoding="utf-8").count(theorem.proof_hole)
                except (OSError, UnicodeError):
                    reasons.append("source is not valid UTF-8")
            if marker_count != 1:
                reasons.append("source must contain exactly one proof hole")
            entries.append({"id": theorem.id, "source": theorem.source,
                            "present": path.is_file(), "sha256": digest,
                            "proof_hole_count": marker_count,
                            "status": "ready" if not reasons else "blocked",
                            "blocking_reasons": reasons})
        lean_result = _lean_probe(lean, self.lean_version)
        reasons = [item for entry in entries for item in entry["blocking_reasons"]]
        reasons.extend(lean_result["blocking_reasons"])
        return {"schema": SCHEMA, "dataset": self.dataset,
                "dataset_commit": self.dataset_commit,
                "lean_version": self.lean_version, "mathlib_commit": self.mathlib_commit,
                "root": str(root), "count": len(entries),
                "status": "ready" if not reasons else "blocked",
                "lean": lean_result, "entries": entries,
                "blocking_reasons": sorted(set(reasons))}


def _lean_probe(lean: str | None, required_version: str) -> dict[str, Any]:
    executable = str(Path(lean).expanduser().resolve()) if lean else shutil.which("lean")
    if not executable or not Path(executable).is_file():
        return {"binary": executable, "version": None,
                "status": "blocked", "blocking_reasons": ["Lean executable is missing"]}
    # Never execute an elan shim: if its pinned toolchain is absent elan may
    # download it.  The benchmark adapter is intentionally read-only.
    executable_path = Path(executable)
    if executable_path.parent.name == "bin" and executable_path.parent.parent.name == ".elan":
        installed = (executable_path.parent.parent / "toolchains"
                     / f"leanprover--lean4---v{required_version}" / "bin" / "lean")
        if not installed.is_file():
            return {"binary": executable, "version": None, "status": "blocked",
                    "blocking_reasons": [f"Lean {required_version} toolchain is not installed"]}
        executable = str(installed)
    try:
        result = subprocess.run([executable, "--version"], capture_output=True, text=True,
                                timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"binary": executable, "version": None, "status": "blocked",
                "blocking_reasons": ["Lean version probe failed"]}
    output = (result.stdout or result.stderr).strip()
    match = re.search(r"Lean \(version ([0-9]+\.[0-9]+\.[0-9]+)", output)
    version = match.group(1) if match else None
    reasons = [] if version == required_version else [f"expected Lean {required_version}"]
    return {"binary": executable, "version": version,
            "status": "ready" if not reasons else "blocked",
            "blocking_reasons": reasons}


def _validate_proof(proof: str) -> None:
    if type(proof) is not str or not proof.strip() or len(proof.encode()) > MAX_PROOF_BYTES:
        raise ValueError("proof must be a bounded non-empty string")
    candidate = proof.strip()
    if not candidate.startswith("by") or (len(candidate) > 2 and not candidate[2].isspace()):
        raise ValueError("proof must be a Lean `by` block")
    # These commands are outside the proof-hole contract.  This is a syntax
    # boundary, not a sandbox; the pinned Lean project remains the trust root.
    lowered = candidate.lower()
    for token in ("sorry", "admit", "axiom", "import ", "#eval", "#check",
                  "set_option", "unsafe", "run_tac", "elab"):
        if token in lowered:
            raise ValueError(f"proof contains forbidden token: {token.strip()}")


def prepare_source(record: TheoremRecord, root: str | os.PathLike[str], proof: str) -> tuple[str, str]:
    """Return candidate source and its digest after hash/marker validation."""
    _validate_proof(proof)
    root = Path(root).expanduser().resolve()
    source_path = (root / record.source).resolve()
    try:
        source_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("source escapes dataset root") from exc
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    source = source_path.read_text(encoding="utf-8")
    if record.source_sha256 and _sha256(source_path) != record.source_sha256:
        raise ValueError("source_sha256 does not match")
    if source.count(record.proof_hole) != 1:
        raise ValueError("source must contain exactly one proof hole")
    rendered = source.replace(record.proof_hole, proof, 1)
    return rendered, hashlib.sha256(rendered.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExternalProofResult:
    theorem_id: str
    status: str
    diagnostics: tuple[str, ...] = ()
    source_digest: str = ""
    proof_digest: str | None = None
    lean_version: str | None = None


class ExternalLeanCompiler:
    """Compile one prepared theorem with a pinned, already installed Lean."""

    def __init__(self, lean: str | os.PathLike[str] | None = None, *,
                 timeout_seconds: float = 30.0, max_output_bytes: int = 131072):
        if not 0 < float(timeout_seconds) <= 120:
            raise ValueError("timeout_seconds must be in (0, 120]")
        if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 1024 * 1024:
            raise ValueError("max_output_bytes is outside its bounded range")
        self.lean = str(Path(lean).expanduser().resolve()) if lean else shutil.which("lean")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes

    def compile(self, record: TheoremRecord, root: str | os.PathLike[str], proof: str,
                *, project_root: str | os.PathLike[str] | None = None,
                required_version: str = "4.29.0") -> ExternalProofResult:
        try:
            source, source_digest = prepare_source(record, root, proof)
        except (OSError, UnicodeError, ValueError) as exc:
            return ExternalProofResult(record.id, "error", (str(exc),))
        if not self.lean or not Path(self.lean).is_file():
            return ExternalProofResult(record.id, "unavailable", ("Lean executable is missing",), source_digest)
        version = _lean_probe(self.lean, required_version)
        if version["status"] != "ready":
            return ExternalProofResult(record.id, "unavailable",
                                       tuple(version["blocking_reasons"]), source_digest,
                                       lean_version=version["version"])
        with tempfile.TemporaryDirectory(prefix="resimind-external-lean-") as temporary:
            directory = Path(temporary)
            source_path = directory / "Main.lean"
            artifact = directory / "Main.olean"
            source_path.write_text(source + f"\n#print axioms {record.theorem}\n", encoding="utf-8")
            cwd = Path(project_root).expanduser().resolve() if project_root else directory
            result = _run_process([version["binary"], "--json", "-M", "2048", "-j", "1",
                                   "-o", str(artifact), str(source_path)],
                                  cwd=cwd, timeout=self.timeout_seconds,
                                  max_output_bytes=self.max_output_bytes)
            output = "\n".join(part for part in (result.stdout, result.stderr) if part)
            diagnostics = tuple(line for line in output.splitlines() if line.strip())
            if result.failure:
                return ExternalProofResult(record.id, "error", (result.failure,), source_digest,
                                           lean_version=version["version"])
            if result.returncode != 0:
                return ExternalProofResult(record.id, "unresolved", diagnostics or ("Lean compilation failed",),
                                           source_digest, lean_version=version["version"])
            if "sorryAx" in output or "declaration uses 'sorry'" in output.lower():
                return ExternalProofResult(record.id, "unresolved",
                                           diagnostics + ("compiled source contains sorryAx",), source_digest,
                                           lean_version=version["version"])
            if not artifact.is_file() or artifact.stat().st_size <= 0:
                return ExternalProofResult(record.id, "error", ("Lean produced no proof artifact",), source_digest,
                                           lean_version=version["version"])
            return ExternalProofResult(record.id, "verified", (), source_digest,
                                       hashlib.sha256(artifact.read_bytes()).hexdigest(), version["version"])


__all__ = ["SCHEMA", "PROOF_HOLE", "TheoremRecord", "TheoremManifest",
           "ExternalProofResult", "ExternalLeanCompiler", "prepare_source"]
