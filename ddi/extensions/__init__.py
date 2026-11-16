"""Roadmap seams. Level 1 imports an extension only when harness.toml enables it.

The contracts below are what Level 2 and Level 3 implement; nothing in Level 1 depends on them
being present, reachable or authenticated.
"""

from typing import Protocol


class CapaContextProvider(Protocol):
    """Level 2: given the run's scope, return related past CAPA records (read-only)."""

    def related(self, scope: dict, top_k: int) -> list: ...


class CapaGraphSink(Protocol):
    """Level 3: accept the run's CAPA drafts and events as a graph export for a tuner."""

    def export(self, nodes: list, edges: list) -> str: ...


def load(cfg: dict) -> dict:
    """Return the enabled extensions, by name. An enabled extension that fails to load is a
    caveat, never a block: Level 1 must run alone."""
    out, caveats = {}, []
    ext = cfg.get("extensions", {})
    if ext.get("level2_rag_s3_capa", {}).get("enabled"):
        try:
            from . import level2_rag_s3_capa
            out["level2"] = level2_rag_s3_capa.Provider(ext["level2_rag_s3_capa"])
        except Exception as e:                       # noqa: BLE001 — any failure degrades to Level 1
            caveats.append(f"level2 enabled but unavailable: {e}")
    if ext.get("level3_capa_tuner", {}).get("enabled"):
        from . import level3_capa_tuner
        out["level3"] = level3_capa_tuner.Sink(ext["level3_capa_tuner"], cfg["_root"])
    return {"loaded": out, "caveats": caveats}
