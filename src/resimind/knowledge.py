"""Domain-verified, persistent knowledge with explicit applicability conditions.

The configured verifier is the trust boundary: evidence references, source task
IDs, model confidence and serialized ``verified`` flags are not proofs. Every
admission and reload invokes the current domain verifier. Applications remain
responsible for supplying a sound verifier and checking that assumptions hold
in a new task. This local JSON store is not authenticated or safe for concurrent
writers; its fingerprint detects accidental changes, not a malicious editor.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Protocol


_SCHEMA_VERSION = 1
_ADMISSION_VERIFIER = "resimind.knowledge/admission-v1"
_VERDICTS = {"verified", "rejected", "unknown"}
_CANDIDATE_FIELDS = {
    "id", "domain", "kind", "statement", "assumptions", "derivation",
    "source_task_id", "evidence_refs",
}


def _text(value: Any, name: str, *, empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if value != value.strip() or (not value and not empty):
        raise ValueError(f"{name} must be nonempty with no surrounding whitespace")
    return value


def _names(value: Any, name: str) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple of strings")
    for item in value:
        _text(item, name)
    return value


def _validate_json(value: Any, active: set[int] | None = None) -> None:
    """Accept only plain JSON types, without coercion or nonfinite numbers."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("knowledge cannot contain NaN or infinity")
        return
    if type(value) not in (list, dict):
        raise TypeError("knowledge must contain only plain JSON types")
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError("knowledge cannot contain cycles")
    active.add(identity)
    try:
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise TypeError("JSON object keys must be strings")
                _validate_json(item, active)
        else:
            for item in value:
                _validate_json(item, active)
    finally:
        active.remove(identity)


