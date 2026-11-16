"""The verification artifact: what one loop iteration ran, found and decided, JSON plus Markdown.

It is the run's claim, with the evidence behind it (every command, its exit code and time). It
becomes evidence only when CI re-runs the same gates and signs the result; the artifact says so.
"""

import json
from dataclasses import asdict
from pathlib import Path

from .schema import check


def build(*, run_id, decision, rng, scope, slice_, chunks, checks, gates, drafts, events,
          caveats, extensions) -> dict:
    mttd = events.first_error_t()
    per_gate = {g.gate: g.seconds for g in gates}
    return {
        "schema": "ddi.verification_artifact.v1",
        "run_id": run_id,
        "decision": decision,              # push_allowed | push_blocked
        "status": "staged",                # staged for review; never promoted by the harness
        "trust": "unverified_claim",       # a local run is a claim; CI-signed results are evidence
        "ci_provenance": None,             # filled only outside the harness, from CI's own signature
        "range": {"base": rng.base, "head": rng.head},
        "scope": asdict(scope) | {"intent": scope.intent[:2000]},
        "eep_slice": [{"id": r.id, "kind": r.kind, "phase": r.phase, "title": r.title} for r in slice_],
        "eep_chunks": len(chunks),
        "solution_check": [asdict(c) for c in checks],
        "gauntlet": [asdict(g) for g in gates],
        "capa_drafts": drafts,
        "mttd": {"seconds_to_first_error": mttd, "run_seconds": round(events.clock() - events.started, 3),
                 "gate_seconds": per_gate},
        "caveats": caveats,
        "extensions": extensions,
        "events": len(events.events),
    }


def write(directory: Path, art: dict) -> tuple:
    check(json.loads(json.dumps(art, default=str)), "verification-artifact.schema.json", "verification artifact")
    d = directory / "verification"
    d.mkdir(parents=True, exist_ok=True)
    j = d / f"{art['run_id']}.json"
    m = d / f"{art['run_id']}.md"
    j.write_text(json.dumps(art, indent=2, sort_keys=True, default=str) + "\n")
    m.write_text(markdown(art))
    (d / "latest.json").write_text(j.read_text())
    (d / "latest.md").write_text(m.read_text())
    return j, m


def markdown(a: dict) -> str:
    ok = a["decision"] == "push_allowed"
    out = [f"# DDI verification: {'push allowed' if ok else 'PUSH BLOCKED'}", "",
           f"Run `{a['run_id']}`, range `{a['range']['base'][:12] or '(root)'}..{a['range']['head'][:12]}`. "
           f"Status `{a['status']}`, trust `{a['trust']}`, ci_provenance none: evidence only once CI re-runs and signs it.", "",
           f"- **Scope:** {', '.join(a['scope']['tags']) or 'none'}; contexts: "
           f"{', '.join(a['scope']['contexts']) or 'none'}; risks: {', '.join(a['scope']['risks']) or 'none'}; "
           f"{a['scope']['changed_lines']} changed lines",
           f"- **MTTD:** " + (f"first error at {a['mttd']['seconds_to_first_error']} s" if a['mttd']['seconds_to_first_error'] is not None
                                else "no error") + f"; run {a['mttd']['run_seconds']} s",
           f"- **EEP slice ({len(a['eep_slice'])} rules, {a['eep_chunks']} chunk(s)):** "
           + ", ".join(r["id"] for r in a["eep_slice"]), "", "## Solution check", ""]
    for c in a["solution_check"]:
        out.append(f"- {'PASS' if c['ok'] else 'FAIL'} `{c['rule']}`: {c['message']}" + (f" → {c['fix']}" if not c["ok"] else ""))
    out += ["", "## Gauntlet (CRAP → mutation → pyramid)", ""]
    if not a["gauntlet"]:
        out.append("- not reached: the solution check blocked first")
    for g in a["gauntlet"]:
        out.append(f"- {'PASS' if g['ok'] else 'FAIL'} **{g['gate']}** ({g['seconds']} s): {g['summary']}")
        for f in g["findings"][:10]:
            out.append(f"  - `{f['subject']}`: {f['message']} → {f['fix']}")
        for cmd in g["commands"]:
            out.append(f"  - ran `{cmd['cmd'][:160]}` → exit {cmd['exit']} in {cmd['seconds']} s")
    if a["capa_drafts"]:
        out += ["", "## CAPA / conflict drafts", ""] + [f"- `.ddi/capa/{fp}.md`" for fp in a["capa_drafts"]]
    if a["caveats"]:
        out += ["", "## Caveats", ""] + [f"- {c}" for c in a["caveats"]]
    return "\n".join(out) + "\n"
