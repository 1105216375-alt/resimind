"""Experimental typed Lean goals and kernel-rechecked theorem memory.

Only a bounded AST and fixed proof recipes are rendered. No raw Lean, arbitrary
imports, tactic programs or commands from a model are accepted. This is a small
core-Lean pilot, not a general Mathlib/miniF2F adapter or process sandbox.
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
          recipes=(Recipe("grind"), Recipe("omega"), Recipe("simp")), recall_first=True):
    """Bounded fixed portfolio pilot. Feedback is recorded, not a learned policy.

    All attempts replay from the original goal. This does not yet implement
    tactic-state branching or diagnostic-conditioned proposal generation.
    """
    goal.render()
    if type(max_checks) is not int or not 1 <= max_checks <= 32:
        raise ValueError("invalid check budget")
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
    candidates = (recalls + fixed) if recall_first else (fixed + recalls)
    attempts = []
    for index, recipe in candidates[:max_checks]:
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
