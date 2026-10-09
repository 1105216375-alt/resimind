# Search when local edits cannot finish a plan

`resimind.search` provides a zero-dependency, provider-independent search over
JSON drafts. The application supplies an expander and a complete verifier.
Temporary regressions are allowed while exploring; **only a nonempty, unchanged
set of checks whose values are all exactly `True` can release a plan**.

```python
from resimind.search import SearchLimits, bounded_search

def verify(draft):
    return {"equal": draft["a"] == draft["b"],
            "total": draft["a"] + draft["b"] == 4,
            "capacity": 0 <= draft["a"] <= 2 and 0 <= draft["b"] <= 2}

def expand(draft, checks):
    for key in ("a", "b"):
        if draft[key] < 2:
            yield {**draft, key: draft[key] + 1}

result = bounded_search({"a": 0, "b": 0}, expand=expand, verifier=verify,
                        limits=SearchLimits(max_expansions=16, max_generated=32))
assert result.accepted
assert result.plan == {"a": 2, "b": 2}
```

Run `PYTHONPATH=src python -m examples.bounded_search`. The first move breaks
equality; a later move restores it while satisfying the total. The earlier
[local repair API](local-repair.md) deliberately rejects regressions when
committing edits. Search keeps such alternatives as drafts until the full
contract passes. Neither API replaces the Agent runtime or adds LangGraph as a
dependency; applications opt into these utilities.

| Result | Meaning |
| --- | --- |
| `accepted` | `plan` and `checks` hold a fully checked result. |
| `exhausted` | No proposed alternative remains; **not** a proof of infeasibility. |
| `limited` | A configured limit or deadline prevented further search. |
| `deferred` | A callback failed, changed its input, or returned an invalid/changing check set. |

Unfinished results have `plan=None`. `best_draft` and `best_checks` are diagnostic
material, never completed output. Priority favors more passed checks, fewer
unknowns, fewer failures, lower depth, then generation order; it does not prove
optimality. Each unique candidate is verified before queue pruning, including
the last candidate permitted by the generation budget.

Limits cover expansions, yielded candidates (including duplicates), depth,
frontier size, JSON bytes and trace size. An optional absolute
`time.monotonic()` deadline is checked around callbacks and iterator advances;
it cannot interrupt a stuck callback. Use a worker process for hard CPU/wall
limits and untrusted-code isolation. Callback mutation checks are not a sandbox.

The verifier must enforce evidence provenance, immutable task requirements and
every relevant rule on **each complete draft**. If activity topology changes,
stable aggregate checks must recompute every underlying obligation and reject
empty groups. An expander's high score, a plausible explanation or a supplied
evidence ID cannot authorize acceptance by itself.

The [ChinaTravel development adapter](../experiments/chinatravel_search/README.md)
adds exact tool binding, schedule construction and bounded restaurant choices.
Its [observed results](search-development.zh-CN.md) include a conservative
default and a separately reported run with explicit manual semantic annotations.
