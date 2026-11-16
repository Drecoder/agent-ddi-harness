"""Deterministic checks behind EEP rules that carry a `check` name.

Each check reads only repository facts and the config, and returns a Result. A blocking rule's
failed Result stops the loop before the gauntlet; a non-blocking one is recorded as evidence.
Checks named in POST_GAUNTLET run after the gauntlet, on what the run itself produced.
"""

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .eep import applies
from .gitio import changed_by_both, git

PLACEHOLDER = re.compile(r"^\s*(wip|fix|fixes|update|updates|changes|tmp|test|\.)\s*$", re.I)
MARKER = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.M)
POST_GAUNTLET = {"evidence_recorded", "capa_on_block", "draft_is_claim"}


@dataclass
class Result:
    rule: str
    ok: bool
    message: str
    details: dict = field(default_factory=dict)
    fix: str = ""


@dataclass
class Ctx:
    root: Path
    cfg: dict
    facts: object
    scope: object


def intent_present(rule, c: Ctx):
    msgs = [m for m in c.facts.messages if m]
    if not msgs and not c.facts.changes:
        return Result(rule, True, "no new commits in the range: nothing to state")
    bad = [m.splitlines()[0] for m in msgs if PLACEHOLDER.match(m.splitlines()[0])]
    if not msgs:
        return Result(rule, False, "the push carries no commit message: no intent to check premises against",
                      fix="amend with a message that states the intent and the premise it relies on")
    if bad:
        return Result(rule, False, f"placeholder intent: {', '.join(repr(b) for b in bad)}",
                      {"placeholders": bad},
                      fix="reword those commits (git rebase -i) to say what changes and why")
    return Result(rule, True, f"{len(msgs)} commit message(s) state the intent")


def _module_of(path: str, roots: list):
    for r in roots:
        r = r.rstrip("/") + "/"
        if path.startswith(r) and path.endswith(".py"):
            mod = path[len(r):-3].replace("/", ".")
            return mod[:-9] if mod.endswith(".__init__") else mod
    return None


def blast_radius(rule, c: Ctx):
    roots = c.cfg["project"]["source_roots"] + c.cfg["project"]["test_roots"]
    changed = {m for m in (_module_of(p, c.cfg["project"]["source_roots"]) for p in c.scope.source_files) if m}
    importers = {}
    for r in roots:
        for f in sorted((c.root / r).rglob("*.py")) if (c.root / r).exists() else []:
            rel = f.relative_to(c.root).as_posix()
            try:
                tree = ast.parse(f.read_text(), rel)
            except SyntaxError:
                continue
            for n in ast.walk(tree):
                names = []
                if isinstance(n, ast.Import):
                    names = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.module:
                    names = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
                for nm in names:
                    for m in changed:
                        if nm == m or nm.startswith(m + "."):
                            importers.setdefault(m, set()).add(rel)
    radius = {m: sorted(v) for m, v in sorted(importers.items())}
    conflict_sides = {}
    for mg in c.facts.merges:
        conflict_sides[mg["sha"][:12]] = changed_by_both(c.root, mg)
    n = sum(len(v) for v in radius.values())
    return Result(rule, True, f"{len(changed)} changed module(s), {n} importing file(s)",
                  {"importers": radius, "changed_by_both_sides": conflict_sides})


def test_with_source(rule, c: Ctx):
    if not c.cfg["solution_check"]["require_test_with_source"] or not c.scope.source_files:
        return Result(rule, True, "no source change, or not required")
    if c.scope.test_files:
        return Result(rule, True, f"{len(c.scope.test_files)} test file(s) change with "
                                  f"{len(c.scope.source_files)} source file(s)")
    return Result(rule, False, f"{len(c.scope.source_files)} source file(s) changed with no test change",
                  {"source_files": c.scope.source_files},
                  fix="add the failing test that this change makes pass, in the same push")


def minimal_diff(rule, c: Ctx):
    cap = c.cfg["solution_check"]["max_changed_lines"]
    if c.scope.changed_lines <= cap:
        return Result(rule, True, f"{c.scope.changed_lines} changed lines (budget {cap})")
    if re.search(r"^Scope:\s*\S", c.scope.intent, re.M):
        return Result(rule, True, f"{c.scope.changed_lines} changed lines over budget {cap}, with a Scope: line")
    return Result(rule, False, f"{c.scope.changed_lines} changed lines, over the {cap}-line budget",
                  {"changed_lines": c.scope.changed_lines, "budget": cap},
                  fix="split the push, or add a 'Scope: <why this must be one change>' line to the intent")


def context_crossing(rule, c: Ctx):
    if len(c.scope.contexts) < 2:
        return Result(rule, True, "one bounded context or none")
    m = re.search(r"^Contexts:\s*(.+)$", c.scope.intent, re.M)
    named = {x.strip() for x in m.group(1).split(",")} if m else set()
    missing = [x for x in c.scope.contexts if x not in named]
    if not missing:
        return Result(rule, True, f"crossing declared: {', '.join(c.scope.contexts)}")
    return Result(rule, False, f"touches {', '.join(c.scope.contexts)} but does not declare {', '.join(missing)}",
                  {"contexts": c.scope.contexts, "declared": sorted(named)},
                  fix=f"add 'Contexts: {', '.join(c.scope.contexts)}' to the intent, or split the change by context")


def no_unmerged_paths(rule, c: Ctx):
    if not c.facts.unmerged:
        return Result(rule, True, "no unmerged paths")
    return Result(rule, False, f"{len(c.facts.unmerged)} unmerged path(s) in the index",
                  {"paths": c.facts.unmerged},
                  fix="resolve each path, run the tests, `git add` it, and commit the merge")


def no_conflict_markers(rule, c: Ctx):
    hits = {}
    for ch in c.facts.changes:
        blob = git(c.root, "show", f"{c.facts.range.head}:{ch.path}", check=False)
        lines = [i + 1 for i, ln in enumerate(blob.splitlines()) if MARKER.match(ln)]
        if lines:
            hits[ch.path] = lines[:10]
    if not hits:
        return Result(rule, True, f"{len(c.facts.changes)} changed file(s) carry no conflict markers")
    return Result(rule, False, f"conflict markers in {len(hits)} file(s)", {"markers": hits},
                  fix="finish the resolution in each file: keep one side or combine them, delete the markers")


def conflict_record(rule, c: Ctx):
    rec = {mg["sha"][:12]: {"parents": [p[:12] for p in mg["parents"]],
                            "changed_by_both": changed_by_both(c.root, mg)} for mg in c.facts.merges}
    return Result(rule, True, f"{len(rec)} merge commit(s) recorded", {"merges": rec})


CHECKS = {f.__name__: f for f in (intent_present, blast_radius, test_with_source, minimal_diff,
                                  context_crossing, no_unmerged_paths, no_conflict_markers,
                                  conflict_record)}


def run(rules: list, ctx: Ctx) -> list:
    """Every rule with a pre-gauntlet check, whether or not it made the slice: the slice ranks
    what an agent reads; enforcement does not depend on rank."""
    out = []
    for r in rules:
        if not r.check or r.check in POST_GAUNTLET:
            continue
        if not applies(r, ctx.scope):
            continue
        res = CHECKS[r.check](r.id, ctx)
        res.details.setdefault("blocking", r.blocking)
        out.append(res)
    return out
