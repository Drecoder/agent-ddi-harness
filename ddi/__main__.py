"""ddi command line.

    ddi run [--repo PATH] [--base SHA --head SHA]   one loop iteration over a range
    ddi pre-push [--repo PATH]                       the git pre-push hook body (reads git's stdin)
    ddi install [--repo PATH]                        install the pre-push hook into a repository
    ddi slice [--repo PATH]                          print the EEP slice for the current push only
    ddi validate SCHEMA FILE...                      validate JSON files against a harness schema

Exit codes: 0 allowed / valid, 1 blocked / invalid, 2 usage or environment error.
"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from . import config, eep, gitio, rotor, schema
from .loop import run

HOOK = """#!/bin/sh
# Installed by `ddi install`. Runs the DDI loop on every push; a non-zero exit stops the push.
# Bypass only with intent: `git push --no-verify` skips every local hook.
DDI_HOME="{home}"
PY="${{DDI_PYTHON:-{python}}}"
PYTHONPATH="$DDI_HOME${{PYTHONPATH:+:$PYTHONPATH}}" exec "$PY" -m ddi pre-push --repo "$(git rev-parse --show-toplevel)"
"""


def cmd_run(a):
    art = run(Path(a.repo), base=a.base or "", head=a.head or "")
    return 0 if art["decision"] == "push_allowed" else 1


def cmd_pre_push(a):
    lines = [ln.split() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if not lines:
        print("ddi: nothing to push")
        return 0
    worst = 0
    for local_ref, local_sha, remote_ref, remote_sha in lines:
        if local_sha == gitio.ZERO:
            print(f"ddi: {remote_ref} is a delete; nothing to verify")
            continue
        print(f"ddi: verifying {local_ref} -> {remote_ref}")
        art = run(Path(a.repo), remote_sha=remote_sha, local_sha=local_sha)
        worst = max(worst, 0 if art["decision"] == "push_allowed" else 1)
    return worst


def cmd_install(a):
    root = Path(gitio.git(Path(a.repo), "rev-parse", "--show-toplevel").strip())
    hooks = Path(gitio.git(root, "rev-parse", "--git-path", "hooks").strip())
    hooks = hooks if hooks.is_absolute() else root / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    h = hooks / "pre-push"
    if h.exists() and "ddi install" not in h.read_text():
        print(f"ddi: {h} exists and is not ours; not overwritten", file=sys.stderr)
        return 2
    home = Path(__file__).resolve().parent.parent
    h.write_text(HOOK.format(home=home.as_posix(), python=Path(sys.executable).as_posix()))
    h.chmod(h.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"ddi: installed {h}")
    if not (root / "harness.toml").exists():
        print("ddi: no harness.toml in the repository; defaults apply (copy one from the harness)")
    return 0


def cmd_slice(a):
    root = Path(a.repo).resolve()
    cfg = config.load(root)
    rng = gitio.resolve_range(root, cfg["rotor"]["base_ref"])
    scope = rotor.classify(gitio.facts(root, rng), cfg)
    rules = eep.load_rules(root, cfg["eep"]["catalogs"])
    sl = eep.slice_rules(rules, scope, cfg["eep"]["slice_size"])
    for c in eep.chunk([eep.render_rule(r, cfg, scope) for r in sl], cfg["eep"]["chunk_chars"]):
        print(c, end="\n\n---\n\n")
    return 0


def cmd_validate(a):
    bad = 0
    for f in a.files:
        try:
            schema.check(json.loads(Path(f).read_text()), a.schema, f)
            print(f"valid   {f}")
        except schema.SchemaError as e:
            bad += 1
            print(f"INVALID {e}")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ddi")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "pre-push", "install", "slice"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=os.getcwd())
        if name == "run":
            p.add_argument("--base")
            p.add_argument("--head")
    v = sub.add_parser("validate")
    v.add_argument("schema")
    v.add_argument("files", nargs="+")
    a = ap.parse_args(argv)
    fn = {"run": cmd_run, "pre-push": cmd_pre_push, "install": cmd_install, "slice": cmd_slice,
          "validate": cmd_validate}[a.cmd]
    try:
        return fn(a)
    except (RuntimeError, schema.SchemaError, FileNotFoundError) as e:
        print(f"ddi: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
