"""One append-only event log for both kinds of signal the loop runs on.

Event-driven: `state` events move a run through its phases (scoped, checked, gate passed, push
allowed). Error-driven: `error` events (broken premise, tripped gate, conflict, caveat) are
first-class too, and each one is what opens or updates a CAPA draft. Both land in
.ddi/events.jsonl, so the next iteration, and the owner, read them instead of a transcript.
"""

import json
import time
import uuid
from pathlib import Path

from .schema import check

STATE, ERROR = "state", "error"


class EventLog:
    def __init__(self, directory: Path, run_id: str | None = None, clock=time.time):
        self.path = directory / "events.jsonl"
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.clock = clock
        self.started = clock()
        self.events = []
        directory.mkdir(parents=True, exist_ok=True)

    def emit(self, kind: str, name: str, **data) -> dict:
        if kind not in (STATE, ERROR):
            raise ValueError(f"event kind must be state or error, got {kind!r}")
        e = {"schema": "ddi.event.v1", "run_id": self.run_id, "kind": kind, "name": name,
             "t": round(self.clock() - self.started, 3), **data}
        check(e, "ddi-event.schema.json", f"event {name}")
        self.events.append(e)
        with self.path.open("a") as f:
            f.write(json.dumps(e, sort_keys=True) + "\n")
        return e

    def state(self, name: str, **data) -> dict:
        return self.emit(STATE, name, **data)

    def error(self, name: str, **data) -> dict:
        return self.emit(ERROR, name, **data)

    def first_error_t(self):
        """Seconds from the start of the run to its first error: the local MTTD."""
        return next((e["t"] for e in self.events if e["kind"] == ERROR), None)


def previous_runs(directory: Path, limit: int = 200) -> list:
    """The last events from earlier runs, for the closed feedback into the next iteration."""
    p = directory / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines()[-limit:] if line.strip()]
