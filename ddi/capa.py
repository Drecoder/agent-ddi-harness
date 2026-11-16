"""CAPA / conflict drafts: the structured record every error event opens or updates.

One file per fingerprint under .ddi/capa/, JSON (the record) plus Markdown (the same record,
ready to paste-free sync into an issue or PR). A repeat of the same failure updates the draft
and counts the recurrence (CAPA-002). Status is always `draft`: the harness's own runs are
claims until CI-signed results back them and the owner promotes them (CAPA-003).
"""

import hashlib
import json
import time
from pathlib import Path

from .schema import check

FIELDS = ["domain_context", "event", "conflicting_paths", "assumptions", "root_cause",
          "invariant_check", "correction", "preventive_action", "verification", "caveats",
          "sync_notes"]


def fingerprint(source: str, subject: str) -> str:
    return hashlib.sha256(f"{source}\x00{subject}".encode()).hexdigest()[:16]


class Drafts:
    def __init__(self, directory: Path, clock=time.time):
        self.dir = directory / "capa"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.touched = []

    def upsert(self, *, source: str, subject: str, kind: str, run_id: str, **fields) -> dict:
        """kind: capa | conflict. source: the rule or gate id. subject: what it tripped on."""
        fp = fingerprint(source, subject)
        path = self.dir / f"{fp}.json"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.clock()))
        if path.exists() and fp in self.touched:
            d = json.loads(path.read_text())    # same run: refine the draft, not a recurrence
        elif path.exists():
            d = json.loads(path.read_text())
            # A draft on disk is re-validated before it is touched: a hand-edited draft that
            # claims CI certification, or any status but draft, is refused, not carried forward.
            check(d, "capa-conflict-draft.schema.json", f"{path.name}")
            d["occurrences"] += 1
            d["last_seen"] = now
            d["runs"] = (d["runs"] + [run_id])[-20:]
        else:
            d = {"schema": "ddi.capa_draft.v1", "fingerprint": fp, "kind": kind,
                 "source": source, "subject": subject, "status": "draft",
                 "trust": "unverified_claim", "ci_provenance": None, "occurrences": 1,
                 "first_seen": now, "last_seen": now, "runs": [run_id],
                 **{k: None for k in FIELDS}}
        for k, v in fields.items():
            if k not in FIELDS:
                raise KeyError(f"unknown CAPA field {k!r}")
            if v not in (None, "", []):
                d[k] = v
        d["status"] = "draft"          # never anything else from here (CAPA-003)
        check(d, "capa-conflict-draft.schema.json", f"{fp}.json")
        path.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")
        (self.dir / f"{fp}.md").write_text(to_markdown(d))
        if fp not in self.touched:
            self.touched.append(fp)
        return d

    def load(self, fp: str) -> dict:
        d = json.loads((self.dir / f"{fp}.json").read_text())
        check(d, "capa-conflict-draft.schema.json", f"{fp}.json")
        return d


def to_markdown(d: dict) -> str:
    title = "Conflict draft" if d["kind"] == "conflict" else "CAPA draft"
    lines = [f"## {title}: {d['source']} on {d['subject']}", "",
             f"- **Status:** {d['status']}, {d['trust']} (ci_provenance: none; a claim until CI-signed evidence backs it)",
             f"- **Fingerprint:** `{d['fingerprint']}`; seen {d['occurrences']} time(s), "
             f"first {d['first_seen']}, last {d['last_seen']}"]
    for k in FIELDS:
        v = d.get(k)
        if v in (None, "", []):
            continue
        label = k.replace("_", " ").capitalize()
        if isinstance(v, list):
            lines.append(f"- **{label}:** " + "; ".join(f"`{x}`" if "/" in str(x) else str(x) for x in v))
        elif isinstance(v, dict):
            lines.append(f"- **{label}:** " + json.dumps(v, sort_keys=True))
        else:
            lines.append(f"- **{label}:** {v}")
    return "\n".join(lines) + "\n"
