"""Offline contract tests; integration requires separately downloaded data."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path

import pytest

from experiments.chinatravel.official_scoring import score, score_batch


def test_public_query_cannot_silently_replace_gold():
    with pytest.raises(ValueError, match="hard_logic_py"):
        score({"uid": "public-query"}, {})


def test_serialized_constraints_must_be_decoded_before_scoring():
    with pytest.raises(ValueError, match="hard_logic_py"):
        score({"uid": "raw-csv", "hard_logic_py": "['result=True']"}, {})


@pytest.fixture
def official_fixture():
    root = os.environ.get("CHINATRAVEL_UPSTREAM")
    data = os.environ.get("CHINATRAVEL_DATA_DIR")
    if not root or not data:
        pytest.skip("Set CHINATRAVEL_UPSTREAM and CHINATRAVEL_DATA_DIR for offline integration.")
    data = Path(data)
    manifest = json.loads((data / "dataset_manifest.json").read_text())
    uid = manifest["dev_uids"][0]
    gold = json.loads((data / "gold" / (uid + ".json")).read_text())
    return root, gold


def test_empty_plan_fails_official_checks_without_mutation(official_fixture):
    root, gold = official_fixture
    original = copy.deepcopy(gold)
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = score(gold, {}, upstream_root=root)
    assert result["schema"] is False
    assert result["env"] is False
    assert result["logical"] is False
    assert result["final"] is False
    assert gold == original
    assert not stdout.getvalue() and not stderr.getvalue()
    assert result["diagnostics"]["logical"]["logic_py_0"] == 0


def test_schema_success_does_not_imply_final_success(official_fixture):
    root, gold = official_fixture
    plan = {
        "people_number": gold["people_number"],
        "start_city": gold["start_city"],
        "target_city": gold["target_city"],
        "itinerary": [],
    }
    result = score(gold, plan, upstream_root=root)
    assert result["schema"] is True
    assert result["logical"] is False
    assert result["final"] is False


def test_missing_output_stays_in_denominator(official_fixture):
    root, gold = official_fixture
    batch = score_batch({gold["uid"]: gold}, {}, upstream_root=root)
    assert batch["query_count"] == 1
    assert batch["metrics"]["final_pass_rate"] == 0
    assert batch["per_query"][gold["uid"]]["final"] is False
