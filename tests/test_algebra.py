"""Exact proof admission, sound cancellation, and bounded untrusted syntax."""

import pytest

from resimind.domains.algebra import (
    AlgebraVerifier,
    MAX_SOURCE_LENGTH,
    PolynomialError,
    nonzero_assumption,
    validate_expression,
    verify_identity,
)
from resimind.knowledge import KnowledgeCandidate, KnowledgeLibrary


def identity(lhs="(u+v)**2", rhs="u**2+2*u*v+v**2", *, variables=("u", "v"),
             derivation=None, **overrides):
    fields = dict(
        id="rule:square", domain="algebra", kind="polynomial_identity",
        statement={"lhs": lhs, "rhs": rhs, "variables": list(variables)},
        assumptions=(),
        derivation=({"lhs": lhs, "rhs": rhs},) if derivation is None else derivation,
        source_task_id="derive:square", evidence_refs=("proof:square",),
    )
    fields.update(overrides)
    return KnowledgeCandidate(**fields)


def cancellation(factor="a", lhs="x", rhs="0", variables=("a", "x"),
                 assumptions=(), derivation=None):
    return KnowledgeCandidate(
        id="rule:cancel", domain="algebra", kind="equation_cancellation",
        statement={"factor": factor, "lhs": lhs, "rhs": rhs, "variables": list(variables)},
        assumptions=assumptions,
        derivation=({"operation": "cancel_nonzero_factor", "factor": factor,
                     "lhs": lhs, "rhs": rhs},) if derivation is None else derivation,
        source_task_id="derive:cancellation", evidence_refs=("proof:cancellation",),
    )


@pytest.mark.parametrize(("lhs", "rhs", "variables"), [
    ("(u+v)**2", "u**2+2*u*v+v**2", ("u", "v")),
    ("(u+v)**3", "u**3+3*u**2*v+3*u*v**2+v**3", ("u", "v")),
    ("(u-v)*(u+v)", "u**2-v**2", ("u", "v")),
    ("(x+1)/3 + (2*x-1)/6", "(4*x+1)/6", ("x",)),
    ("x/-2", "-x/2", ("x",)),
    ("+x + -x", "0", ("x",)),
    ("7/3", "14/6", ()),
    ("x**0", "1", ("x",)),
    ("long_variable*(long_variable+1)", "long_variable**2+long_variable", ("long_variable",)),
])
def test_exact_identities_are_universal_not_sampled(lhs, rhs, variables):
    result = verify_identity(lhs, rhs, variables)
    assert result.status == "verified"
    assert result.verifier_id == "exact-rational-polynomial-v1"


def test_complete_multistep_derivation_is_checked():
    candidate = identity(derivation=(
        {"lhs": "(u+v)**2", "rhs": "(u+v)*(u+v)"},
        {"lhs": " (u + v) * (u + v) ", "rhs": "u*u + u*v + v*u + v*v"},
        {"lhs": "u*u + u*v + v*u + v*v", "rhs": "u**2+2*u*v+v**2"},
    ))
    assert AlgebraVerifier().verify(candidate).status == "verified"


@pytest.mark.parametrize(("lhs", "rhs"), [
    ("x", "999"),
    ("(x+1)**2", "x**2+1"),
    ("x*(x-1)*(x-2)*(x-3)", "0"),
    ("x/3", "x/2"),
])
def test_false_claims_and_fixed_sample_overfit_are_rejected(lhs, rhs):
    # The third claim fits all x in {0,1,2,3}; coefficients expose the overfit.
    assert verify_identity(lhs, rhs, ("x",)).status == "rejected"
    assert AlgebraVerifier().verify(identity(lhs, rhs, variables=("x",))).status == "rejected"


def test_true_endpoints_do_not_excuse_a_false_middle_step():
    candidate = identity(derivation=(
        {"lhs": "(u+v)**2", "rhs": "999"},
        {"lhs": "999", "rhs": "u**2+2*u*v+v**2"},
    ))
    result = AlgebraVerifier().verify(candidate)
    assert result.status == "rejected"
    assert "step 0" in result.reason


@pytest.mark.parametrize("derivation", [
    (),
    ({"lhs": "u+v", "rhs": "v+u"},),
    ({"lhs": "(u+v)**2", "rhs": "(u+v)*(u+v)"},),
    ({"lhs": "(u+v)**2", "rhs": "(u+v)*(u+v)"},
     {"lhs": "u*u+2*u*v+v*v", "rhs": "u**2+2*u*v+v**2"}),
    ({"samples": [{"u": 1, "v": 2, "passed": True}]},),
])
def test_missing_unrelated_discontinuous_or_sample_only_certificate_is_rejected(derivation):
    assert AlgebraVerifier().verify(identity(derivation=derivation)).status == "rejected"


def test_arbitrary_inference_or_equation_text_is_never_verified():
    assert AlgebraVerifier().verify(identity(kind="inference")).status == "unknown"
    assert verify_identity("x=999", "True", ("x",)).status == "unknown"


def test_cancellation_requires_nonzero_factor_assumption():
    # a*x=0 is true for every x when a=0, so it does not imply x=0.
    result = AlgebraVerifier().verify(cancellation())
    assert result.status == "rejected"
    assert "a != 0" in result.reason
    assert AlgebraVerifier().verify(cancellation(assumptions=("a != 0",))).status == "verified"


def test_cancellation_cannot_drop_zero_root_of_arbitrary_variable():
    candidate = cancellation(factor="t", lhs="t-1", variables=("t",))
    assert AlgebraVerifier().verify(candidate).status == "rejected"
    guarded = cancellation(factor="t", lhs="t-1", variables=("t",), assumptions=("t != 0",))
    assert AlgebraVerifier().verify(guarded).status == "verified"


