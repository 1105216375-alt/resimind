"""Small, non-executing checks of the agent's own ChinaTravel constraints.

This is deliberately not a DSL validator or a general satisfiability solver.
Only complete, recognized AST shapes justify a finding. An unrecognized
program is unknown, never evidence that its contract is satisfiable. The
translation is neither rewritten nor evaluated here; gold labels are not used.
"""
from __future__ import annotations

import ast


_MAX_CONSTRAINTS = 128
_MAX_SOURCE_BYTES = 65_536
_MAX_AST_NODES = 4_096
_RESERVED = {
    "plan", "result", "set", "allactivities", "activity_type",
    "activity_transports", "innercity_transport_type", "room_count", "room_type",
}


def _name(node, value):
    return isinstance(node, ast.Name) and node.id == value


def _call(node, function, *arguments):
    return (isinstance(node, ast.Call) and _name(node.func, function)
            and not node.keywords and len(node.args) == len(arguments)
            and all(_name(arg, name) for arg, name in zip(node.args, arguments)))


def _assignment(node, target):
    return (isinstance(node, ast.Assign) and len(node.targets) == 1
            and _name(node.targets[0], target))


def _boolean_assignment(node, target, value):
    return (_assignment(node, target) and isinstance(node.value, ast.Constant)
            and node.value.value is value)


def _activity_loop(node):
    if (not isinstance(node, ast.For) or not isinstance(node.target, ast.Name)
            or node.target.id in _RESERVED or node.orelse
            or not _call(node.iter, "allactivities", "plan")):
        return None
    return node.target.id


def _transport_collection(tree):
    """Match the entire initialize -> collect -> compare data flow.

    Restricting the complete program prevents false alarms from reassignment,
    filtering, aliases, mutation, early loop exits, or a later result override.
    Collector and loop variable spellings carry no special meaning.
    """
    if len(tree.body) != 3:
        return None
    initialize, loop, final = tree.body
    if (not isinstance(initialize, ast.Assign) or len(initialize.targets) != 1
            or not isinstance(initialize.targets[0], ast.Name)
            or not _call(initialize.value, "set")):
        return None
    collector = initialize.targets[0].id
    activity = _activity_loop(loop)
    if (collector in _RESERVED or activity is None or collector == activity
            or len(loop.body) != 1 or not isinstance(loop.body[0], ast.Expr)):
        return None
    addition = loop.body[0].value
    if (not isinstance(addition, ast.Call) or addition.keywords
            or len(addition.args) != 1
            or not isinstance(addition.func, ast.Attribute)
            or not _name(addition.func.value, collector)
            or addition.func.attr != "add"):
        return None
    mode = addition.args[0]
    if (not isinstance(mode, ast.Call) or mode.keywords or len(mode.args) != 1
            or not _name(mode.func, "innercity_transport_type")
            or not _call(mode.args[0], "activity_transports", activity)):
        return None
    if not _assignment(final, "result"):
        return None
    comparison = final.value
    if (not isinstance(comparison, ast.Compare)
            or not _name(comparison.left, collector)
            or len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.LtE)
            or len(comparison.comparators) != 1
            or not isinstance(comparison.comparators[0], ast.Set)):
        return None
    allowed = comparison.comparators[0].elts
    if not all(isinstance(value, ast.Constant) and type(value.value) is str
               for value in allowed):
        return None
    return {"collector_variable": collector, "activity_variable": activity,
            "allowed_modes": sorted({value.value for value in allowed})}


def _comparison_value(node, function, activity, operator):
    if (not isinstance(node, ast.Compare) or len(node.ops) != 1
            or not isinstance(node.ops[0], operator) or len(node.comparators) != 1):
        return None
    left, right = node.left, node.comparators[0]
    if _call(left, function, activity) and isinstance(right, ast.Constant):
        return right.value
    if _call(right, function, activity) and isinstance(left, ast.Constant):
        return left.value
    return None


