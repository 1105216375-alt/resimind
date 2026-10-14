"""Experimental typed Lean goals and kernel-rechecked theorem memory.

Only a bounded AST and fixed proof recipes are rendered. No raw Lean, arbitrary
imports, tactic programs or commands from a model are accepted. The planner can
also compose conditional lemmas through residual subgoals, but every complete
plan is still independently compiled by Lean. This is a small core-Lean pilot,
not a general Mathlib/miniF2F adapter or process sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time

from ..core import content_digest
from .lean import LeanPolynomialBackend, LeanProofResult


@dataclass(frozen=True, slots=True)
class Term:
    op: str
    args: tuple


def var(name):
    return Term("var", (name,))


def nat(value):
    return Term("nat", (value,))


def term(op, *args):
    return Term(op, args)


@dataclass(frozen=True, slots=True)
class Goal:
    bindings: tuple[tuple[str, str], ...]
    statement: Term

    def render(self):
        if (type(self.bindings) is not tuple or len(self.bindings) > 16
                or any(type(b) is not tuple or len(b) != 2 for b in self.bindings)):
            raise ValueError("invalid bindings")
        names = {}
        for i, (name, sort) in enumerate(self.bindings):
            if (type(name) is not str or not name.isascii() or not name.isidentifier()
                    or len(name) > 32 or name in names or sort not in ("Nat", "Prop")):
                raise ValueError("invalid binding")
            names[name] = (f"v{i}", sort)
        count = 0

        def render(node, depth=0):
            nonlocal count
            count += 1
            if (count > 256 or depth > 32 or type(node) is not Term
                    or type(node.op) is not str or type(node.args) is not tuple):
                raise ValueError("invalid or oversized term")
            if node.op == "var" and len(node.args) == 1:
                if type(node.args[0]) is not str or node.args[0] not in names:
                    raise ValueError("unbound variable")
                return names[node.args[0]]
            if node.op == "nat" and len(node.args) == 1:
                n = node.args[0]
                if type(n) is not int or not 0 <= n <= 10000:
                    raise ValueError("invalid natural literal")
                return f"({n} : Nat)", "Nat"
            unary = {"not": ("¬", "Prop", "Prop"), "succ": ("Nat.succ", "Nat", "Nat")}
            binary = {"and": ("∧", "Prop", "Prop"), "or": ("∨", "Prop", "Prop"),
                      "imp": ("→", "Prop", "Prop"), "iff": ("↔", "Prop", "Prop"),
                      "add": ("+", "Nat", "Nat"), "mul": ("*", "Nat", "Nat"),
                      "eq": ("=", "Nat", "Prop"), "le": ("≤", "Nat", "Prop"),
                      "lt": ("<", "Nat", "Prop")}
            if node.op in unary and len(node.args) == 1:
                symbol, expected, result = unary[node.op]
                text, actual = render(node.args[0], depth + 1)
                if actual != expected:
                    raise ValueError("sort mismatch")
                return f"({symbol} {text})", result
            if node.op in binary and len(node.args) == 2:
                symbol, expected, result = binary[node.op]
                lhs, ls = render(node.args[0], depth + 1)
                rhs, rs = render(node.args[1], depth + 1)
                if ls != expected or rs != expected:
                    raise ValueError("sort mismatch")
                return f"({lhs} {symbol} {rhs})", result
            raise ValueError("unsupported term")

        statement, sort = render(self.statement)
        if sort != "Prop":
            raise ValueError("theorem must be a proposition")
        binders = " ".join(f"({rendered} : {sort})" for rendered, sort in names.values())
        return binders, statement, tuple((name, value[0]) for name, value in names.items())


@dataclass(frozen=True, slots=True)
class Recipe:
    tactic: str
    induction_variable: str | None = None

    def render(self, goal):
        if self.tactic in ("grind", "omega", "simp", "simp_all", "decide"):
            if self.induction_variable is not None:
                raise ValueError("unexpected induction variable")
            return self.tactic
        if self.tactic == "induction_simp":
            goal.render()
            for i, (name, sort) in enumerate(goal.bindings):
                if name == self.induction_variable and sort == "Nat":
                    simp = "simp_all [Nat.succ_mul, Nat.add_assoc, Nat.add_comm, Nat.add_left_comm]"
                    return f"induction v{i} with\n  | zero => {simp}\n  | succ k ih => {simp}"
        raise ValueError("unsupported proof recipe")


@dataclass(frozen=True, slots=True)
class Lemma:
    goal: Goal
    recipe: Recipe


@dataclass(frozen=True, slots=True)
class ProofPlan:
    """A bounded, typed proof tree emitted by the residual planner.

    This is intentionally not a user-facing Lean program. ``kind`` is one of
    ``recipe``, ``recall``, ``intro`` or ``assumption`` and the renderer below
    rejects every other shape before invoking Lean.
    """

    kind: str
    target: Term
    index: int | None = None
    recipe: Recipe | None = None
    children: tuple["ProofPlan", ...] = ()
    assumption: int | None = None


def _render_memory_definitions(memory):
    definitions = []
    for i, lemma in enumerate(memory):
        binders, statement, _ = lemma.goal.render()
        body = lemma.recipe.render(lemma.goal)
        definitions.append("set_option linter.unusedVariables false in\n"
                           "set_option linter.unusedSimpArgs false in\n"
                           f"theorem memory_{i} {binders} : {statement} := by\n  {body}\n")
    return "".join(definitions)


def _split_imp(node):
    premises = []
    while type(node) is Term and node.op == "imp" and len(node.args) == 2:
        premises.append(node.args[0])
        node = node.args[1]
    return tuple(premises), node


def _term_sort(node, bindings):
    if type(node) is not Term:
        raise ValueError("invalid term")
    if node.op == "var" and len(node.args) == 1:
        return dict(bindings)[node.args[0]]
    if node.op == "nat" and len(node.args) == 1:
        return "Nat"
    if node.op == "not" and len(node.args) == 1:
        if _term_sort(node.args[0], bindings) != "Prop":
            raise ValueError("sort mismatch")
        return "Prop"
    if node.op == "succ" and len(node.args) == 1:
        if _term_sort(node.args[0], bindings) != "Nat":
            raise ValueError("sort mismatch")
        return "Nat"
    if node.op in {"and", "or", "imp", "iff"} and len(node.args) == 2:
        if any(_term_sort(child, bindings) != "Prop" for child in node.args):
            raise ValueError("sort mismatch")
        return "Prop"
    if node.op in {"add", "mul"} and len(node.args) == 2:
        if any(_term_sort(child, bindings) != "Nat" for child in node.args):
            raise ValueError("sort mismatch")
        return "Nat"
    if node.op in {"eq", "le", "lt"} and len(node.args) == 2:
        if any(_term_sort(child, bindings) != "Nat" for child in node.args):
            raise ValueError("sort mismatch")
        return "Prop"
    raise ValueError("unsupported term")


def _term_vars(node):
    if type(node) is not Term:
        return set()
    if node.op == "var" and len(node.args) == 1:
        return {node.args[0]}
    return {name for child in node.args if type(child) is Term for name in _term_vars(child)}


def _match_term(pattern, target, source_bindings, target_bindings, substitutions):
    if type(pattern) is not Term or type(target) is not Term:
        return False
    if pattern.op == "var" and len(pattern.args) == 1:
        name = pattern.args[0]
        if name not in dict(source_bindings):
            return False
        try:
            if dict(source_bindings)[name] != _term_sort(target, target_bindings):
                return False
        except (KeyError, ValueError):
            return False
        if name in substitutions:
            return substitutions[name] == target
        substitutions[name] = target
        return True
    if pattern.op != target.op or len(pattern.args) != len(target.args):
        return False
    return all(_match_term(a, b, source_bindings, target_bindings, substitutions)
               if type(a) is Term and type(b) is Term else a == b
               for a, b in zip(pattern.args, target.args))


def _substitute(node, substitutions):
    if node.op == "var" and len(node.args) == 1:
        return substitutions.get(node.args[0], node)
    return Term(node.op, tuple(_substitute(arg, substitutions) if type(arg) is Term else arg
                               for arg in node.args))


def _application(lemma, goal):
    """Return instantiated premises for applying ``lemma`` to ``goal``."""
    lemma.goal.render()
    goal.render()
    premises, conclusion = _split_imp(lemma.goal.statement)
    source_bindings = lemma.goal.bindings
    target_bindings = goal.bindings
    source_names = {name for name, _ in source_bindings}
    if not source_names <= _term_vars(conclusion):
        # Lean exposes every declared binder when ``apply`` is used. A binder
        # absent from the conclusion would become an unconstrained metavariable
        # (and often a stray ``⊢ Prop`` goal), so composition declines it.
        return None
    substitutions = {}
    if not _match_term(conclusion, goal.statement, source_bindings, target_bindings, substitutions):
        return None
    # A premise may not introduce an unconstrained metavariable. This keeps
    # application deterministic and prevents Lean from inventing witnesses.
    if any(name not in _term_vars(conclusion) for premise in premises
           for name in _term_vars(premise)):
        return None
    return tuple(_substitute(premise, substitutions) for premise in premises)


def _render_plan(plan, *, target_term, bindings, memory, context=()):
    if type(plan) is not ProofPlan or plan.target != target_term:
        raise ValueError("invalid proof plan target")
    if plan.kind == "assumption":
        if (plan.assumption is None or type(plan.assumption) is not int
                or not 0 <= plan.assumption < len(context)
                or context[plan.assumption] != target_term):
            raise ValueError("invalid assumption plan")
        return f"exact h{plan.assumption}"
    if plan.kind == "recipe":
        if plan.recipe is None or plan.children or plan.index is not None:
            raise ValueError("invalid recipe plan")
        return plan.recipe.render(Goal(bindings, target_term))
    if plan.kind == "intro":
        if (plan.children != tuple(plan.children) or len(plan.children) != 1
                or target_term.op != "imp" or len(target_term.args) != 2):
            raise ValueError("invalid intro plan")
        child = _render_plan(plan.children[0], target_term=target_term.args[1],
                             bindings=bindings, memory=memory,
                             context=context + (target_term.args[0],))
        return "intro h{}\n{}".format(len(context), child)
    if plan.kind == "recall":
        if (plan.index is None or type(plan.index) is not int
                or not 0 <= plan.index < len(memory)):
            raise ValueError("invalid recall plan")
        premises = _application(memory[plan.index], Goal(bindings, target_term))
        if premises is None or len(premises) != len(plan.children):
            raise ValueError("recall plan does not match target")
        if not premises:
            if plan.children:
                raise ValueError("unexpected recall children")
            return f"exact memory_{plan.index}"
        rendered = [f"apply memory_{plan.index}"]
        for premise, child in zip(premises, plan.children):
            text = _render_plan(child, target_term=premise, bindings=bindings,
                                memory=memory, context=context)
            lines = text.splitlines() or [""]
            rendered.append("· " + lines[0])
            rendered.extend("  " + line for line in lines[1:])
        return "\n".join(rendered)
    raise ValueError("unsupported proof plan")


class FormalBackend(LeanPolynomialBackend):
    """Share the existing version, resource, artifact and axiom acceptance gate."""

    def check_goal(self, goal, recipe=None, *, memory=(), recall=None):
        if type(goal) is not Goal or type(memory) is not tuple or len(memory) > 32:
            raise ValueError("invalid goal or memory")
        if recall is not None and (type(recall) is not int or not 0 <= recall < len(memory)):
            raise ValueError("invalid recall index")
        if (recall is None) == (recipe is None):
            raise ValueError("choose one recipe or recalled lemma")
        definitions = []
        for i, lemma in enumerate(memory):
            if type(lemma) is not Lemma or type(lemma.recipe) is not Recipe:
                raise ValueError("invalid lemma")
            binders, statement, _ = lemma.goal.render()
            body = lemma.recipe.render(lemma.goal)
            definitions.append("set_option linter.unusedVariables false in\n"
                               "set_option linter.unusedSimpArgs false in\n"
                               f"theorem memory_{i} {binders} : {statement} := by\n  {body}\n")
        binders, target, mapping = goal.render()
        if recall is not None:
            body = f"apply memory_{recall}"
        else:
            if type(recipe) is not Recipe:
                raise ValueError("invalid recipe")
            body = recipe.render(goal)
        source = ("import Lean\nset_option maxRecDepth 512\n"
                  f"set_option maxHeartbeats {self.max_heartbeats}\n"
                  + "".join(definitions)
                  + "set_option linter.unusedVariables false in\n"
                  "set_option linter.unusedSimpArgs false in\n"
                  f"theorem resimind_checked {binders} : {target} := by\n"
                  f"  trace_state\n  {body}\n#print axioms resimind_checked\n")
        binding = content_digest((goal, recipe, memory, recall))
        return self._check_generated(source, target, mapping, body, binding)

    def check_plan(self, goal, plan, *, memory=()):
        """Compile a planner-generated proof tree through the same Lean gate.

        ``plan`` is deliberately an internal, typed tree. It is never accepted
        as Lean text; ``_render_plan`` only emits the small set of tactics and
        memory references produced by :func:`solve`.
        """
        if type(goal) is not Goal or type(memory) is not tuple or len(memory) > 32:
            raise ValueError("invalid goal or memory")
        for lemma in memory:
            if type(lemma) is not Lemma:
                raise ValueError("invalid lemma")
            lemma.goal.render()
            lemma.recipe.render(lemma.goal)
        binders, target, mapping = goal.render()
        body = _render_plan(plan, target_term=goal.statement,
                            bindings=goal.bindings, memory=memory)
        definitions = _render_memory_definitions(memory)
        source = ("import Lean\nset_option maxRecDepth 512\n"
                  f"set_option maxHeartbeats {self.max_heartbeats}\n"
                  + definitions
                  + "set_option linter.unusedVariables false in\n"
                  "set_option linter.unusedSimpArgs false in\n"
                  f"theorem resimind_checked {binders} : {target} := by\n"
                  f"  trace_state\n  {body.replace(chr(10), chr(10) + '  ')}\n"
                  "#print axioms resimind_checked\n")
        binding = content_digest((goal, plan, memory))
        return self._check_generated(source, target, mapping, body, binding)


def applicable(lemma, goal):
    """Typed structural substitution proposes applicability; Lean decides it."""
    lemma.goal.render()
    goal.render()
    substitutions = {}
    source_sorts = dict(lemma.goal.bindings)
    target_sorts = dict(goal.bindings)

    def sort(node):
        if node.op == "var":
            return target_sorts[node.args[0]]
        return "Nat" if node.op in ("nat", "add", "mul", "succ") else "Prop"

    def match(pattern, target):
        if pattern.op == "var":
            name = pattern.args[0]
            if source_sorts[name] != sort(target):
                return False
            if name in substitutions:
                return substitutions[name] == target
            substitutions[name] = target
            return True
        return (pattern.op == target.op and len(pattern.args) == len(target.args)
                and all(match(a, b) if type(a) is Term and type(b) is Term else a == b
                        for a, b in zip(pattern.args, target.args)))

    return match(lemma.goal.statement, goal.statement)


def _search_plan(goal, memory, recipes, *, depth, context=(), allow_recipe=True,
                 seen=None, budget=64, is_root=True):
    """Bounded backward search over typed residual subgoals.

    The search proposes a plan only. The caller must replay that plan through
    :meth:`FormalBackend.check_plan`; structural matching is never acceptance.
    ``depth`` bounds lemma applications/introductions and ``budget`` bounds
    visited states, so cyclic or adversarial memories cannot cause open-ended
    work.
    """
    if depth < 0 or budget <= 0:
        return None
    seen = set() if seen is None else seen
    state = (content_digest((goal.statement, context)), depth, allow_recipe)
    if state in seen:
        return None
    seen.add(state)
    if any(item == goal.statement for item in context):
        return ProofPlan("assumption", goal.statement,
                         assumption=next(i for i, item in enumerate(context)
                                         if item == goal.statement))
    if depth > 0 and goal.statement.op == "imp" and len(goal.statement.args) == 2:
        premise, conclusion = goal.statement.args
        for child_allow_recipe in (False, True):
            child = _search_plan(Goal(goal.bindings, conclusion), memory, recipes,
                                 depth=depth - 1, context=context + (premise,),
                                 allow_recipe=child_allow_recipe, seen=seen,
                                 budget=budget - 1, is_root=False)
            if child is not None:
                return ProofPlan("intro", goal.statement, children=(child,))
    if depth > 0:
        # First search for a wholly symbolic chain. Only if that fails do we
        # allow a bounded recipe at a residual leaf. This avoids selecting the
        # first structurally matching lemma with an unverified dead-end when a
        # later lemma closes the subgoal through memory or an assumption.
        for child_allow_recipe in (False, True):
            for index, lemma in enumerate(memory):
                premises = _application(lemma, goal)
                if premises is None:
                    continue
                children = []
                for premise in premises:
                    child = _search_plan(Goal(goal.bindings, premise), memory, recipes,
                                         depth=depth - 1, context=context,
                                         allow_recipe=child_allow_recipe, seen=seen,
                                         budget=budget - 1, is_root=False)
                    if child is None:
                        break
                    children.append(child)
                else:
                    # Do not duplicate the ordinary zero-premise recall path.
                    if premises or not is_root:
                        return ProofPlan("recall", goal.statement, index=index,
                                         children=tuple(children))
    if allow_recipe:
        for recipe in recipes:
            recipe.render(goal)
            return ProofPlan("recipe", goal.statement, recipe=recipe)
    return None


@dataclass(frozen=True, slots=True)
class TheoremMemory:
    lemmas: tuple[Lemma, ...] = ()

    def __post_init__(self):
        if type(self.lemmas) is not tuple or len(self.lemmas) > 32:
            raise ValueError("memory limit exceeded")
        for lemma in self.lemmas:
            if type(lemma) is not Lemma or type(lemma.goal) is not Goal or type(lemma.recipe) is not Recipe:
                raise ValueError("invalid memory entry")
            lemma.goal.render()
            lemma.recipe.render(lemma.goal)

    def admit(self, goal, recipe, backend):
        result = backend.check_goal(goal, recipe)
        if result.status != "verified":
            raise ValueError("lemma admission requires a checked proof")
        lemma = Lemma(goal, recipe)
        if lemma in self.lemmas:
            return self, result
        return TheoremMemory(self.lemmas + (lemma,)), result

    def save(self, path):
        from dataclasses import asdict
        Path(path).write_text(json.dumps({"format": 1, "lemmas": [asdict(l) for l in self.lemmas]}))

    @classmethod
    def load(cls, path, backend):
        path = Path(path)
        if path.stat().st_size > 262144:
            raise ValueError("memory file too large")

        def unique(pairs):
            result = {}
            for k, v in pairs:
                if k in result:
                    raise ValueError("duplicate JSON key")
                result[k] = v
            return result

        def node(obj, depth=0):
            if (depth > 32 or type(obj) is not dict or set(obj) != {"op", "args"}
                    or type(obj["args"]) is not list or len(obj["args"]) > 2):
                raise ValueError("invalid serialized term")
            return Term(obj["op"], tuple(node(a, depth + 1) if type(a) is dict else a for a in obj["args"]))

        data = json.loads(path.read_text(), object_pairs_hook=unique)
        if (type(data) is not dict or set(data) != {"format", "lemmas"}
                or type(data["format"]) is not int or data["format"] != 1
                or type(data["lemmas"]) is not list or len(data["lemmas"]) > 32):
            raise ValueError("invalid memory format")
        memory = cls()
        for item in data["lemmas"]:
            if (type(item) is not dict or set(item) != {"goal", "recipe"}
                    or type(item["goal"]) is not dict or set(item["goal"]) != {"bindings", "statement"}
                    or type(item["recipe"]) is not dict or set(item["recipe"]) != {"tactic", "induction_variable"}
                    or type(item["goal"]["bindings"]) is not list
                    or any(type(b) is not list for b in item["goal"]["bindings"])):
                raise ValueError("invalid serialized lemma")
            goal = Goal(tuple(tuple(b) for b in item["goal"]["bindings"]), node(item["goal"]["statement"]))
            memory, _ = memory.admit(goal, Recipe(**item["recipe"]), backend)
        return memory


@dataclass(frozen=True, slots=True)
class Attempt:
    action: str
    status: str
    seconds: float
    goals: tuple[str, ...]
    source_digest: str
    proof_digest: str | None


@dataclass(frozen=True, slots=True)
class FormalResult:
    status: str
    attempts: tuple[Attempt, ...]
    seconds: float
    memory_digest: str
    model_calls: int = 0


def solve(goal, backend, *, memory=TheoremMemory(), max_checks=6,
          recipes=(Recipe("grind"), Recipe("omega"), Recipe("simp")), recall_first=True,
          composition_depth=3, composition_budget=64):
    """Solve with bounded residual composition plus a fixed proof portfolio.

    Conditional memory is used only to propose a typed proof tree. The complete
    tree is replayed by Lean before success is returned. ``composition_depth``
    and ``composition_budget`` are finite search limits, not claims of learned
    general theorem proving. Set depth to zero for the original portfolio-only
    baseline.
    """
    goal.render()
    if type(max_checks) is not int or not 1 <= max_checks <= 32:
        raise ValueError("invalid check budget")
    if (type(composition_depth) is not int or not 0 <= composition_depth <= 8
            or type(composition_budget) is not int or not 1 <= composition_budget <= 256):
        raise ValueError("invalid composition budget")
    if type(memory) is not TheoremMemory or type(recipes) is not tuple or len(recipes) > 16:
        raise ValueError("invalid solver options")
    for recipe in recipes:
        if type(recipe) is not Recipe:
            raise ValueError("invalid recipe")
        recipe.render(goal)
    start = time.perf_counter()
    digest = content_digest(memory)
    recalls = [(i, None) for i, lemma in enumerate(memory.lemmas) if applicable(lemma, goal)]
    fixed = [(None, recipe) for recipe in recipes]
    attempts = []
    checks_left = max_checks

    # Composition is intentionally a separate candidate from exact recall and
    # direct recipes, making the ablation explicit in attempt logs.
    if composition_depth and memory.lemmas and checks_left and not (recall_first and recalls):
        plan = _search_plan(goal, memory.lemmas, recipes, depth=composition_depth,
                            allow_recipe=False, budget=composition_budget)
        if plan is not None:
            began = time.perf_counter()
            result = backend.check_plan(goal, plan, memory=memory.lemmas)
            attempts.append(Attempt("compose", result.status,
                                    time.perf_counter() - began, result.goals,
                                    result.source_digest, result.proof_digest))
            checks_left -= 1
            if result.status == "verified":
                return FormalResult("verified", tuple(attempts), time.perf_counter() - start, digest)
            if result.status == "unavailable":
                return FormalResult("unresolved", tuple(attempts), time.perf_counter() - start, digest)

    candidates = (recalls + fixed) if recall_first else (fixed + recalls)
    for index, recipe in candidates[:checks_left]:
        began = time.perf_counter()
        result = backend.check_goal(goal, recipe, memory=memory.lemmas if index is not None else (), recall=index)
        attempts.append(Attempt(f"recall:{index}" if index is not None else recipe.tactic,
                                result.status, time.perf_counter() - began, result.goals,
                                result.source_digest, result.proof_digest))
        if result.status == "verified":
            return FormalResult("verified", tuple(attempts), time.perf_counter() - start, digest)
        if result.status == "unavailable":
            break
    return FormalResult("unresolved", tuple(attempts), time.perf_counter() - start, digest)
