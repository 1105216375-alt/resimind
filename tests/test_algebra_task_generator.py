"""Fixed test seeds only; do not select, print, or save the eventual study seed."""
import ast
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import random

import pytest

from benchmarks import algebra_task_generator as generator
from benchmarks import knowledge_growth, run_algebra_live_eval
from resimind.domains import algebra


# These deliberately public constants are unit-test fixtures, not study seeds.
TEST_SEEDS = (104729, 130363, 155921)


def test_default_is_eight_balanced_strata_with_only_case_fields():
    cases = generator.generate_cases(TEST_SEEDS[0])
    assert isinstance(cases, tuple)
    assert len(cases) == 32
    assert Counter(case.group for case in cases) == {group.id: 4 for group in generator.GROUPS}
    assert len({case.id for case in cases}) == 32
    for case in cases:
        assert set(asdict(case)) == {"id", "expression", "variables", "group"}
        assert case.id.startswith(f"transfer-{case.group}-")
        assert {node.id for node in ast.walk(ast.parse(case.expression))
                if isinstance(node, ast.Name)} == set(case.variables)


def test_reproducible_seed_and_independent_global_random_state():
    before = random.getstate()
    first = generator.generate_cases(TEST_SEEDS[0])
    assert first == generator.generate_cases(TEST_SEEDS[0])
    assert first != generator.generate_cases(TEST_SEEDS[1])
    assert random.getstate() == before


