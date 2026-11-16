"""CRAP gate: Change Risk Anti-Patterns, per function.

    CRAP(f) = c(f)^2 * (1 - cov(f))^3 + c(f)

c is cyclomatic complexity from the AST (1 + decision points); cov is the share of the
function's statement lines the test suite executed. A complex function that tests barely touch
scores high; the same function well covered scores close to its complexity. The suite must pass
first: coverage from a failing suite measures nothing.
"""

import ast
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import GateResult

COVRUN = Path(__file__).with_name("covrun.py")


def complexity(fn: ast.AST) -> int:
    c = 1
    for n in _own_nodes(fn):
        if isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.IfExp, ast.ExceptHandler,
                          ast.Assert, ast.match_case)):
            c += 1
        elif isinstance(n, ast.BoolOp):
            c += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            c += 1 + len(n.ifs)
    return c


def _own_nodes(fn):
    """Nodes of fn's body, not of functions or classes nested inside it."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield n
        stack.extend(ast.iter_child_nodes(n))


def statement_lines(fn) -> set:
    """Line numbers of fn's own statements, without its docstring or nested definitions."""
    doc = None
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant) \
            and isinstance(fn.body[0].value.value, str):
        doc = fn.body[0]
    return {n.lineno for n in _own_nodes(fn) if isinstance(n, ast.stmt) and n is not doc}


def functions(path: Path, rel: str):
    tree = ast.parse(path.read_text(), rel)
    out = []

    def visit(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{ch.name}"
                out.append((name, ch))
                visit(ch, name + ".")
            elif isinstance(ch, ast.ClassDef):
                visit(ch, f"{prefix}{ch.name}.")
    visit(tree, "")
    return out


def crap(c: int, cov: float) -> float:
    return round(c * c * (1 - cov) ** 3 + c, 2)


def run_suite(root: Path, cfg: dict, out: Path, trace: bool = True) -> dict:
    proj = cfg["project"]
    t0 = time.time()
    if proj.get("test_command"):
        cmd = shlex.split(proj["test_command"])
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        res = {"ok": p.returncode == 0, "ran": None, "failures": None, "errors": None,
               "failed_tests": [], "output_tail": (p.stdout + p.stderr)[-4000:], "lines": None}
        rep = proj.get("coverage_report")
        if rep and (root / rep).exists():
            # coverage.py JSON: {"files": {path: {"executed_lines": [...]}}}
            data = json.loads((root / rep).read_text())
            res["lines"] = {k: v["executed_lines"] for k, v in data.get("files", {}).items()}
        exit_code = p.returncode
    else:
        cmd = [sys.executable, "-I", str(COVRUN), str(root), str(out), "--tests", *proj["test_roots"],
               "--sources", *proj["source_roots"]] + ([] if trace else ["--no-trace"])
        p = subprocess.run(cmd, capture_output=True, text=True)
        exit_code = p.returncode
        if out.exists():
            res = json.loads(out.read_text())
        else:
            res = {"ok": False, "ran": 0, "failures": 0, "errors": 1, "failed_tests": [],
                   "output_tail": p.stderr[-4000:], "lines": {}}
    res["command"] = {"cmd": " ".join(shlex.quote(c) for c in cmd), "exit": exit_code,
                      "seconds": round(time.time() - t0, 2)}
    return res


def gate(root: Path, cfg: dict, scope, workdir: Path) -> GateResult:
    t0 = time.time()
    g = cfg["gates"]["crap"]
    suite = run_suite(root, cfg, workdir / "coverage.json")
    r = GateResult("crap", False, "", commands=[suite["command"]])
    r.metrics["suite"] = {k: suite.get(k) for k in ("ran", "failures", "errors", "ok")}
    if not suite["ok"]:
        r.summary = "the test suite fails or ran no tests: coverage would measure nothing"
        r.findings.append({"subject": "test suite", "message": "; ".join(suite.get("failed_tests") or []) or
                           suite.get("output_tail", "")[-400:],
                           "fix": "make the suite pass (or add a test) before the CRAP gate can score"})
        r.seconds = round(time.time() - t0, 2)
        return r
    if suite["lines"] is None:
        r.caveats.append("test_command set without coverage_report: coverage counted as 0 for every function")
        suite["lines"] = {}
    files = [p for p in scope.source_files if p.endswith(".py")] if g["changed_only"] else \
        sorted(str(f.relative_to(root).as_posix()) for s in cfg["project"]["source_roots"]
               for f in (root / s).rglob("*.py"))
    rows, worst = [], 0.0
    for rel in files:
        f = root / rel
        if not f.exists():
            continue                      # deleted in this push
        hit = set(suite["lines"].get(rel, []))
        for name, fn in functions(f, rel):
            lines = statement_lines(fn)
            cov = len(lines & hit) / len(lines) if lines else 1.0
            c = complexity(fn)
            score = crap(c, cov)
            worst = max(worst, score)
            rows.append({"file": rel, "function": name, "line": fn.lineno, "complexity": c,
                         "coverage": round(cov, 3), "crap": score})
            if score > g["threshold"]:
                r.findings.append({"subject": f"{rel}::{name}",
                                   "message": f"CRAP {score} > {g['threshold']} (complexity {c}, coverage {cov:.0%})",
                                   "fix": "cover its branches with tests, or split it into simpler functions"})
    rows.sort(key=lambda x: -x["crap"])
    r.metrics.update({"threshold": g["threshold"], "functions": len(rows), "worst": worst, "top": rows[:10]})
    r.ok = not r.findings
    r.summary = (f"{len(rows)} function(s) in {len(files)} changed file(s); worst CRAP {worst}"
                 + ("" if r.ok else f"; {len(r.findings)} over {g['threshold']}"))
    r.seconds = round(time.time() - t0, 2)
    return r
