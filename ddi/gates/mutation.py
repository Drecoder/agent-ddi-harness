"""Mutation gate: do the tests notice when the changed code is wrong?

builtin: for each changed Python source file, make one-token mutants from the AST (a comparison,
arithmetic or boolean operator swapped, a boolean constant flipped, a number moved by one), run
the suite against each in a scratch copy of the project, and count it killed when the suite
fails. Mutants are enumerated in a fixed order and sampled evenly up to max_mutants, so the same
tree gives the same mutants.

report: read a Stryker mutation.json (mutation-testing-report-schema) a TypeScript project
already produced, and score it the same way.

score = killed / (killed + survived); a timeout counts as killed, a mutant with no coverage as
survived.
"""

import ast
import copy
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import GateResult
from .crap import COVRUN

SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq,
        ast.NotEq: ast.Eq, ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv,
        ast.FloorDiv: ast.Mult, ast.Div: ast.Mult, ast.And: ast.Or, ast.Or: ast.And,
        ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}


def sites(tree: ast.AST) -> list:
    """Every mutation site: (node index in walk order, field, index-in-field, description)."""
    out = []
    for i, n in enumerate(ast.walk(tree)):
        if isinstance(n, ast.Compare):
            for j, op in enumerate(n.ops):
                if type(op) in SWAP:
                    out.append((i, "ops", j, f"{type(op).__name__}->{SWAP[type(op)].__name__}", n.lineno))
        elif isinstance(n, (ast.BinOp, ast.BoolOp)) and type(n.op) in SWAP:
            out.append((i, "op", None, f"{type(n.op).__name__}->{SWAP[type(n.op)].__name__}", n.lineno))
        elif isinstance(n, ast.Constant) and type(n.value) is bool:
            out.append((i, "value", None, f"{n.value}->{not n.value}", n.lineno))
        elif isinstance(n, ast.Constant) and type(n.value) in (int, float) and not isinstance(n.value, bool):
            out.append((i, "value", None, f"{n.value}->{n.value + 1}", n.lineno))
    return out


def _in_docstring_free_code(tree):
    """Line numbers of module/class/function docstrings, which are never mutated."""
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                skip.add(b.value.lineno)
    return skip


def mutate(tree: ast.AST, site) -> ast.AST:
    t = copy.deepcopy(tree)
    i, fld, j, _, _ = site
    node = list(ast.walk(t))[i]
    if fld == "ops":
        node.ops[j] = SWAP[type(node.ops[j])]()
    elif fld == "op":
        node.op = SWAP[type(node.op)]()
    elif type(node.value) is bool:
        node.value = not node.value
    else:
        node.value = node.value + 1
    return ast.fix_missing_locations(t)


