"""Optional, bounded Lean 4 checks for the existing rational polynomial grammar.

Only a generated theorem and the fixed ``rfl`` / ``grind`` tactics reach Lean.
``grind`` is Lean's built-in tactic, including its ring solver; this adapter
does not claim to provide Mathlib's ``ring`` or ``ring_nf`` tactics. Lean is an
external, trusted installation, not a Python dependency. No model-supplied Lean
source is accepted, and a failed or unavailable check never becomes a proof.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Sequence

from ..core import content_digest
from ..domains.algebra import PolynomialError, validate_expression

SUPPORTED_LEAN_VERSION = "4.29.0"
ALLOWED_TACTICS = ("rfl", "grind")
APPROVED_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
_THEOREM = "resimind_checked"
_MAX_PROOF_BYTES = 16 * 1024 * 1024


def polynomial_binding_digest(before: str, after: str, variables: Sequence[str]) -> str:
    """Bind feedback to the exact two expressions and ordered variable names."""
    return content_digest({"before": before, "after": after, "variables": tuple(variables)})


@dataclass(frozen=True, slots=True)
class LeanProofResult:
    status: str
    tactic: str
    target: str
    actual_target: str
    goals: tuple[str, ...]
    diagnostics: tuple[str, ...]
    binding_digest: str
    source_digest: str
    proof_digest: str | None = None
    axioms: tuple[str, ...] = ()
    lean_version: str | None = None
    variable_mapping: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    failure: str | None = None


def _run_process(command: list[str], *, cwd: Path, timeout: float,
                 max_output_bytes: int) -> _ProcessResult:
    """Bound wall time and captured bytes without buffering unbounded output."""
    environment = os.environ.copy()
    # Resolve imports from this installation, never an ambient project path.
    for name in ("LEAN_PATH", "LEAN_SRC_PATH", "LEAN_SYSROOT"):
        environment.pop(name, None)
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                       start_new_session=(os.name == "posix"))
        except (OSError, ValueError):
            return _ProcessResult(failure="Lean executable could not be started")
        deadline = time.monotonic() + timeout
        failure = None
        while process.poll() is None:
            if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > max_output_bytes:
                failure = "Lean output exceeded the byte limit; truncated feedback is not a proof"
            elif time.monotonic() >= deadline:
                failure = "Lean check timed out; no proof was accepted"
            if failure:
                try:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
                break
            time.sleep(0.01)
        if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > max_output_bytes:
            failure = "Lean output exceeded the byte limit; truncated feedback is not a proof"
        output.seek(0)
        errors.seek(0)
        try:
            stdout = output.read(max_output_bytes).decode("utf-8", errors="strict")
            stderr = errors.read(max_output_bytes).decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _ProcessResult(process.returncode, failure="Lean returned invalid UTF-8 output")
        return _ProcessResult(process.returncode, stdout, stderr, failure)


def _render(node: ast.expr, names: dict[str, str]) -> str:
    """Translate a validated AST, never interpolate original source tokens."""
    if isinstance(node, ast.Name):
        return names[node.id]
    if isinstance(node, ast.Constant):
        return f"({node.value} : Rat)"
    if isinstance(node, ast.UnaryOp):
        operand = _render(node.operand, names)
        return operand if isinstance(node.op, ast.UAdd) else f"(-{operand})"
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Pow):
            exponent = node.right
            if isinstance(exponent, ast.UnaryOp):
                value = exponent.operand.value
                value = -value if isinstance(exponent.op, ast.USub) else value
            else:
                value = exponent.value
            return f"({_render(node.left, names)} ^ {value})"
        operator = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}[type(node.op)]
        return f"({_render(node.left, names)} {operator} {_render(node.right, names)})"
    raise PolynomialError("unsupported expression for Lean translation")


def _source(before: str, after: str, variables: Sequence[str], tactic: str,
            max_heartbeats: int) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    if type(tactic) is not str or tactic not in ALLOWED_TACTICS:
        raise ValueError("Lean tactic must be exactly 'rfl' or 'grind'")
    validate_expression(before, variables)
    validate_expression(after, variables)
    mapping = tuple((name, f"v{index}") for index, name in enumerate(variables))
    names = dict(mapping)
    lhs = _render(ast.parse(before.strip(), mode="eval").body, names)
    rhs = _render(ast.parse(after.strip(), mode="eval").body, names)
    target = f"{lhs} = {rhs}"
    binders = " ".join(f"({renamed} : Rat)" for _, renamed in mapping)
    # Keep every task variable bound even after cancellation removes it from
    # both expressions. Silence only this theorem's cosmetic unused-binder
    # linter; every emitted warning/error still fails the diagnostic gate.
    source = ("import Lean\n"
              "set_option maxRecDepth 512\n"
              f"set_option maxHeartbeats {max_heartbeats}\n"
              "set_option linter.unusedVariables false in\n"
              f"theorem {_THEOREM} {binders} : {target} := by\n"
              "  trace_state\n"
              f"  {tactic}\n"
              f"#print axioms {_THEOREM}\n")
    return source, target, mapping


class LeanPolynomialBackend:
    """Execute a real Lean kernel check with fixed syntax and bounded resources.

    A successful result requires compiler success, complete JSON diagnostics,
    an actual Lean target, a generated ``.olean`` file, and exactly one axiom
    report for this theorem containing only the approved standard axioms.
    The ``proof_digest`` hashes that compiled proof artifact, not a claim that
    the tactic ran. Unresolved tactics expose Lean's remaining goals so an
    Agent can change its next proposal or tactic. No source is executed with
    ``--run`` and no package is downloaded by this adapter.
    """

    def __init__(self, lean_path: str | os.PathLike[str] | None = None, *,
                 timeout_seconds: float = 10.0, max_output_bytes: int = 65536,
                 max_heartbeats: int = 200000, memory_megabytes: int = 2048,
                 required_version: str = SUPPORTED_LEAN_VERSION):
        if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 120):
            raise ValueError("timeout_seconds must be finite and in (0, 120]")
        for name, value, lower, upper in (
            ("max_output_bytes", max_output_bytes, 1024, 1024 * 1024),
            ("max_heartbeats", max_heartbeats, 1000, 1000000),
            ("memory_megabytes", memory_megabytes, 128, 4096),
        ):
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} is outside its bounded range")
        if required_version != SUPPORTED_LEAN_VERSION:
            raise ValueError(f"this backend is validated for Lean {SUPPORTED_LEAN_VERSION}")
        if lean_path is None:
            installed = (Path.home() / ".elan" / "toolchains"
                         / f"leanprover--lean4---v{SUPPORTED_LEAN_VERSION}" / "bin" / "lean")
            lean_path = str(installed) if installed.is_file() else shutil.which("lean")
        # Resolve aliases before recognizing elan's shims. A PATH entry such
        # as /usr/local/bin/lean may point through several symlinks to elan;
        # executing that shim even for --version could download a toolchain.
        try:
            self.lean_path = None if lean_path is None else str(Path(lean_path).expanduser().resolve())
        except (OSError, RuntimeError) as exc:
            raise ValueError("Lean executable path could not be resolved") from exc
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_bytes = max_output_bytes
        self.max_heartbeats = max_heartbeats
        self.memory_megabytes = memory_megabytes
        self.required_version = required_version

    def check(self, before: str, after: str, variables: Sequence[str], *,
              tactic: str = "grind") -> LeanProofResult:
        """Check this bound equality; never silently fall back to another verifier."""
        # Invalid inputs fail closed before launching the external executable.
        try:
            binding = polynomial_binding_digest(before, after, variables)
        except (TypeError, ValueError, RecursionError):
            return LeanProofResult("error", str(tactic)[:64], "", "", (),
                                   ("Invalid Lean polynomial request binding",), "", "")
        try:
            source, target, mapping = _source(before, after, variables, tactic, self.max_heartbeats)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            return LeanProofResult("error", str(tactic)[:64], "", "", (),
                                   (f"Invalid Lean polynomial request: {exc}",), binding, "")
        source_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()

        def result(status, diagnostics, *, actual_target="", goals=(), axioms=(),
                   proof_digest=None, version=None):
            return LeanProofResult(status, tactic, target, actual_target, tuple(goals),
                                   tuple(diagnostics), binding, source_digest, proof_digest,
                                   tuple(axioms), version, mapping)

        if self.lean_path is None or not Path(self.lean_path).is_file():
            return result("unavailable", ("Lean executable is not installed or not found",))
        with tempfile.TemporaryDirectory(prefix="resimind-lean-") as temporary:
            directory = Path(temporary)
            # Do not let elan infer a project or download a toolchain. Resolve
            # an elan shim to an already installed pinned binary when possible.
            executable = self.lean_path
            if Path(executable).parent.name == "bin" and Path(executable).parent.parent.name == ".elan":
                installed = (Path(executable).parent.parent / "toolchains"
                             / f"leanprover--lean4---v{self.required_version}" / "bin" / "lean")
                if not installed.is_file():
                    return result("unavailable", (f"Lean {self.required_version} is not installed",))
                executable = str(installed)
            version_result = _run_process([executable, "--version"], cwd=directory,
                                          timeout=min(self.timeout_seconds, 5.0),
                                          max_output_bytes=self.max_output_bytes)
            if version_result.failure or version_result.returncode != 0 or version_result.stderr:
                return result("unavailable", (version_result.failure or "Lean version check failed",))
            match = re.fullmatch(r"Lean \(version ([0-9]+\.[0-9]+\.[0-9]+)(?:, [^\r\n]*)?\)\s*",
                                 version_result.stdout)
            version = match.group(1) if match else None
            if version != self.required_version:
                return result("unavailable", (f"Expected Lean {self.required_version}; installed version is unsupported",),
                              version=version)
            (directory / "Proof.lean").write_text(source, encoding="utf-8")
            compilation = _run_process(
                [executable, "--json", "-M", str(self.memory_megabytes), "-j", "1",
                 "-o", "Proof.olean", "Proof.lean"], cwd=directory,
                timeout=self.timeout_seconds, max_output_bytes=self.max_output_bytes)
            if compilation.failure:
                return result("error", (compilation.failure,), version=version)
            if compilation.stderr:
                return result("error", ("Lean emitted unexpected stderr; no proof was accepted",), version=version)
            try:
                messages = [json.loads(line) for line in compilation.stdout.splitlines() if line.strip()]
                if not messages or any(type(item) is not dict or type(item.get("data")) is not str
                                       or item.get("severity") not in {"information", "warning", "error"}
                                       for item in messages):
                    raise ValueError
            except (ValueError, TypeError):
                return result("error", ("Lean returned incomplete or malformed diagnostics",), version=version)
            actual_targets = [item["data"] for item in messages
                              if item.get("kind") == "trace" and item["severity"] == "information"
                              and "⊢" in item["data"]]
            if len(actual_targets) != 1:
                return result("error", ("Lean did not return exactly one bound target",), version=version)
            actual_target = actual_targets[0]
            diagnostics = tuple(item["data"] for item in messages if item["severity"] in {"error", "warning"})
            goals = tuple(item["data"].split("\n\n")[-1] for item in messages
                          if item["severity"] == "error" and "⊢" in item["data"])
            axiom_reports = []
            for item in messages:
                if item["severity"] != "information":
                    continue
                data = item["data"]
                if data == f"'{_THEOREM}' does not depend on any axioms":
                    axiom_reports.append(())
                else:
                    axiom_match = re.fullmatch(rf"'{_THEOREM}' depends on axioms: \[([^\]]*)\]", data)
                    if axiom_match:
                        axiom_reports.append(tuple(part.strip() for part in axiom_match.group(1).split(",")
                                                   if part.strip()))
            axioms = axiom_reports[0] if len(axiom_reports) == 1 else ()
            if compilation.returncode != 0 or diagnostics:
                # Lean may add sorryAx while recovering from tactic errors.
                # Those declarations are never reusable successful proofs.
                status = "unresolved" if goals else "error"
                return result(status, diagnostics or ("Lean compilation failed",),
                              actual_target=actual_target, goals=goals, axioms=axioms, version=version)
            if len(axiom_reports) != 1 or not set(axioms) <= APPROVED_AXIOMS:
                return result("error", ("Lean proof contains missing, ambiguous, or unapproved axiom evidence",),
                              actual_target=actual_target, axioms=axioms, version=version)
            artifact = directory / "Proof.olean"
            if not artifact.is_file() or not 0 < artifact.stat().st_size <= _MAX_PROOF_BYTES:
                return result("error", ("Lean did not produce a bounded compiled proof artifact",),
                              actual_target=actual_target, axioms=axioms, version=version)
            proof_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            return result("verified", (), actual_target=actual_target, axioms=axioms,
                          proof_digest=proof_digest, version=version)