def test_default_sampling_is_backward_compatible_with_v3_test_fixture():
    cases = generator.generate_cases(TEST_SEEDS[0])
    encoded = json.dumps([asdict(case) for case in cases], sort_keys=True,
                         separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "1dbdb7edd8c51ba382bc8f1c204e27e880ebeb3ae528b04bfb7f25579cf79038"
    assert cases == generator.generate_cases(TEST_SEEDS[0], exclude_expressions=())
    assert cases == generator.generate_cases(TEST_SEEDS[0], exclude_expressions=[])
    metadata = json.dumps(generator.generator_metadata(), sort_keys=True,
                          separators=(",", ":")).encode()
    assert hashlib.sha256(metadata).hexdigest() == "8c33ee694d66e3e792eea96e9b8d25cfeb28ad7da758b9b36d3799e2c75d6294"


def test_explicit_prior_cases_are_excluded_without_changing_strata():
    # Fixed public fixtures exercise previous-study exclusion without reading
    # a real private manifest or choosing any new formal-study seed.
    previous = generator.generate_cases(TEST_SEEDS[0])
    exclusions = [f"  ({case.expression})  " for case in previous]
    current = generator.generate_cases(TEST_SEEDS[0], exclude_expressions=exclusions)
    assert len(current) == 32
    assert Counter(case.group for case in current) == Counter(case.group for case in previous)
    previous_keys = {generator._ast_key(case.expression) for case in previous}
    current_keys = {generator._ast_key(case.expression) for case in current}
    assert len(current_keys) == 32 and not current_keys.intersection(previous_keys)
    assert not current_keys.intersection(generator._excluded_ast_keys())
    assert current == generator.generate_cases(TEST_SEEDS[0], exclude_expressions=tuple(exclusions))


def test_exclusion_snapshot_preserves_raw_list_and_canonical_ast_digest():
    expressions = [" (x+y)**2 ", "((x+y)**2)", "(x-y)**2"]
    original = list(expressions)
    metadata = generator.exclusion_metadata(expressions)
    encoded = json.dumps(original, ensure_ascii=False, sort_keys=True, allow_nan=False,
                         separators=(",", ":")).encode()
    assert metadata["expressions"] == original
    assert metadata["expression_count"] == 3
    assert metadata["unique_ast_count"] == 2
    assert metadata["expressions_sha256"] == hashlib.sha256(encoded).hexdigest()
    assert json.loads(json.dumps(metadata)) == metadata
    canonical = generator.exclusion_metadata(["(x-y)**2", "(x+y)**2"])
    assert metadata["ast_set_sha256"] == canonical["ast_set_sha256"]
    assert metadata["expressions_sha256"] != canonical["expressions_sha256"]
    expressions.append("x")
    assert metadata["expressions"] == original
    metadata["expressions"].append("y")
    assert expressions == original + ["x"]


def test_exclusion_validation_precedes_sampling(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid explicit exclusions must fail before sampling")

    monkeypatch.setattr(generator, "_draw_expression", forbidden)
    with pytest.raises(ValueError, match="exclude_expressions"):
        generator.generate_cases(TEST_SEEDS[0], exclude_expressions=["x/0"])


@pytest.mark.parametrize("expressions", [None, "x+y", {"x+y"}, {"expression": "x+y"}])
def test_exclusions_require_an_explicit_list_or_tuple(expressions):
    with pytest.raises(TypeError, match="tuple or list"):
        generator.generate_cases(TEST_SEEDS[0], exclude_expressions=expressions)
    with pytest.raises(TypeError, match="tuple or list"):
        generator.exclusion_metadata(expressions)


@pytest.mark.parametrize("expression", [None, True, 123])
def test_exclusion_entries_must_be_strings(expression):
    with pytest.raises(TypeError, match=r"exclude_expressions\[0\]"):
        generator.generate_cases(TEST_SEEDS[0], exclude_expressions=[expression])


@pytest.mark.parametrize("expression", ["", "  ", "x+", "f(x)", "x/y", "x/0", "x**-1",
                                         "x**17", "x+0.5", "[x,y]", "x.__class__", "lambda: x",
                                         "x" * (algebra.MAX_SOURCE_LENGTH + 1)])
def test_exclusion_entries_use_bounded_polynomial_syntax(expression):
    with pytest.raises(ValueError, match="bounded polynomial syntax"):
        generator.exclusion_metadata([expression])


def test_per_group_keeps_the_existing_prefix_of_every_stratum():
    short = generator.generate_cases(TEST_SEEDS[0], 2)
    long = generator.generate_cases(TEST_SEEDS[0], 5)
    for group in generator.GROUPS:
        assert [c for c in short if c.group == group.id] == [c for c in long if c.group == group.id][:2]


@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_ast_unique_and_disjoint_from_all_published_corpora(seed):
    keys = [generator._ast_key(case.expression) for case in generator.generate_cases(seed)]
    published = {
        generator._ast_key(case.expression)
        for module in (knowledge_growth, run_algebra_live_eval)
        for name in ("DEVELOPMENT", "DISCOVERY", "TRANSFER")
        for case in getattr(module, name, ())
    }
    assert len(keys) == len(set(keys))
    assert not published.intersection(keys)


def test_published_and_within_batch_duplicates_are_resampled(monkeypatch):
    draw = generator._draw_expression
    calls = 0
    first = None

    def duplicate_then_draw(sampler, group, index):
        nonlocal calls, first
        calls += 1
        if calls == 1:
            case = run_algebra_live_eval.TRANSFER[0]
            # Redundant parentheses must still collide with the original AST.
            return f"({case.expression})", case.variables
        if calls == 3:
            return first
        value = draw(sampler, group, index)
        if calls == 2:
            first = value
        return value

    monkeypatch.setattr(generator, "_draw_expression", duplicate_then_draw)
    cases = generator.generate_cases(TEST_SEEDS[0])
    assert len(cases) == 32
    assert calls >= 34
    assert len({generator._ast_key(case.expression) for case in cases}) == 32


def test_generation_never_expands_scores_or_evaluates_answers(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Task sampling must not expand or evaluate an answer")

    monkeypatch.setattr(algebra._Ring, "read", forbidden)
    monkeypatch.setattr(algebra, "validate_expression", forbidden)
    monkeypatch.setattr(algebra, "verify_identity", forbidden)
    monkeypatch.setattr(knowledge_growth, "score_expansion", forbidden)
    monkeypatch.setattr(knowledge_growth, "_value", forbidden)
    assert len(generator.generate_cases(TEST_SEEDS[0])) == 32
    exclusions = ["(x+y)**2", "(-3*x+2*y)**4"]
    assert len(generator.generate_cases(TEST_SEEDS[0], exclude_expressions=exclusions)) == 32
    assert generator.exclusion_metadata(exclusions)["expression_count"] == 2


@pytest.mark.parametrize("seed", TEST_SEEDS)
def test_syntax_and_compact_answer_capacity_fit_production_and_oracle(seed):
    # Static checks only: these do not compute coefficients or expected answers.
    for case in generator.generate_cases(seed):
        tree, degrees = knowledge_growth._parse(case.expression, case.variables)
        assert algebra._tree(case.expression)
        assert algebra._variables(case.variables) == case.variables
        _, degree, terms, work = generator._shape_bound(tree, len(case.variables))
        assert degree <= 6 and terms <= 28 and work < algebra.MAX_PRODUCTS
        assert max(degrees) <= knowledge_growth.MAX_SCORER_DEGREE
        generator._validate_bounds(case.expression, case.variables)


def test_variants_are_balanced_and_rational_square_coefficients_are_nonintegral():
    cases = generator.generate_cases(TEST_SEEDS[0])
    cubes = [case for case in cases if case.group == "affine-trinomial-cube"]
    assert [len(case.variables) for case in cubes] == [2, 3, 2, 3]
    powers = [ast.parse(case.expression, mode="eval").body.right.value
              for case in cases if case.group == "three-variable-high-power"]
    assert powers == [5, 6, 5, 6]
    conjugates = [case for case in cases if case.group == "conjugate-product"]
    assert [len(case.variables) for case in conjugates] == [2, 3, 2, 3]
    for case in cases:
        if case.group != "rational-binomial-square":
            continue
        divisions = [node for node in ast.walk(ast.parse(case.expression))
                     if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)]
        assert len(divisions) == 2
        for division in divisions:
            numerator = knowledge_growth._integer(division.left)
            denominator = knowledge_growth._integer(division.right)
            assert (numerator, denominator) in generator.RATIONAL_COEFFICIENTS
            assert numerator and numerator % denominator


def test_public_metadata_is_stable_seed_free_and_detached():
    metadata = generator.generator_metadata()
    assert metadata["default_task_count"] == 32
    assert [group["id"] for group in metadata["groups"]] == [group.id for group in generator.GROUPS]
    assert "seed" not in metadata and "cases" not in metadata
    assert json.loads(json.dumps(metadata)) == metadata
    metadata["groups"][0]["description"] = "mutated"
    assert generator.generator_metadata()["groups"][0]["description"] != "mutated"


def test_sampler_has_a_stable_published_algorithm():
    # Lock a sampler stream without publishing any generated test expressions.
    sampler = generator._Sampler(TEST_SEEDS[0], "unit-test-only")
    expected = []
    values = tuple(range(256))  # Divides 2**256 exactly, so no modulo rejection.
    for counter in range(8):
        payload = json.dumps([generator.GENERATOR_VERSION, str(TEST_SEEDS[0]),
                              "unit-test-only", counter], separators=(",", ":")).encode()
        expected.append(hashlib.sha256(payload).digest()[-1])
    assert [sampler.choice(values) for _ in range(8)] == expected


@pytest.mark.parametrize("seed", [None, True, "123", 1.5])
def test_seed_must_be_explicit_integer(seed):
    with pytest.raises(TypeError, match="explicit integer"):
        generator.generate_cases(seed)


@pytest.mark.parametrize("per_group", [0, -1, True, 1.5, "4", generator.MAX_PER_GROUP + 1])
def test_per_group_limits_are_explicit(per_group):
    with pytest.raises(ValueError, match="per_group"):
        generator.generate_cases(TEST_SEEDS[0], per_group)


def test_rejection_loop_has_a_bound(monkeypatch):
    old = knowledge_growth.DISCOVERY[0]
    monkeypatch.setattr(generator, "MAX_DRAW_ATTEMPTS", 3)
    monkeypatch.setattr(generator, "_draw_expression", lambda *args: (old.expression, old.variables))
    with pytest.raises(RuntimeError, match="duplicate rejection budget"):
        generator.generate_cases(TEST_SEEDS[0])
