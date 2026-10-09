"""Coupled choices may require a temporarily worse draft. No API key needed."""
from resimind.search import SearchLimits, bounded_search


def verify(draft):
    return {"equal_allocations": draft["a"] == draft["b"],
            "required_total": draft["a"] + draft["b"] == 4,
            "within_capacity": 0 <= draft["a"] <= 2 and 0 <= draft["b"] <= 2}


def expand(draft, checks):
    for key in ("a", "b"):
        if draft[key] < 2:
            yield {**draft, key: draft[key] + 1}


if __name__ == "__main__":
    result = bounded_search({"a": 0, "b": 0}, expand=expand, verifier=verify,
                            limits=SearchLimits(max_expansions=16, max_generated=32))
    assert result.accepted and result.plan == {"a": 2, "b": 2}
    print(result.status, result.plan, result.checks)
