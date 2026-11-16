"""Rotor: read the push (diff, intent, conflicts) and say what kind of task it is.

Rotor decides nothing about passing. It turns repository facts into a scope (which bounded
contexts, source or tests or docs, a merge, a conflict) and risk signals, and the EEP slice
ranks rules from those. Same facts in, same scope out.
"""

from dataclasses import dataclass, field

from .gitio import RepoFacts

DOC_SUFFIXES = (".md", ".rst", ".txt")


@dataclass
class Scope:
    contexts: list = field(default_factory=list)   # bounded contexts touched
    tags: list = field(default_factory=list)       # source, tests, docs, config, merge, conflict, multi-context
    risks: list = field(default_factory=list)      # from risk_paths and risk_words
    source_files: list = field(default_factory=list)
    test_files: list = field(default_factory=list)
    changed_lines: int = 0
    intent: str = ""


def _under(path, roots):
    return any(path == r.rstrip("/") or path.startswith(r.rstrip("/") + "/") for r in roots)


def classify(facts: RepoFacts, cfg: dict) -> Scope:
    s = Scope()
    proj, rot = cfg["project"], cfg["rotor"]
    s.intent = "\n\n".join(m for m in facts.messages if m)
    for c in facts.changes:
        s.changed_lines += c.added + c.deleted
        p = c.path
        if _under(p, proj["test_roots"]):
            s.test_files.append(p)
        elif _under(p, proj["source_roots"]):
            s.source_files.append(p)
        for name, ctx in cfg["contexts"].items():
            if any(p.startswith(pre) for pre in ctx.get("paths", [])) and name not in s.contexts:
                s.contexts.append(name)
        for frag, risk in rot["risk_paths"].items():
            if frag in p and risk not in s.risks:
                s.risks.append(risk)
    low = s.intent.lower()
    for w in rot["risk_words"]:
        if w.lower() in low and w not in s.risks:
            s.risks.append(w)
    tags = set()
    if s.source_files:
        tags.add("source")
    if s.test_files:
        tags.add("tests")
    if any(c.path.endswith(DOC_SUFFIXES) for c in facts.changes):
        tags.add("docs")
    if len(s.contexts) > 1:
        tags.add("multi-context")
    if facts.merges:
        tags.add("merge")
    if facts.unmerged:
        tags.add("conflict")
    s.tags = sorted(tags)
    s.contexts.sort()
    s.risks.sort()
    return s
