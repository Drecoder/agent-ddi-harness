"""TDD pyramid gate: the suite keeps its shape, and it passes.

Tests are counted statically from the AST (test_* functions and methods), so the count does not
depend on a runner. Each test file lands in the first layer whose glob matches its path;
anything matching none is counted as e2e. The gate fails when a required layer is empty, when
unit tests fall below their minimum share, when e2e rises above its maximum, or when the suite
the CRAP gate ran did not pass.
"""

import ast
import fnmatch
import time
from pathlib import Path

from . import GateResult


def count_tests(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text(), str(path))
    except SyntaxError:
        return 0
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            n += 1
    return n


def layer_of(rel: str, layers: dict) -> str:
    for name, glob in layers.items():
        if fnmatch.fnmatch(rel, glob):
            return name
    return "e2e"


def gate(root: Path, cfg: dict, scope, workdir: Path, suite_ok: bool | None = None) -> GateResult:
    t0 = time.time()
    g = cfg["gates"]["pyramid"]
    counts = {name: 0 for name in g["layers"]}
    counts["e2e"] = 0
    files = {}
    for t in cfg["project"]["test_roots"]:
        d = root / t
        for f in sorted(d.rglob("test*.py")) if d.exists() else []:
            rel = f.relative_to(root).as_posix()
            n = count_tests(f)
            lay = layer_of(rel, g["layers"])
            counts[lay] += n
            files[rel] = {"layer": lay, "tests": n}
    total = sum(counts.values())
    r = GateResult("pyramid", True, "", metrics={"counts": counts, "total": total})
    share = {k: round(v / total, 3) if total else 0.0 for k, v in counts.items()}
    r.metrics["share"] = share
    for need in g["require_layers"]:
        if counts.get(need, 0) == 0:
            r.findings.append({"subject": f"layer {need}", "message": f"no {need} tests",
                               "fix": f"add tests under {g['layers'].get(need, '?')}"})
    if total and share.get("unit", 0) < g["min_unit_share"]:
        r.findings.append({"subject": "unit share", "message": f"unit tests are {share['unit']:.0%} of the suite, "
                           f"below {g['min_unit_share']:.0%}",
                           "fix": "push behaviour down into unit tests; keep slow layers for what only they can prove"})
    if total and share["e2e"] > g["max_e2e_share"]:
        r.findings.append({"subject": "e2e share", "message": f"{counts['e2e']} test(s) outside the declared layers "
                           f"({share['e2e']:.0%} > {g['max_e2e_share']:.0%})",
                           "fix": "move them under a declared layer, or declare their layer in harness.toml"})
    if suite_ok is False:
        r.findings.append({"subject": "suite", "message": "the suite did not pass",
                           "fix": "fix the failing tests"})
    r.ok = not r.findings
    r.summary = ", ".join(f"{k} {v}" for k, v in counts.items()) + (f"; {len(r.findings)} problem(s)" if r.findings else "")
    r.seconds = round(time.time() - t0, 2)
    return r
