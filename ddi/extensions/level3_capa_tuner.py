"""Level 3 (roadmap): export the run's CAPA drafts and events as graph JSON for a tuner.

The export is real and local (schemas/capa_graph_export.v1.json); the intelligence model and
tuner that will read it are not built. Nodes: the run, each draft, each rule or gate that
fired. Edges: run -emitted-> draft, draft -tripped_by-> rule/gate.
"""

import json
from pathlib import Path


class Sink:
    def __init__(self, conf: dict, root: str):
        self.path = Path(root) / conf.get("export_path", ".ddi/level3/capa_graph_export.json")

    def export(self, nodes: list, edges: list) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schema": "ddi.capa_graph_export.v1", "nodes": nodes, "edges": edges}
        self.path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        return str(self.path)
