from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from resimind.integrations.formal import (
    FormalBackend, Goal, Lemma, Recipe, TheoremMemory, Term, applicable, nat, solve, term, var,
)


def conjunction():
    p, q = var("p"), var("q")
    return Goal((("p", "Prop"), ("q", "Prop")), term("imp", term("and", p, q), term("and", q, p)))


@pytest.fixture
def backend():
    backend = FormalBackend(timeout_seconds=10)
    if not backend.lean_path or not Path(backend.lean_path).is_file():
        pytest.skip("real Lean unavailable")
    return backend


@pytest.mark.parametrize("goal", [
    Goal((), var("x")),
    Goal((("x", "Nat"),), term("and", var("x"), var("x"))),
    Goal((), nat(True)),
    Goal((), nat(-1)),
    Goal((), Term("sorry", ())),
    Goal((("p) := by sorry", "Prop"),), var("p")),
    Goal((("p", "Prop"), ("p", "Prop")), var("p")),
    Goal((("p", "IO"),), var("p")),
    Goal((), term("eq", nat(10001), nat(10001))),
])
def test_typed_goal_rejects_invalid_syntax_before_compilation(goal, monkeypatch):
    monkeypatch.setattr(FormalBackend, "_check_generated", lambda *a: pytest.fail("must not compile"))
    with pytest.raises(ValueError):
        FormalBackend().check_goal(goal, Recipe("grind"))


@pytest.mark.parametrize("recipe", [Recipe("sorry"), Recipe("grind\naxiom bad : False"),
                                    Recipe("run_tac"), Recipe("induction_simp", "p"),
                                    Recipe("grind", "p")])
def test_recipe_injection_is_rejected(recipe):
    with pytest.raises(ValueError):
        FormalBackend().check_goal(conjunction(), recipe)


def test_term_depth_and_size_are_bounded():
    node = var("p")
    for _ in range(34):
        node = term("not", node)
    with pytest.raises(ValueError):
        Goal((("p", "Prop"),), node).render()


def test_structural_match_preserves_repeated_variable_bindings():
    lemma = Lemma(conjunction(), Recipe("grind"))
    p, q, r = var("p"), var("q"), var("r")
    a = term("or", p, r)
    good = Goal((("p", "Prop"), ("q", "Prop"), ("r", "Prop")),
                term("imp", term("and", a, q), term("and", q, a)))
    wrong = Goal(good.bindings, term("imp", term("and", a, q), term("and", p, a)))
    assert applicable(lemma, good)
    assert not applicable(lemma, wrong)


def test_real_logic_and_composed_recall_are_kernel_checked(backend, tmp_path):
    memory, proof = TheoremMemory().admit(conjunction(), Recipe("grind"), backend)
    assert proof.status == "verified" and proof.proof_digest
    memory.save(tmp_path / "memory.json")
    reloaded = TheoremMemory.load(tmp_path / "memory.json", backend)
    assert reloaded == memory
    p, q, r = var("p"), var("q"), var("r")
    a = term("or", p, r)
    goal = Goal((("p", "Prop"), ("q", "Prop"), ("r", "Prop")),
                term("imp", term("and", a, q), term("and", q, a)))
    result = solve(goal, backend, memory=reloaded)
    assert result.status == "verified"
    assert result.attempts[0].action == "recall:0"
    assert result.attempts[0].proof_digest
    with pytest.raises(FrozenInstanceError):
        memory.lemmas = ()


def test_real_false_goal_and_poisoned_memory_fail(backend, tmp_path):
    false = Goal((("n", "Nat"),), term("eq", var("n"), term("succ", var("n"))))
    proof = backend.check_goal(false, Recipe("omega"))
    assert proof.status != "verified" and proof.proof_digest is None
    with pytest.raises(ValueError):
        TheoremMemory().admit(false, Recipe("omega"), backend)
    forged = TheoremMemory((Lemma(false, Recipe("omega")),))
    assert solve(false, backend, memory=forged, recipes=()).status != "verified"
    forged.save(tmp_path / "forged.json")
    with pytest.raises(ValueError):
        TheoremMemory.load(tmp_path / "forged.json", backend)


def test_real_induction_and_natural_order(backend):
    n, m = var("n"), var("m")
    goal = Goal((("n", "Nat"), ("m", "Nat")),
                term("eq", term("mul", n, term("add", m, nat(1))),
                     term("add", term("mul", n, m), n)))
    assert backend.check_goal(goal, Recipe("induction_simp", "n")).status == "verified"
    order = Goal((("n", "Nat"), ("m", "Nat")), term("le", n, term("add", n, m)))
    assert backend.check_goal(order, Recipe("omega")).status == "verified"


def test_feedback_records_failed_attempt_before_retry(backend):
    result = solve(conjunction(), backend, recipes=(Recipe("decide"), Recipe("grind")))
    assert result.status == "verified"
    assert len(result.attempts) == 2
    assert result.attempts[0].status != "verified"
    assert result.attempts[1].status == "verified"
    limited = solve(conjunction(), backend, max_checks=1, recipes=(Recipe("decide"), Recipe("grind")))
    assert limited.status == "unresolved"


@pytest.mark.parametrize("text", ['{"format":1,"format":1,"lemmas":[]}',
                                  '{"format":true,"lemmas":[]}',
                                  '{"format":1,"lemmas":[],"verified":true}'])
def test_memory_format_does_not_trust_approval_flags(tmp_path, text):
    path = tmp_path / "invalid.json"
    path.write_text(text)
    with pytest.raises(ValueError):
        TheoremMemory.load(path, FormalBackend())
