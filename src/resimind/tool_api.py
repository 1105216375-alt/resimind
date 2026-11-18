"""Bounded, model-free tools shared by JSON-lines and the optional MCP server."""
from __future__ import annotations

from dataclasses import asdict
import json
from threading import Lock

from .domains.algebra import MAX_SOURCE_LENGTH, PolynomialError, verify_identity
from .domains.compact_algebra import run_compact
from .domains.lifelong_math import LifelongMathAgent
from .domains.polynomial_learning import PolynomialProblem
from .tool_memory import ToolMemory

SCOPE = "bounded rational polynomial algebra; exact coefficient checks, not Lean proofs"
MAX_REQUEST = 32768
DESCRIPTIONS = {
    "verify_identity": "Check whether two bounded rational polynomials are identically equal; return a coefficient mismatch when false.",
    "expand_polynomial": "Expand a bounded rational polynomial into independently checked sparse terms without calling a model.",
    "learn_polynomial": "Solve an expansion and explicitly update rule memory; compatible examples may yield a universally checked schema.",
    "recall_polynomial": "Try only verified rules in memory, rechecking the application; no fallback computation or model call.",
    "list_rules": "List verified rule statements and fingerprints; listing is not proof of any new task.",
}


def tool_catalog(memory_scope: str = "process") -> dict:
    expression = {"type": "string", "minLength": 1, "maxLength": MAX_SOURCE_LENGTH}
    variables = {"type": "array", "maxItems": 16, "uniqueItems": True,
                 "items": {"type": "string", "maxLength": 64, "pattern": "^[A-Za-z][A-Za-z0-9_]*$"}}
    tools = []
    for name, description in DESCRIPTIONS.items():
        fields = {} if name == "list_rules" else ({"lhs": expression, "rhs": expression, "variables": variables}
            if name == "verify_identity" else {"expression": expression, "variables": variables})
        tools.append({"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": fields,
                "required": list(fields), "additionalProperties": False},
            "readOnly": name != "learn_polynomial"})
    return {"schema_version": 1, "scope": SCOPE, "memory_scope": memory_scope, "tools": tools}


def _response(status, reason, **data):
    return {"schema_version": 1, "status": status, "reason": reason, "scope": SCOPE,
            "model_calls": 0, "memory_scope": "process", **data}


def _verdict(check):
    return "unresolved" if check.status == "unknown" else check.status


def _certificate(certificate):
    if certificate is None:
        return None
    # Decimal strings preserve exact coefficients in JavaScript and other clients.
    return {"source": certificate.source, "variables": list(certificate.variables),
        "fingerprint": certificate.fingerprint,
        "terms": [{"powers": list(t.powers), "numerator": str(t.numerator),
                   "denominator": str(t.denominator)} for t in certificate.terms]}


