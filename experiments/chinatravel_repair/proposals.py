"""Task-anchored, schema-only proposal prompts for the enhanced experiment.

This intentionally changes the proposer and is not an official ReAct baseline.
It reads only the public task, agent notebook, and local acceptance feedback.
The original worker, official agents, and historical experiment stay unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from types import MethodType

from experiments.chinatravel.upstream_worker import _decode_plan, _save


_RULES = """You are repairing or constructing the CURRENT travel task below.
Return one complete JSON object conforming to the schema, with no markdown.
Use only facts from the current task and its tool evidence. Never copy an
unrelated demonstration or change the task's origin, destination, party size,
or number of days. Unknown facts require a tool query; do not invent a nearby
restaurant, shorten a database entity name, or manufacture transport segments.
The price field is the UNIT price. The cost field is the TOTAL charge:
train/airplane/attraction/metro cost = price * tickets;
meal cost = price * people_number; accommodation cost = price * rooms;
taxi cost = price * cars; walk price = cost = 0.
Use the task party size for tickets, and enough cars/rooms for that party.
Preserve exact tool entity names, identifiers, fares, durations, and route legs.
Transport must connect the actual previous activity to the current activity;
arrival must precede the activity start. Recheck downstream time and cost
constraints after a repair. Keep unaffected activities and task facts intact.
Every nonfinal day must explicitly return by an evidenced route to the chosen
hotel and end with an accommodation activity whose end_time is 24:00. Begin
the next day's route from that same hotel. A morning check-in alone does not
represent an overnight stay. Do not bridge a missing overnight by routing from
yesterday's restaurant directly to this morning's breakfast. Query and schedule
the actual evening hotel return. If the return or next activity cannot fit the
available time window, leave the task unfinished rather than inventing a route,
overlapping activities, or deleting the overnight obligation.
Acceptance diagnostics are failed checks, not permission to ignore constraints.
Only the local checker may accept a plan; your answer is a candidate.
"""


def public_task_envelope(query: dict) -> dict:
    """Extract only the explicit bracketed benchmark header, never infer labels."""
    if (type(query) is not dict or set(query) != {"uid", "nature_language"}
            or type(query["uid"]) is not str
            or type(query["nature_language"]) is not str
            or not query["nature_language"].strip()):
        raise ValueError("expected_public_query_only")
    result = {"uid": query["uid"], "nature_language": query["nature_language"]}
    pattern = (r"^\s*\[当前位置\s*([^,，\]]+)[,，]\s*目标位置\s*([^,，\]]+)"
               r"[,，]\s*旅行人数\s*(\d+)[,，]\s*旅行天数\s*(\d+)\]")
    match = re.match(pattern, query["nature_language"])
    if match:
        start, target, people, days = match.groups()
        if int(people) > 0 and int(days) > 0:
            result["explicit_header"] = {
                "start_city": start.strip(), "target_city": target.strip(),
                "people_number": int(people), "days": int(days),
            }
    return result


def compact_feedback(feedback: dict | None) -> dict | None:
    """Keep the full previous candidate and bounded, explicitly abridged checks."""
    if feedback is None:
        return None
    if type(feedback) is not dict:
        raise TypeError("feedback must be an object or None")
    result = {key: deepcopy(feedback[key]) for key in
              ("decision", "previous_plan") if key in feedback}
    reasons = feedback.get("reasons", [])
    if type(reasons) is not list:
        reasons = [str(reasons)]
    result["reasons"] = reasons[:32]
    result["omitted_reason_count"] = max(0, len(reasons) - 32)
    diagnostics = feedback.get("diagnostics") or {}
    if type(diagnostics) is not dict:
        raise TypeError("diagnostics must be an object")
    compact = {}
    for name, value in diagnostics.items():
        if name == "environment" and type(value) is dict:
            compact[name] = {}
            for check, info in value.items():
                if type(info) is not dict or info.get("passed") is True:
                    continue
                record = {key: deepcopy(info[key]) for key in
                          ("passed", "exception_class") if key in info}
                record["failed_counts"] = {
                    key: number for key, number in info.get("counts", {}).items()
                    if number != 0
                }
                details = info.get("details", [])
                record["details"] = [str(detail)[:500] for detail in details[:6]]
                record["omitted_detail_count"] = max(0, len(details) - 6)
                record["details_abridged"] = any(len(str(d)) > 500 for d in details[:6])
                compact[name][check] = record
        else:
            compact[name] = deepcopy(value)
    result["diagnostics"] = compact
    return result


def build_plan_prompt(query: dict, schema: dict, feedback: dict | None = None,
                      *, notebook: str = "", action_query: str = "") -> str:
    """Schema and current-task evidence, with the original requirement last."""
    envelope = public_task_envelope(query)
    if type(schema) is not dict:
        raise TypeError("schema must be an object")
    encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
    parts = [_RULES, "CURRENT TASK\n" + encode(envelope),
             "OUTPUT SCHEMA (structure only; not a sample answer)\n" + encode(schema),
             "CURRENT TOOL NOTEBOOK\n" + notebook]
    if feedback is not None:
        parts.append("LOCAL ACCEPTANCE FEEDBACK\n" + encode(compact_feedback(feedback)))
    if action_query:
        parts.append("CURRENT AGENT REQUEST (must not replace the original task)\n" + action_query)
    parts.append("ORIGINAL REQUIREMENT — solve exactly this task:\n" + query["nature_language"])
    return "\n\n".join(parts)


class RepairingReActProposer:
    """Reuse one ReAct instance and its step budget with anchored final prompts.

    Only this supplied instance's ``plan`` method is replaced. Every response
    still goes through its existing model/broker, with no reset between repairs.
    Candidate files retain the raw answer even when it is invalid or drifts.
    Checking and deterministic repair belong to the caller; this wrapper never
    silently accepts, substitutes, or edits an emitted model plan.
    """

    def __init__(self, agent, query: dict, output_dir: Path, schema: dict):
        public_task_envelope(query)
        self.agent = agent
        self.query = deepcopy(query)
        self.schema = deepcopy(schema)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.attempt = 0
        self.feedback = None
        self.agent.plan_prompt = build_plan_prompt(self.query, self.schema)
        # Exploration receives the corrected arithmetic too; its native action
        # protocol and notebook are preserved.
        self.agent.prompt += "\n\nCURRENT-TASK RULES\n" + _RULES + "\n"

        def anchored_plan(instance, query):
            prompt = build_plan_prompt(
                self.query, self.schema, self.feedback,
                notebook=instance.notebook.read(), action_query=query,
            )
            return instance.backbone_llm(
                [{"role": "user", "content": prompt}], json_mode=True, one_line=False,
            )

        self.agent.plan = MethodType(anchored_plan, self.agent)

    def __call__(self, feedback: dict | None):
        self.attempt += 1
        self.feedback = deepcopy(feedback)
        if self.attempt == 1:
            if feedback is not None:
                raise ValueError("initial_feedback_must_be_none")
            result = self.agent(self.query["nature_language"])
            raw = result["ans"]
        else:
            if type(feedback) is not dict:
                raise ValueError("continuation_feedback_missing")
            _save(self.output_dir / f"feedback_{self.attempt:02d}.json", feedback)
            instructions = (
                "Continue the SAME task. Repair only the failed portions using verified tool facts. "
                "Keep correct prior work and original task metadata. Query missing facts if needed, "
                "then call plan(...) for a complete candidate.\n"
                + json.dumps(compact_feedback(feedback), ensure_ascii=False)
                + "\nORIGINAL REQUIREMENT:\n" + self.query["nature_language"]
            )
            self.agent.json_scratchpad.append({"role": "user", "content": instructions})
            self.agent._log.append({f"AcceptanceFeedback[{self.attempt}]": deepcopy(feedback)})
            self.agent.finished = False
            self.agent._ans = ""
            while self.agent.cur_step < self.agent.max_steps and not self.agent.finished:
                self.agent.step()
            raw = self.agent._ans if self.agent.finished else None
        plan = _decode_plan(raw)
        _save(self.output_dir / f"candidate_{self.attempt:02d}.json", {
            "attempt": self.attempt, "react_step": self.agent.cur_step,
            "finished": self.agent.finished, "raw_answer": raw, "plan": plan,
            "proposer": "schema_only_task_anchored_react",
        })
        _save(self.output_dir / "react_log.json", self.agent._log)
        return plan