def _room_requirements(tree):
    """Extract universal positive integer room decisions, not bed capacity.

    A room's ``room_type`` is the sandbox's bed count. This function does not
    infer occupants per bed, multiply beds by rooms, or compare that product
    with the party size. Such an inference would reject valid shared-bed use.
    """
    if len(tree.body) != 2 or not _boolean_assignment(tree.body[0], "result", True):
        return None
    loop = tree.body[1]
    activity = _activity_loop(loop)
    if activity is None or not loop.body:
        return None
    requirements = []
    for statement in loop.body:
        if (not isinstance(statement, ast.If) or statement.orelse
                or len(statement.body) != 1
                or not _boolean_assignment(statement.body[0], "result", False)
                or not isinstance(statement.test, ast.BoolOp)
                or not isinstance(statement.test.op, ast.And)
                or len(statement.test.values) != 2):
            return None
        terms = statement.test.values
        guards = [index for index, term in enumerate(terms)
                  if _comparison_value(term, "activity_type", activity, ast.Eq)
                  == "accommodation"]
        if len(guards) != 1:
            return None
        requirement = terms[1 - guards[0]]
        matched = None
        for function, field in (("room_count", "rooms"), ("room_type", "room_type")):
            value = _comparison_value(requirement, function, activity, ast.NotEq)
            if type(value) is int and value > 0:
                matched = {"field": field, "value": value,
                           "scope": "all_accommodation_activities"}
                break
        if matched is None:
            return None
        requirements.append(matched)
    return requirements


def preflight_translation(translation, *, first_activity_has_empty_transports=True):
    """Report known contract contradictions without changing the translation.

    The search domain starts with intercity travel and no incoming local route.
    Its official ``innercity_transport_type([])`` is ``'empty'``. The explicit
    keyword makes that domain assumption visible to other callers; a caller
    without this invariant must pass False. Findings request retranslation from
    the original task, never an automatic addition of ``'empty'`` to the DSL.

    ``no_known_contradiction`` is not an acceptance or satisfiability result.
    Unknown constraint indices still require the normal DSL and plan checks.
    Only the current ``hard_logic_py`` is read, not historical reflection text
    or the informal ``hard_logic`` strings.
    """
    if type(first_activity_has_empty_transports) is not bool:
        raise TypeError("first_activity_has_empty_transports must be a bool")
    report = {
        "status": "no_known_contradiction", "needs_retranslation": False,
        "issues": [], "room_requirements": [], "recognized_constraint_indices": [],
        "unknown_constraint_indices": [], "diagnostics": [],
        "scope": "bounded_static_checks_of_self_translation_only",
        "first_activity_has_empty_transports": first_activity_has_empty_transports,
    }
    codes = translation.get("hard_logic_py") if type(translation) is dict else None
    if type(codes) is not list or not codes or len(codes) > _MAX_CONSTRAINTS:
        report["diagnostics"].append({"reason": "invalid_or_excessive_constraint_list"})
        report["status"] = "unassessed"
        return report
    for index, code in enumerate(codes):
        reason = None
        tree = None
        if type(code) is not str or not code.strip():
            reason = "constraint_not_nonempty_source"
        elif len(code.encode("utf-8", errors="surrogatepass")) > _MAX_SOURCE_BYTES:
            reason = "source_limit"
        else:
            try:
                tree = ast.parse(code, mode="exec")
                if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
                    reason = "ast_limit"
            except (SyntaxError, ValueError, RecursionError, MemoryError):
                reason = "unparseable_source"
        if reason is not None:
            report["unknown_constraint_indices"].append(index)
            report["diagnostics"].append({"constraint_index": index, "reason": reason})
            continue
        collection = _transport_collection(tree)
        rooms = _room_requirements(tree)
        if collection is None and rooms is None:
            report["unknown_constraint_indices"].append(index)
            continue
        report["recognized_constraint_indices"].append(index)
        if collection is not None and "empty" not in collection["allowed_modes"]:
            if first_activity_has_empty_transports:
                report["issues"].append({
                    "code": "unconditional_transport_set_excludes_empty",
                    "constraint_index": index, "action": "needs_retranslation",
                    "message": (
                        "This constraint collects transport types from every activity, "
                        "including the initial intercity activity's empty incoming route. "
                        "The official function returns 'empty', which this allowed set "
                        "excludes. Retranslate the original request; do not invent a route "
                        "or silently relax the constraint."
                    ),
                    "source": code,
                    "evidence": {**collection, "required_initial_value": "empty"},
                })
        if rooms is not None:
            report["room_requirements"].extend(
                {"constraint_index": index, **item} for item in rooms)
    if report["issues"]:
        report["status"] = "needs_retranslation"
        report["needs_retranslation"] = True
    return report