class ToolSession:
    """Isolated memory by default; an explicit directory persists verified learning."""

    def __init__(self, memory_dir=None):
        self._lock = Lock()
        self._memory = ToolMemory(memory_dir) if memory_dir is not None else None
        try:
            self.agent = self._memory.load() if self._memory else LifelongMathAgent()
        except BaseException:
            self.close()
            raise
        self._save_failed = False
        self._closed = False

    def close(self):
        """Release the optional memory directory for the next process."""
        with self._lock:
            if self._memory is not None:
                self._memory.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def call(self, name, arguments):
        response = self._dispatch(name, arguments)
        response['memory_scope'] = 'directory' if self._memory else 'process'
        return response

    def _dispatch(self, name, arguments):
        if type(name) is not str or name not in DESCRIPTIONS:
            return _response("error", "unknown tool")
        fields = set() if name == "list_rules" else (
            {"lhs", "rhs", "variables"} if name == "verify_identity" else {"expression", "variables"})
        if type(arguments) is not dict or set(arguments) != fields:
            return _response("error", "arguments must contain exactly: " + ", ".join(sorted(fields)))
        if fields:
            for field in fields - {"variables"}:
                value = arguments[field]
                if type(value) is not str or not 0 < len(value) <= MAX_SOURCE_LENGTH:
                    return _response("error", f"{field} must be a string of 1..{MAX_SOURCE_LENGTH} characters")
            variables = arguments["variables"]
            if type(variables) is not list or len(variables) > 16 or any(type(v) is not str or len(v) > 64 for v in variables):
                return _response("error", "variables must be a list of at most 16 short names")
            check = verify_identity("0", "0", variables)
            if check.status != "verified":
                return _response("error", check.reason)
        with self._lock:
            if self._closed:
                return _response("error", "tool session is closed")
            if self._save_failed:
                return _response("error", "memory save failed; restart after checking the snapshot directory")
            if name == "learn_polynomial" and self.agent.frozen:
                return _response("error", "memory snapshot is frozen; learning is disabled")
            try:
                response = self._call(name, arguments)
                if self._memory and name == "learn_polynomial" and response['status'] == 'verified':
                    try:
                        self._memory.save(self.agent)
                    except (OSError, ValueError) as exc:
                        self._save_failed = True
                        return _response("error", "memory save failed: " + str(exc)[:512])
                return response
            except (PolynomialError, ValueError, RecursionError) as exc:
                return _response("unresolved", str(exc)[:512] or type(exc).__name__)

    def _call(self, name, arguments):
        if name == "list_rules":
            records = self.agent.library.lookup("algebra", kind="polynomial_identity", assumptions=())
            return _response("ok", "current verified rules; each application must still be checked",
                rules=[{"id": r.candidate.id, "statement": dict(r.candidate.statement),
                        "fingerprint": r.fingerprint, "verification": asdict(r.verification)} for r in records])
        variables = tuple(arguments["variables"])
        if name == "verify_identity":
            check = verify_identity(arguments["lhs"], arguments["rhs"], variables)
            return _response(_verdict(check), check.reason, verification=asdict(check))
        expression = arguments["expression"]
        if name == "expand_polynomial":
            run = run_compact(expression, variables)
            return _response(_verdict(run.verification), run.verification.reason,
                certificate=_certificate(run.certificate), verification=asdict(run.verification),
                work=asdict(run.stats))
        learn = name == "learn_polynomial"
        episode = self.agent.solve(PolynomialProblem(expression, variables), learn=learn,
            routes=("compact",) if learn else ("recall",), use_policy=False)
        solved = episode.status == "solved"
        output = episode.expression
        if output is not None and len(output) > MAX_SOURCE_LENGTH:
            output = None
        # Recall already checks each application. This final check binds the returned
        # expression to this request, independent of the recorded route/status.
        check = (episode.compact.verification if episode.compact is not None else
                 verify_identity(expression, output, variables) if output is not None else None)
        verified = solved and check is not None and check.status == "verified"
        return _response("verified" if verified else "unresolved",
            check.reason if check is not None else "no applicable verified rule within the recall budget",
            expression=output if verified else None, route=episode.route,
            verification=asdict(check) if check is not None else None,
            certificate=_certificate(episode.compact.certificate) if episode.compact is not None else None,
            learning={"requested": learn, "new_rule_ids": episode.learned_ids,
                      "checks": episode.learning_checks, "error": episode.learning_error},
            search_actions=episode.search_actions)


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def run_json_lines(source, destination, *, memory_dir=None) -> int:
    """One request/response per line, with optional persistent memory. Not JSON-RPC."""
    with ToolSession(memory_dir) as session:
        return _run_json_lines(source, destination, session)


def _run_json_lines(source, destination, session) -> int:
    errors = False
    while True:
        line = source.readline(MAX_REQUEST + 1)
        if not line:
            break
        oversized = len(line) > MAX_REQUEST
        if oversized:
            while line and not line.endswith("\n"):
                line = source.readline(MAX_REQUEST + 1)
            response = _response("error", "request exceeds 32768 characters")
        else:
            try:
                request = json.loads(line, object_pairs_hook=_unique,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON number")))
                if type(request) is not dict or set(request) != {"tool", "arguments"}:
                    raise ValueError("expected exactly tool and arguments")
                response = session.call(request["tool"], request["arguments"])
            except (ValueError, RecursionError):
                response = _response("error", "invalid JSON request or fields")
        errors = errors or response["status"] == "error"
        response['memory_scope'] = 'directory' if session._memory else 'process'
        destination.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
        destination.flush()
    return 2 if errors else 0