def _canonical(value: Any) -> str:
    _validate_json(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_dict(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise TypeError(f"{name} must be a plain dictionary")
    return json.loads(_canonical(value))


@dataclass(frozen=True)
class KnowledgeCandidate:
    """An untrusted claim, its conditions and a domain-specific derivation.

    Nested JSON values are copied at construction and at every library boundary.
    They remain ordinary editable dictionaries on this detached value object.
    ``source_task_id`` records provenance and is required for admission; its
    presence and ``evidence_refs`` do not establish mathematical correctness.
    """

    id: str
    domain: str
    kind: str
    statement: dict[str, Any]
    assumptions: tuple[str, ...] = ()
    derivation: tuple[dict[str, Any], ...] = ()
    source_task_id: str = ""
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("id", "domain", "kind"):
            _text(getattr(self, name), name)
        _text(self.source_task_id, "source_task_id", empty=True)
        _names(self.assumptions, "assumptions")
        _names(self.evidence_refs, "evidence_refs")
        if type(self.derivation) is not tuple:
            raise TypeError("derivation must be a tuple of dictionaries")
        object.__setattr__(self, "statement", _json_dict(self.statement, "statement"))
        object.__setattr__(self, "derivation", tuple(
            _json_dict(step, "derivation step") for step in self.derivation
        ))


@dataclass(frozen=True)
class Verification:
    status: str
    reason: str
    verifier_id: str

    def __post_init__(self) -> None:
        _text(self.status, "status")
        if self.status not in _VERDICTS:
            raise ValueError("verification status must be verified, rejected or unknown")
        _text(self.reason, "reason")
        _text(self.verifier_id, "verifier_id")


class KnowledgeVerifier(Protocol):
    def verify(self, candidate: KnowledgeCandidate) -> Verification:
        """Independently check the claim under its explicit assumptions."""
        ...


@dataclass(frozen=True)
class KnowledgeRecord:
    candidate: KnowledgeCandidate
    verification: Verification
    status: str
    fingerprint: str

    def __post_init__(self) -> None:
        candidate = _clone(self.candidate)
        if type(self.verification) is not Verification:
            raise TypeError("verification must be a Verification")
        verification = Verification(
            self.verification.status, self.verification.reason, self.verification.verifier_id,
        )
        _text(self.status, "record status")
        if self.status != "revoked" and self.status != verification.status:
            raise ValueError("record status must match its verification or be revoked")
        if type(self.fingerprint) is not str or self.fingerprint != _fingerprint(candidate):
            raise ValueError("record fingerprint does not match its candidate")
        object.__setattr__(self, "candidate", candidate)
        object.__setattr__(self, "verification", verification)


def _candidate_payload(candidate: KnowledgeCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id, "domain": candidate.domain, "kind": candidate.kind,
        "statement": candidate.statement, "assumptions": list(candidate.assumptions),
        "derivation": list(candidate.derivation), "source_task_id": candidate.source_task_id,
        "evidence_refs": list(candidate.evidence_refs),
    }


def _candidate_from_payload(payload: Any) -> KnowledgeCandidate:
    if type(payload) is not dict or set(payload) != _CANDIDATE_FIELDS:
        raise ValueError("invalid knowledge candidate fields")
    for name in ("assumptions", "derivation", "evidence_refs"):
        if type(payload[name]) is not list:
            raise TypeError(f"serialized {name} must be a list")
    return KnowledgeCandidate(**{
        **payload,
        **{name: tuple(payload[name]) for name in ("assumptions", "derivation", "evidence_refs")},
    })


def _clone(candidate: KnowledgeCandidate) -> KnowledgeCandidate:
    if type(candidate) is not KnowledgeCandidate:
        raise TypeError("candidate must be a KnowledgeCandidate")
    # Revalidate even frozen instances: their nested values may have been edited.
    return KnowledgeCandidate(**{
        name: getattr(candidate, name) for name in _CANDIDATE_FIELDS
    })


def _fingerprint(candidate: KnowledgeCandidate) -> str:
    return sha256(_canonical(_candidate_payload(candidate)).encode("utf-8")).hexdigest()


def _detach(record: KnowledgeRecord) -> KnowledgeRecord:
    return KnowledgeRecord(record.candidate, record.verification, record.status, record.fingerprint)


class KnowledgeLibrary:
    """Admit only verifier-approved knowledge, with terminal revocation.

    Domain names select verifiers; registration must come from trusted application
    code. Lookup conditions are exact string matches supplied by the caller, not
    a proof that the conditions hold. Reusing a rule still requires checking its
    instantiation and applicability in the current task.
    """

    def __init__(self, verifiers: Mapping[str, KnowledgeVerifier]) -> None:
        if not isinstance(verifiers, Mapping):
            raise TypeError("verifiers must be a domain-to-verifier mapping")
        self._verifiers = dict(verifiers)
        for domain, verifier in self._verifiers.items():
            _text(domain, "verifier domain")
            if not callable(getattr(verifier, "verify", None)):
                raise TypeError("each verifier must provide a callable verify method")
        self._records: dict[str, KnowledgeRecord] = {}
        self._revocations: dict[str, str] = {}

    def admit(self, candidate: KnowledgeCandidate) -> KnowledgeRecord:
        """Verify a detached candidate; retain rejected and unknown outcomes too."""
        stored = _clone(candidate)
        if stored.id in self._records:
            raise ValueError(f"knowledge ID already exists: {stored.id}")
        fingerprint = _fingerprint(stored)
        verification = self._verify(stored, fingerprint)
        record = KnowledgeRecord(stored, verification, verification.status, fingerprint)
        self._records[stored.id] = record
        return _detach(record)

    def _verify(self, candidate: KnowledgeCandidate, fingerprint: str) -> Verification:
        def unknown(reason: str) -> Verification:
            return Verification("unknown", reason, _ADMISSION_VERIFIER)

        if not candidate.source_task_id:
            return unknown("source_task_id is required for admission")
        verifier = self._verifiers.get(candidate.domain)
        if verifier is None:
            return unknown("no verifier is registered for this domain")
        submitted = _clone(candidate)
        try:
            result = verifier.verify(submitted)
            if _fingerprint(submitted) != fingerprint:
                return unknown("verifier mutated its candidate input")
            if type(result) is not Verification:
                return unknown("verifier returned a malformed verdict")
            # Reconstruct to validate even an object modified through object.__setattr__.
            return Verification(result.status, result.reason, result.verifier_id)
        except Exception as exc:
            return unknown(f"verifier failed ({type(exc).__name__})")

    def get(self, id: str) -> KnowledgeRecord:
        """Return a detached record, or raise KeyError for an unknown ID."""
        _text(id, "id")
        return _detach(self._records[id])

    def lookup(
        self, domain: str, kind: str | None = None, assumptions: tuple[str, ...] = (),
    ) -> tuple[KnowledgeRecord, ...]:
        """Return verified records with conditions contained in the query."""
        _text(domain, "domain")
        if kind is not None:
            _text(kind, "kind")
        available = set(_names(assumptions, "assumptions"))
        return tuple(
            _detach(record) for record in self._records.values()
            if record.status == "verified" and record.candidate.domain == domain
            and (kind is None or record.candidate.kind == kind)
            and set(record.candidate.assumptions).issubset(available)
        )

    def revoke(self, id: str, reason: str) -> KnowledgeRecord:
        """Retire any recorded outcome; its ID cannot be admitted again."""
        _text(reason, "reason")
        record = self.get(id)
        if record.status == "revoked":
            raise ValueError("knowledge is already revoked")
        retired = KnowledgeRecord(record.candidate, record.verification, "revoked", record.fingerprint)
        self._records[id] = retired
        self._revocations[id] = reason
        return _detach(retired)

    def save(self, path: str | os.PathLike[str]) -> None:
        """Atomically replace a JSON file in an existing parent directory."""
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "records": [{
                "candidate": _candidate_payload(record.candidate),
                "verification": {
                    "status": record.verification.status, "reason": record.verification.reason,
                    "verifier_id": record.verification.verifier_id,
                },
                "status": record.status, "fingerprint": record.fingerprint,
                "revocation_reason": self._revocations.get(record.candidate.id),
            } for record in self._records.values()],
        }
        encoded = _canonical(payload)
        destination = Path(path)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent,
                prefix=f".{destination.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = handle.name
                handle.write(encoded + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                os.unlink(temporary)

    @classmethod
    def load(
        cls, path: str | os.PathLike[str], verifiers: Mapping[str, KnowledgeVerifier],
    ) -> KnowledgeLibrary:
        """Reverify every stored candidate; saved approval is never authoritative.

        Invalid schemas and fingerprints raise without returning a partial store.
        Revocation is preserved, but the file is not a tamper-proof revocation log.
        """
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError(f"duplicate JSON key: {key}")
                result[key] = value
            return result

        payload = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs)
        _validate_json(payload)
        if type(payload) is not dict or set(payload) != {"schema_version", "records"}:
            raise ValueError("invalid knowledge library schema")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != _SCHEMA_VERSION:
            raise ValueError("unsupported knowledge library schema version")
        if type(payload["records"]) is not list:
            raise TypeError("records must be a list")
        library = cls(verifiers)
        for row in payload["records"]:
            if type(row) is not dict or set(row) != {
                "candidate", "verification", "status", "fingerprint", "revocation_reason",
            }:
                raise ValueError("invalid knowledge record fields")
            _text(row["status"], "stored status")
            if row["status"] not in _VERDICTS | {"revoked"}:
                raise ValueError("invalid stored status")
            prior = row["verification"]
            if type(prior) is not dict or set(prior) != {"status", "reason", "verifier_id"}:
                raise ValueError("invalid stored verification")
            Verification(**prior)  # Validate shape, then discard the untrusted verdict.
            candidate = _candidate_from_payload(row["candidate"])
            if type(row["fingerprint"]) is not str or row["fingerprint"] != _fingerprint(candidate):
                raise ValueError("knowledge candidate fingerprint mismatch")
            if row["status"] == "revoked":
                _text(row["revocation_reason"], "revocation reason")
            elif row["revocation_reason"] is not None:
                raise ValueError("only revoked records may have a revocation reason")
            library.admit(candidate)
            if row["status"] == "revoked":
                library.revoke(candidate.id, row["revocation_reason"])
        return library