def test_cancellation_assumption_stays_bound_to_exact_factor():
    candidate = cancellation(factor="a+b", variables=("a", "b", "x"), assumptions=("a != 0",))
    assert AlgebraVerifier().verify(candidate).status == "rejected"
    assert nonzero_assumption(" a+b ") == "a + b != 0"
    good = cancellation(factor="a+b", variables=("a", "b", "x"), assumptions=("a + b != 0",))
    assert AlgebraVerifier().verify(good).status == "verified"


@pytest.mark.parametrize("derivation", [
    (),
    ({"operation": "sample_check", "factor": "a", "lhs": "x", "rhs": "0"},),
    ({"operation": "cancel_nonzero_factor", "factor": "a", "lhs": "x", "rhs": "999"},),
])
def test_cancellation_needs_matching_explicit_certificate(derivation):
    candidate = cancellation(assumptions=("a != 0",), derivation=derivation)
    assert AlgebraVerifier().verify(candidate).status == "rejected"


def test_zero_polynomial_cannot_be_declared_nonzero():
    candidate = cancellation(factor="a-a", assumptions=("a - a != 0",))
    assert AlgebraVerifier().verify(candidate).status == "rejected"


@pytest.mark.parametrize("assumptions", [
    ("a!=0",), ("a is probably nonzero",), ("b != 0",),
    ("a = 1 in all samples",), ("a != 0 or a == 0",),
])
def test_textual_claims_do_not_discharge_the_nonzero_condition(assumptions):
    assert AlgebraVerifier().verify(cancellation(assumptions=assumptions)).status == "rejected"


def test_other_number_system_cannot_receive_a_rational_cancellation_proof():
    candidate = cancellation(factor="2", lhs="x", rhs="0", variables=("x",),
                             assumptions=("2 != 0",))
    candidate.statement["number_system"] = "integers_mod_6"
    # In Z/6Z, 2*3=2*0 but 3!=0: a nonzero factor alone does not suffice.
    assert AlgebraVerifier().verify(candidate).status == "unknown"


def test_unknown_statement_semantics_are_not_silently_ignored():
    candidate = identity()
    candidate.statement["variables_are_matrices"] = True
    assert AlgebraVerifier().verify(candidate).status == "unknown"


def test_cancellation_library_conditions_and_provenance_survive_round_trip(tmp_path):
    library = KnowledgeLibrary({"algebra": AlgebraVerifier()})
    record = library.admit(cancellation(assumptions=("a != 0",)))
    assert record.status == "verified"
    assert library.lookup("algebra") == ()
    assert library.lookup("algebra", assumptions=("a is probably nonzero",)) == ()
    assert len(library.lookup("algebra", assumptions=("a != 0",))) == 1
    path = tmp_path / "algebra.json"
    library.save(path)
    restored = KnowledgeLibrary.load(path, {"algebra": AlgebraVerifier()})
    assert restored.lookup("algebra") == ()
    loaded = restored.lookup("algebra", assumptions=("a != 0",))[0]
    assert loaded.candidate.source_task_id == "derive:cancellation"
    assert loaded.candidate.assumptions == ("a != 0",)
    assert loaded.verification.verifier_id == "exact-rational-polynomial-v1"


def test_exact_proof_without_source_task_stays_out_of_library():
    library = KnowledgeLibrary({"algebra": AlgebraVerifier()})
    candidate = identity(source_task_id="")
    assert AlgebraVerifier().verify(candidate).status == "verified"
    assert library.admit(candidate).status == "unknown"
    assert library.lookup("algebra") == ()


@pytest.mark.parametrize("expression", [
    "1.0", "True", "1j", "None", "'x'", "[x]", "{'x': 1}",
    "x/y", "x/(1+1)", "x/0", "x**-1", "x**y", "x**17",
    "sin(x)", "x.real", "x[0]", "__import__('os').getcwd()",
    "x if x else 1", "x // 2", "x % 2", "x << 1", "x == x",
    "(lambda: 1)()", "2x", "z-z", "z**0", "0*(1/x)",
])
def test_unsupported_or_undefined_inputs_never_verify(expression):
    assert verify_identity(expression, expression, ("x", "y")).status == "unknown"


@pytest.mark.parametrize("variables", [
    "x", None, ["x", "x"], ["_x"], ["x-y"], ["for"], [True],
    ["x" * 65], [f"v{i}" for i in range(17)],
])
def test_invalid_variable_declarations_fail_closed(variables):
    assert verify_identity("1", "1", variables).status == "unknown"


@pytest.mark.parametrize("expression", [
    "x" * (MAX_SOURCE_LENGTH + 1),
    "+" * 100 + "x",
    "2**16**16",
    "(x**16)**3",
    "9" * 100,
    "(a+b+c+d+e+f+g+h+i+j)**16",
])
def test_resource_exhaustion_returns_unknown(expression):
    variables = tuple("abcdefghijx")
    assert verify_identity(expression, expression, variables).status == "unknown"


def test_validation_helper_bounds_input_without_executing_it(tmp_path):
    validate_expression("(x+1)**3", ("x",))
    marker = tmp_path / "must-not-exist"
    payload = f"__import__('pathlib').Path({str(marker)!r}).touch()"
    with pytest.raises(PolynomialError):
        validate_expression(payload, ())
    assert not marker.exists()


def test_fresh_expression_substitution_is_rechecked():
    # A learned u,v identity instantiated with new compound expressions.
    result = verify_identity("((2*x-3)+(x/2+4))**2",
                             "(2*x-3)**2+2*(2*x-3)*(x/2+4)+(x/2+4)**2", ("x",))
    assert result.status == "verified"
    assert verify_identity("((2*x-3)+(x/2+4))**2",
                           "(2*x-3)**2+(x/2+4)**2", ("x",)).status == "rejected"