def sample(items: list, n: int) -> list:
    if len(items) <= n:
        return items
    return [items[(k * len(items)) // n] for k in range(n)]


def _ignore(_dir, names):
    return [x for x in names if x in (".git", ".ddi", "__pycache__", "node_modules", ".venv")]


def builtin(root: Path, cfg: dict, scope, r: GateResult) -> None:
    g = cfg["gates"]["mutation"]
    proj = cfg["project"]
    files = [p for p in scope.source_files if p.endswith(".py") and (root / p).exists()]
    plan = []
    for rel in files:
        src = (root / rel).read_text()
        tree = ast.parse(src, rel)
        skip = _in_docstring_free_code(tree)
        plan += [(rel, tree, s) for s in sites(tree) if s[4] not in skip]
    chosen = sample(plan, g["max_mutants"])
    if len(chosen) < len(plan):
        r.caveats.append(f"sampled {len(chosen)} of {len(plan)} mutants (max_mutants)")
    r.metrics.update({"engine": "builtin", "files": files, "candidates": len(plan), "run": len(chosen)})
    if not chosen:
        r.ok, r.summary = True, "no mutation sites in changed Python source"
        r.metrics["score"] = None
        r.caveats.append("mutation not run: the push changes no mutable Python source"
                         + (f" ({len(files)} file(s) without sites)" if files else ""))
        return
    killed = survived = 0
    survivors = []
    with tempfile.TemporaryDirectory(prefix="ddi-mut-") as tmp:
        work = Path(tmp) / "p"
        shutil.copytree(root, work, ignore=_ignore)
        originals = {rel: (work / rel).read_text() for rel in files}
        t0 = time.time()
        for rel, tree, site in chosen:
            (work / rel).write_text(ast.unparse(mutate(tree, site)) + "\n")
            cmd = [sys.executable, "-I", str(COVRUN), str(work), str(Path(tmp) / "r.json"),
                   "--tests", *proj["test_roots"], "--sources", *proj["source_roots"], "--no-trace"]
            if proj.get("test_command"):
                cmd = shlex.split(proj["test_command"])
            try:
                p = subprocess.run(cmd, cwd=work, capture_output=True, timeout=g["timeout_s"])
                dead = p.returncode != 0
            except subprocess.TimeoutExpired:
                dead = True
            if dead:
                killed += 1
            else:
                survived += 1
                survivors.append({"file": rel, "line": site[4], "mutation": site[3]})
            (work / rel).write_text(originals[rel])
        r.commands.append({"cmd": f"{len(chosen)} mutant run(s) of the suite in a scratch copy",
                           "exit": 0, "seconds": round(time.time() - t0, 2)})
    score = killed / (killed + survived)
    r.metrics.update({"killed": killed, "survived": survived, "score": round(score, 3),
                      "survivors": survivors[:25]})
    by_file = {}
    for s in survivors:
        by_file.setdefault(s["file"], []).append(f"line {s['line']} {s['mutation']}")
    for f, muts in by_file.items():
        r.findings.append({"subject": f,
                           "message": f"{len(muts)} mutant(s) survived: " + "; ".join(muts[:8]),
                           "fix": "add assertions that fail when these operators or values change"})
    r.ok = score >= g["min_score"]
    r.summary = f"score {score:.0%} ({killed} killed, {survived} survived; minimum {g['min_score']:.0%})"
    if r.ok:
        r.findings = []          # survivors are listed in metrics; they do not block above the bar


def report(root: Path, cfg: dict, scope, r: GateResult) -> None:
    g = cfg["gates"]["mutation"]
    p = root / g["report_path"]
    if not p.exists():
        r.ok = False
        r.summary = f"engine=report but {g['report_path']} does not exist"
        r.findings.append({"subject": g["report_path"], "message": "no mutation report",
                           "fix": "run the project's mutation tool (e.g. `npx stryker run`) before pushing"})
        return
    data = json.loads(p.read_text())
    killed = survived = 0
    for f in data.get("files", {}).values():
        for m in f.get("mutants", []):
            st = m.get("status")
            if st in ("Killed", "Timeout"):
                killed += 1
            elif st in ("Survived", "NoCoverage"):
                survived += 1
    total = killed + survived
    score = killed / total if total else None
    r.metrics.update({"engine": "report", "report": g["report_path"], "killed": killed,
                      "survived": survived, "score": round(score, 3) if score is not None else None})
    r.caveats.append("report engine: the harness reads the report; it did not run the mutants")
    if score is None:
        r.ok, r.summary = True, "report has no scored mutants"
        r.caveats.append("mutation report has no scored mutants")
        return
    r.ok = score >= g["min_score"]
    r.summary = f"report score {score:.0%} ({killed} killed, {survived} survived; minimum {g['min_score']:.0%})"
    if not r.ok:
        r.findings.append({"subject": g["report_path"], "message": r.summary,
                           "fix": "kill the surviving mutants listed in the report"})


def gate(root: Path, cfg: dict, scope, workdir: Path) -> GateResult:
    t0 = time.time()
    r = GateResult("mutation", False, "")
    engine = cfg["gates"]["mutation"]["engine"]
    {"builtin": builtin, "report": report}[engine](root, cfg, scope, r)
    r.seconds = round(time.time() - t0, 2)
    return r
