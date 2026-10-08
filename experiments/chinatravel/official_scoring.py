"""Offline adapter to the pinned, unchanged official ChinaTravel evaluators.

Install the upstream requirements separately and set CHINATRAVEL_UPSTREAM or
pass upstream_root. Its Chinese ``environment/database`` must contain the
official sandbox. This module never loads a model or calls a model API.

``score(gold, plan)`` returns schema/env/logical/final booleans and diagnostics.
Only the evaluator receives ``gold`` (including ``hard_logic_py``); never give
gold or these post-run diagnostics to a solving agent. Use ``score_batch`` for
official corpus-level micro metrics: averaging per-plan micro rates is not the
same operation. Percentages follow the upstream convention, ranging 0..100.
"""

from __future__ import annotations

import contextlib
import copy
from functools import lru_cache
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


UPSTREAM_COMMIT = "f445e0011f42594fcc29b8c752ece06ded9a8218"


def _root(upstream_root: str | Path | None) -> Path:
    value = upstream_root or os.environ.get("CHINATRAVEL_UPSTREAM")
    if not value:
        raise ValueError("Pass upstream_root or set CHINATRAVEL_UPSTREAM.")
    return Path(value).expanduser().resolve()


@lru_cache(maxsize=1)
def _official(root: Path) -> tuple[Any, Any, Any, dict[str, Any]]:
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    if revision != UPSTREAM_COMMIT:
        raise ValueError(f"Expected official revision {UPSTREAM_COMMIT}, got {revision}")
    if not (root / "chinatravel/environment/database").is_dir():
        raise FileNotFoundError("The complete official Chinese sandbox is not installed.")
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    modules = [
        importlib.import_module("chinatravel.evaluation." + name)
        for name in ("schema_constraint", "commonsense_constraint", "hard_constraint")
    ]
    for module in modules:
        if not Path(module.__file__).resolve().is_relative_to(root):
            raise RuntimeError("A different ChinaTravel checkout is already imported.")
    schema = json.loads(
        (root / "chinatravel/evaluation/output_schema.json").read_text(encoding="utf-8")
    )
    return (
        modules[0].evaluate_schema_constraints,
        modules[1].evaluate_commonsense_constraints,
        modules[2].evaluate_hard_constraints_v2,
        schema,
    )


def _validate_gold(query_gold: Mapping[str, Any]) -> str:
    uid = query_gold.get("uid")
    if not isinstance(uid, str) or not uid:
        raise ValueError("Gold query requires a nonempty uid.")
    constraints = query_gold.get("hard_logic_py")
    if (
        not isinstance(constraints, list)
        or not constraints
        or any(not isinstance(item, str) or not item for item in constraints)
    ):
        raise ValueError("Gold hard_logic_py must be a nonempty list of DSL strings.")
    return uid


def score_batch(
    query_gold: Mapping[str, Mapping[str, Any]],
    plans: Mapping[str, Any],
    *,
    upstream_root: str | Path | None = None,
) -> dict[str, Any]:
    """Call the same three official functions as eval_exp.py, retaining gold.

    Missing outputs are represented by {}, as in the official result loader.
    Evaluator setup/errors propagate; an infrastructure failure is never silently
    reported as a model failure. The upstream's own caught validation errors keep
    their original scoring behavior and appear in the diagnostics.
    """
    ids = list(query_gold)
    if not ids:
        raise ValueError("Cannot score an empty query cohort.")
    for uid, query in query_gold.items():
        if _validate_gold(query) != uid:
            raise ValueError("Gold mapping key does not match its query uid.")
    gold = copy.deepcopy(dict(query_gold))
    outputs = {uid: copy.deepcopy(plans.get(uid, {})) for uid in ids}
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        schema_eval, env_eval, logic_eval, schema = _official(_root(upstream_root))
        schema_rate, schema_table, schema_ids = schema_eval(ids, outputs, schema=schema)
        env_macro, env_micro, env_table, env_ids = env_eval(
            ids, gold, outputs, verbose=False, lang="zh"
        )
        (
            logic_macro, logic_micro, conditional_macro, conditional_micro,
            logic_table, logic_ids,
        ) = logic_eval(ids, gold, outputs, env_pass_id=env_ids, verbose=False, lang="zh")
    schema_pass, env_pass, logic_pass = map(set, (schema_ids, env_ids, logic_ids))
    final_pass = schema_pass & env_pass & logic_pass
    # pandas' JSON conversion handles NumPy scalars and missing table cells.
    tables = {
        key: {row["data_id"]: row for row in json.loads(table.to_json(orient="records"))}
        for key, table in (
            ("schema", schema_table), ("env", env_table), ("logical", logic_table)
        )
    }
    per_query = {}
    for uid in ids:
        per_query[uid] = {
            "uid": uid,
            "schema": uid in schema_pass,
            "env": uid in env_pass,
            "logical": uid in logic_pass,
            "final": uid in final_pass,
            "diagnostics": {key: rows[uid] for key, rows in tables.items()},
        }
    return {
        "upstream_commit": UPSTREAM_COMMIT,
        "language": "zh",
        "query_count": len(ids),
        "metrics": {
            "schema_pass_rate": schema_rate,
            "commonsense_macro": env_macro,
            "commonsense_micro": env_micro,
            "logical_macro": logic_macro,
            "logical_micro": logic_micro,
            "conditional_logical_macro": conditional_macro,
            "conditional_logical_micro": conditional_micro,
            "final_pass_rate": 100.0 * len(final_pass) / len(ids),
        },
        "per_query": per_query,
        "captured_stdout": stdout.getvalue(),
        "captured_stderr": stderr.getvalue(),
    }


def score(
    query_gold: Mapping[str, Any],
    plan: Any,
    *,
    upstream_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return schema/env/logical/final booleans for one completed output."""
    uid = _validate_gold(query_gold)
    batch = score_batch({uid: query_gold}, {uid: plan}, upstream_root=upstream_root)
    return {
        **batch["per_query"][uid],
        "metrics": batch["metrics"],
        "upstream_commit": batch["upstream_commit"],
        "captured_stdout": batch["captured_stdout"],
        "captured_stderr": batch["captured_stderr"],
    }
