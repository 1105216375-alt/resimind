"""Oracle-free checks for repair; self-translated constraints are not gold.

Checks are deliberately stable within a plan's activity topology. All official
world checks are rerun after each proposed transaction, including those that
previously passed. Exceptions remain unknown and cannot authorize delivery.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from experiments.chinatravel.upstream_worker import ENVIRONMENT_CHECKS


def coverage_checks(plan, people):
    """Check ticket/vehicle allocation and positive hotel quantities.

    Malformed or unavailable structure is unknown, never a vacuous pass. These
    checks also run before schema repair; missing fields must not make a later
    valid replacement introduce a different verification contract. The sandbox
    models four passengers per taxi. Hotel ``numbed`` is a bed count, not a
    guest-capacity field. Positive rooms/beds are checked here; actual hotel
    occupancy capacity is outside this dataset's verification scope.
    """
    def enough(value, minimum):
        return type(value) is int and value >= minimum

    checks = {}
    days = plan.get("itinerary") if type(plan) is dict else None
    if type(days) is not list:
        return checks
    valid_people = type(people) is int and people > 0
    for d, day in enumerate(days):
        activities = day.get("activities") if type(day) is dict else None
        if type(activities) is not list:
            continue
        for a, activity in enumerate(activities):
            prefix = f"coverage/{d}/{a}"
            checks[prefix + "/activity"] = None
            checks[prefix + "/route"] = None
            if type(activity) is not dict or not valid_people:
                continue
            kind = activity.get("type")
            covered = None
            if kind in ("train", "airplane", "attraction"):
                covered = enough(activity.get("tickets"), people)
            elif kind == "accommodation":
                rooms, beds = activity.get("rooms"), activity.get("room_type")
                covered = enough(rooms, 1) and enough(beds, 1)
            elif kind in ("breakfast", "lunch", "dinner"):
                covered = True
            checks[prefix + "/activity"] = covered
            legs = activity.get("transports")
            if type(legs) is not list:
                continue
            coverage = []
            for leg in legs:
                mode = leg.get("mode") if type(leg) is dict else None
                coverage.append(
                    enough(leg.get("tickets"), people) if mode == "metro" else
                    enough(leg.get("cars"), (people + 3) // 4) if mode == "taxi" else
                    True if mode == "walk" else None)
            checks[prefix + "/route"] = (
                False if any(value is False for value in coverage) else
                None if any(value is None for value in coverage) else True)
    return checks


class LocalContract:
    def __init__(self, translation: dict, upstream: Path, *, tools=None, public_query=None):
        from jsonschema.validators import validator_for
        from chinatravel.symbol_verification import commonsense_constraint as common
        from chinatravel.symbol_verification.concept_func import func_dict
        from chinatravel.symbol_verification.dsl import validate_dsl_code
        from chinatravel.environment.language import city_names

        if type(translation) is not dict:
            raise TypeError("translation must be an oracle-free self-translation object")
        self.translation = deepcopy(translation)
        translation = self.translation
        self.tools = tools
        self.common = common
        self.last_diagnostics = {}
        schema = json.loads((upstream / "chinatravel/evaluation/output_schema.json").read_text())
        validator = validator_for(schema)
        validator.check_schema(schema)
        self.validator = validator(schema)
        # Table headers from this pinned official implementation. The transport
        # checker adds a second, differently spelled cost column on failure.
        import ast
        import inspect
        self.columns = {}
        for name in ENVIRONMENT_CHECKS:
            tree = ast.parse(inspect.getsource(getattr(common, name)))
            headers = next(ast.literal_eval(keyword.value)
                           for node in ast.walk(tree) if isinstance(node, ast.Call)
                           for keyword in node.keywords if keyword.arg == "columns")
            if name == "Is_transport_correct":
                headers.append("Incorrect cost information of Inner-City Transport")
            self.columns[name] = headers
        self.translation_errors = []
        self.structure_valid = (
            all(translation.get(key) in city_names("zh") for key in ("start_city", "target_city"))
            and type(translation.get("days")) is int and 1 <= translation["days"] <= 6
            and type(translation.get("people_number")) is int and translation["people_number"] > 0)
        if not self.structure_valid:
            self.translation_errors.append("translation_structure_invalid")
        if public_query is not None:
            from .proposals import public_task_envelope
            header = public_task_envelope(public_query).get("explicit_header", {})
            if any(translation.get(key) != value for key, value in header.items()):
                self.translation_errors.append("translation_disagrees_with_public_header")
        codes = translation.get("hard_logic_py")
        self.codes = codes if type(codes) is list else []
        if not self.codes:
            self.translation_errors.append("translation_constraints_empty")
        for code in self.codes:
            try:
                if type(code) is not str or not code.strip():
                    raise ValueError("empty DSL")
                validate_dsl_code(code, allowed_names=set(func_dict) | {"plan"})
            except Exception:
                self.translation_errors.append("translation_dsl_invalid")
        if translation.get("ood") or translation.get("error"):
            self.translation_errors.append("translation_reported_error")
        reflect = translation.get("reflect_info", [])
        if type(reflect) is not list or any(type(item) is not dict for item in reflect):
            self.translation_errors.append("translation_reflection_invalid")
        elif reflect and (reflect[-1].get("run_error_list") or reflect[-1].get("value_error_list")):
            self.translation_errors.append("translation_unresolved")

    def __call__(self, plan: dict) -> dict[str, bool | None]:
        from chinatravel.symbol_verification.concept_func import func_dict
        from chinatravel.symbol_verification.dsl import execute_dsl_code
        from .binding import binding_checks

        checks = {"translation_valid": True if not self.translation_errors else None}
        diagnostics = {"translation_errors": self.translation_errors, "environment": {},
                       "scope_limits": ["hotel_guest_capacity_not_available_in_dataset"]}
        errors = list(self.validator.iter_errors(plan))
        checks["schema"] = not errors
        diagnostics["schema"] = [
            {"path": list(e.absolute_path), "validator": e.validator,
             "message": e.message[:500], "message_abridged": len(e.message) > 500}
            for e in errors[:64]
        ]
        diagnostics["schema_omitted_count"] = max(0, len(errors) - 64)
        plan_object = plan if type(plan) is dict else {}
        for key in ("start_city", "target_city", "people_number"):
            checks["task/" + key] = (plan_object.get(key) == self.translation.get(key)
                                     if self.structure_valid else None)
        checks["task/days"] = (len(plan_object["itinerary"]) == self.translation["days"]
                               if self.structure_valid and type(plan_object.get("itinerary")) is list else None)
        self.common._set_tool_lang("zh")
        for name in ENVIRONMENT_CHECKS:
            keys = [name + "/" + col for col in self.columns[name]]
            checks.update({key: None for key in keys})
            checks[name] = None
            if errors or not self.structure_valid:
                continue
            try:
                local_plan = deepcopy(plan)
                table, info = getattr(self.common, name)(deepcopy(self.translation), local_plan, verbose=False)
                if local_plan != plan:
                    raise ValueError("checker_mutated_plan")
                row = table.iloc[0].to_dict()
                counts = {str(key): int(value) for key, value in row.items()}
                if set(counts) - set(self.columns[name]):
                    raise ValueError("unknown_environment_check")
                checks[name] = all(value == 0 for value in counts.values())
                for col in self.columns[name]:
                    checks[name + "/" + col] = counts.get(col, 0) == 0
                diagnostics["environment"][name] = {"passed": checks[name], "counts": counts, "details": info}
            except Exception as exc:
                diagnostics["environment"][name] = {"exception_class": type(exc).__name__}
        for index, code in enumerate(self.codes):
            key = f"self_constraint/{index}"
            checks[key] = None
            if errors or self.translation_errors:
                continue
            try:
                local_plan = deepcopy(plan)
                variables = dict(func_dict, plan=local_plan)
                execute_dsl_code(code, variables, allowed_builtins={"set": set})
                result = variables.get("result")
                if local_plan == plan and type(result) is bool:
                    checks[key] = result
            except Exception:
                pass
        if self.structure_valid:
            # Binding and coverage inspect available fields conservatively.
            # Skipping them on schema failure changes the named check set when
            # exact tool data repairs missing fields in the same activity slots.
            checks.update(coverage_checks(plan, self.translation["people_number"]))
            checks.update({"binding/" + key: value for key, value in
                           binding_checks(plan, self.translation, tools=self.tools).items()})
        self.last_diagnostics = diagnostics
        return checks
