"""Load harness.toml. Missing keys take the defaults below, so a minimal file still runs."""

import copy
import tomllib
from pathlib import Path

DEFAULTS = {
    "project": {"source_roots": ["src"], "test_roots": ["tests"], "test_command": ""},
    "rotor": {"base_ref": "origin/main", "risk_paths": {}, "risk_words": []},
    "contexts": {},
    "eep": {"catalogs": ["eep-rules/habits.json", "eep-rules/ddd.json", "eep-rules/capa.json",
                         "eep-rules/conflict.json"], "slice_size": 12, "chunk_chars": 1200},
    "solution_check": {"max_changed_lines": 400, "require_test_with_source": True},
    "gates": {
        "crap": {"threshold": 30.0, "changed_only": True},
        "mutation": {"engine": "builtin", "report_path": "", "min_score": 0.8,
                     "max_mutants": 60, "timeout_s": 60},
        "pyramid": {"layers": {"unit": "tests/unit/**"}, "min_unit_share": 0.6,
                    "max_e2e_share": 0.1, "require_layers": ["unit"]},
    },
    "artifacts": {"dir": ".ddi"},
    "extensions": {
        "level2_rag_s3_capa": {"enabled": False},
        "level3_capa_tuner": {"enabled": False},
    },
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict) and k != "contexts":
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(root: Path, path: str = "harness.toml") -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    f = root / path
    if f.exists():
        _merge(cfg, tomllib.loads(f.read_text()))
    cfg["_root"] = str(root)
    return cfg
