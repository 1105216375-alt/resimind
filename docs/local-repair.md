# Local repair without losing checked work

For coupled choices that require temporarily worse drafts, see the separate
[bounded search API](bounded-search.md). It still requires complete validation
before releasing a solution.

`resimind.repair` adds a provider-independent, zero-dependency repair transaction
for JSON drafts. An application supplies tool evidence and a whole-plan checker;
a model or a deterministic adapter proposes a small set of replacements. This
is an optional utility for proposers, not a replacement for the Agent's final
domain verifier or an automatic modification to the six existing domains.

```python
from resimind.repair import Edit, RepairProposal, apply_repair, plan_sha256

draft = {"quantity": 2, "unit_price": 20, "total": 20}

def verify(plan):
    return {
        "quantity": plan["quantity"] == 2,
        "price": plan["unit_price"] == 20,
        "total": plan["total"] == plan["quantity"] * 20,
        "budget": plan["total"] <= 50,
    }

patch = RepairProposal(plan_sha256(draft), (
    Edit("/total", before=20, value=40, evidence_refs=("catalog:item",)),
))
result = apply_repair(draft, patch, evidence_ids={"catalog:item"}, verifier=verify)
assert result.status == "accepted"
assert result.plan["total"] == 40
assert draft["total"] == 20  # Caller-owned input is unchanged.
```

This is a synthetic arithmetic example. In an application, the verifier must
derive its reference values from trusted task/tool data. A reference ID or a
matching hash proves neither that the data is true nor that an edit is correct.
Run `PYTHONPATH=src python -m examples.local_repair` for the complete audit.

| Status | Meaning |
| --- | --- |
| `accepted` | A repair made strict progress and all supplied checks now pass. |
| `repaired` | Strict progress was committed to the draft; some checks remain unresolved. **Do not deliver as complete.** |
| `rejected` | Stale/invalid edits, a regression, or no progress; the original draft is returned. |
| `deferred` | Verification failed, mutated its input, changed check names, or hid a known failure behind an unknown result; no edit commits. |

Each edit uses an RFC 6901 JSON pointer, an exact expected previous value, and
application-registered evidence IDs. The proposal also binds to the complete
base-plan hash. Replacements are atomic; overlapping paths, array append,
root replacement, non-JSON values, and unregistered references are rejected.
Replacing an existing object can add its fields; adapters must constrain what
changes are semantically allowed and the checker must cover those constraints.

The checker runs on detached copies of the complete original and proposed
plans. It returns the same nonempty mapping of check names to `True`, `False`,
or `None`. A transaction commits only when more checks become `True`, no prior
`True` becomes false/unknown, and no prior `False` becomes unknown. This is
monotonic progress under the **supplied checks**, not proof of arbitrary
natural-language correctness. Applications must keep check meanings and tool
evidence stable during a transaction, and enforce callback timeouts externally.

The ChinaTravel development adapter uses this API to bind exact entity data,
recalculate total prices, and rebuild existing-mode routes between adjacent
activities. It keeps destination choices and activity order, abstains on unknown
or ambiguous entities, and rechecks time, cost, environment and self-translated
constraints. Invalid translations require regeneration rather than repeated
plan rewrites. Current implementation stops on such translations; automatic
retranslation and constraint-guided search are not implemented.

After a live run exposed an overnight gap, the adapter also requires each
nonfinal day's last activity to be accommodation ending at `24:00` before it
can bind the following day's origin. A route from yesterday's dinner to
today's breakfast cannot replace evidence of an overnight hotel return.

[Development results (中文)](repair-development.zh-CN.md) ·
[Experiment and reproduction](../experiments/chinatravel_repair/README.md) ·
[Core implementation](../src/resimind/repair.py)
