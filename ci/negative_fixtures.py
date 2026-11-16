"""Every negative fixture must be rejected, for its own stated reason; every positive one accepted.
Runs in the pinned CI image (ci/run.sh). Exit 1 names each fixture that behaved otherwise."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ddi import schema  # noqa: E402

FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
REASON = {"NEG-EEP-01": "kind", "NEG-EEP-02": "check", "NEG-CAPA-01": "status", "NEG-CAPA-02": "trust",
          "NEG-CAPA-03": "ci_provenance", "NEG-CAPA-04": "ci_signed_evidence", "NEG-EVT-01": "ci_signed_evidence"}
POSITIVE = {"eep-rule.json": "eep-rule.schema.json", "capa-draft.json": "capa-conflict-draft.schema.json",
            "event.json": "ddi-event.schema.json"}

bad = []
index = json.loads((FIX / "negative/index.json").read_text())
if sorted(index) != sorted(REASON):
    bad.append(f"index lists {sorted(index)}, expected {sorted(REASON)}")
for name, meta in sorted(index.items()):
    errs = schema.errors(json.loads((FIX / f"negative/{name}.json").read_text()), schema.load(meta["schema"]))
    hit = [e for e in errs if REASON.get(name, "\0") in e]
    print(f"{'rejected' if hit else 'WRONG   '} {name}: {hit[0] if hit else errs or 'accepted'}")
    if not hit:
        bad.append(name)
for f, s in POSITIVE.items():
    errs = schema.errors(json.loads((FIX / "positive" / f).read_text()), schema.load(s))
    print(f"{'accepted' if not errs else 'WRONG   '} positive/{f}")
    if errs:
        bad.append(f"positive/{f}")
sys.exit(1 if bad else 0)
