"""The EEP rule engine: load catalogs, rank rules for a scope, cut the slice into chunks.

Habits, DDD rules, CAPA rules and merge-conflict rules are one schema (ddi.eep_rules.v1), so
the loop never consults a separate checklist. A rule with `check` is enforced by the solution
check; a rule without one shapes the prompt slice an agent works from.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .schema import check

KINDS = {"habit", "ddd", "capa", "conflict", "architecture"}
PHASES = ["rotor", "inner", "capture", "check", "gauntlet"]


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    phase: str
    title: str
    text: str
    weight: float
    triggers: dict
    check: str | None = None
    blocking: bool = True


HOME = Path(__file__).resolve().parent.parent


def load_rules(root: Path, catalogs: list) -> list:
    """A catalog path resolves in the repository first, then in the harness: a consumer may
    override a catalog by vendoring it, and otherwise gets the harness's own."""
    rules, seen = [], {}
    for cat in catalogs:
        f = root / cat if (root / cat).exists() else HOME / cat
        doc = json.loads(f.read_text())
        check(doc, "eep-catalog.schema.json", cat)
        for r in doc["rules"]:
            check(r, "eep-rule.schema.json", f"{cat}: {r.get('id', '?')}")
            if r["id"] in seen:
                raise ValueError(f"{cat}: {r['id']} already defined in {seen[r['id']]}")
            if r["kind"] not in KINDS:
                raise ValueError(f"{cat}: {r['id']} has unknown kind {r['kind']!r}")
            if r["phase"] not in PHASES:
                raise ValueError(f"{cat}: {r['id']} has unknown phase {r['phase']!r}")
            seen[r["id"]] = cat
            rules.append(Rule(r["id"], r["kind"], r["phase"], r["title"], r["text"],
                              float(r["weight"]), r.get("triggers", {}),
                              r.get("check"), bool(r.get("blocking", True))))
    return rules


def applies(rule: Rule, scope) -> bool:
    """A rule with scope triggers applies only when one of them is in the scope's tags."""
    want = rule.triggers.get("scopes")
    return not want or bool(set(want) & set(scope.tags))


def score(rule: Rule, scope, prior: dict | None = None) -> float:
    s = rule.weight
    if prior and prior.get(rule.id):
        s += min(1.0, 0.25 * prior[rule.id])   # closed feedback: what tripped before ranks higher
    t = rule.triggers
    if set(t.get("scopes", [])) & set(scope.tags):
        s += 1.0
    if set(t.get("risk", [])) & set(scope.risks):
        s += 1.0
    if "conflict" in scope.tags and rule.kind == "conflict":
        s += 2.0           # an unmerged push is about the conflict before anything else
    if scope.risks and rule.kind in ("habit", "capa"):
        s += 0.25 * len(scope.risks)
    return round(s, 3)


def slice_rules(rules: list, scope, size: int, prior: dict | None = None) -> list:
    """Applicable rules, highest score first; ties keep phase order, then id. Deterministic."""
    cand = [r for r in rules if applies(r, scope)]
    cand.sort(key=lambda r: (-score(r, scope, prior), PHASES.index(r.phase), r.id))
    return cand[:size]


def render_rule(rule: Rule, cfg: dict, scope) -> str:
    text = f"[{rule.id}] {rule.title} ({rule.kind}, {rule.phase}): {rule.text}"
    if rule.id == "DDD-003":
        for c in scope.contexts:
            inv = cfg["contexts"][c].get("invariants", [])
            if inv:
                text += f"\n  {c} invariants: " + "; ".join(inv)
    if rule.id == "DDD-001":
        for c in scope.contexts:
            text += f"\n  {c} terms: " + ", ".join(cfg["contexts"][c].get("terms", []))
    return text


def chunk(texts: list, limit: int) -> list:
    """Pack rendered rules into chunks of at most `limit` characters, never splitting a rule."""
    chunks, cur = [], ""
    for t in texts:
        if cur and len(cur) + 2 + len(t) > limit:
            chunks.append(cur)
            cur = ""
        cur = t if not cur else cur + "\n\n" + t
    if cur:
        chunks.append(cur)
    return chunks
