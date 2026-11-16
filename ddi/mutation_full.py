"""Full-population mutation run: every mutant of every source file, no sampling.

The pre-push gate samples (max_mutants) to stay fast. This runs the whole population, split
into disjoint shards so CI can run them in parallel, then merges them into one complete count.

    python -m ddi.mutation_full run   --repo . --shard 0 --of 4 --out shard-0.json
    python -m ddi.mutation_full merge shard-*.json --out full.json --summary full.md

Mutant k (in plan_mutants order, over source_roots sorted) belongs to shard k % of. The merge
checks that the shards come from the same plan, are each present exactly once, and together
cover every mutant; anything else is an error, not a partial count.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import config
from .gates.mutation import plan_mutants, run_mutants


def source_files(root: Path, cfg: dict) -> list:
    out = []
    for s in cfg["project"]["source_roots"]:
        out += [f.relative_to(root).as_posix() for f in sorted((root / s).rglob("*.py"))
                if "__pycache__" not in f.parts]
    return sorted(out)


def plan_id(plan: list) -> str:
    """A digest of the mutant list, so shards from different trees cannot be merged."""
    h = hashlib.sha256()
    for rel, _, site in plan:
        h.update(f"{rel}:{site[4]}:{site[3]}\n".encode())
    return h.hexdigest()[:16]


def shard_of(plan: list, shard: int, of: int) -> list:
    if not 0 <= shard < of:
        raise ValueError(f"shard {shard} is not in 0..{of - 1}")
    return [m for k, m in enumerate(plan) if k % of == shard]


def run(root: Path, shard: int, of: int, timeout_s: float) -> dict:
    cfg = config.load(root)
    plan = plan_mutants(root, source_files(root, cfg))
    mine = shard_of(plan, shard, of)
    killed, survived, survivors, secs = run_mutants(root, cfg["project"], mine, timeout_s)
    return {"schema": "ddi.mutation_shard.v1", "plan_id": plan_id(plan), "total": len(plan),
            "shard": shard, "of": of, "run": len(mine), "killed": killed, "survived": survived,
            "survivors": survivors, "seconds": secs,
            "trust": "unverified_claim"}


def merge(shards: list) -> dict:
    if not shards:
        raise ValueError("no shards to merge")
    ids = {s["plan_id"] for s in shards}
    ofs = {s["of"] for s in shards}
    if len(ids) != 1 or len(ofs) != 1:
        raise ValueError(f"shards from different plans or splits: plan_ids {sorted(ids)}, of {sorted(ofs)}")
    of = ofs.pop()
    got = sorted(s["shard"] for s in shards)
    if got != list(range(of)):
        raise ValueError(f"expected shards 0..{of - 1} exactly once, got {got}")
    totals = {s["total"] for s in shards}
    if len(totals) != 1:
        raise ValueError(f"shards disagree on the plan size: {sorted(totals)}")
    total = totals.pop()
    ran = sum(s["run"] for s in shards)
    if ran != total:
        raise ValueError(f"shards ran {ran} mutants, the plan has {total}")
    killed = sum(s["killed"] for s in shards)
    survived = sum(s["survived"] for s in shards)
    survivors = sorted((x for s in shards for x in s["survivors"]),
                       key=lambda x: (x["file"], x["line"], x["mutation"]))
    return {"schema": "ddi.mutation_full.v1", "plan_id": ids.pop(), "total": total,
            "killed": killed, "survived": survived,
            "score": round(killed / total, 4) if total else None,
            "survivors": survivors, "trust": "unverified_claim"}


def summary(full: dict) -> str:
    lines = ["# Full mutation run", "",
             f"Every mutant of every source file: **{full['total']}** "
             f"(plan `{full['plan_id']}`), no sampling.", "",
             f"- killed: **{full['killed']}**",
             f"- survived: **{full['survived']}**",
             f"- score: **{full['score']:.1%}**" if full["score"] is not None else "- score: n/a",
             "", "## Survivors", ""]
    by_file = {}
    for s in full["survivors"]:
        by_file.setdefault(s["file"], []).append(s)
    for f, ss in sorted(by_file.items()):
        lines.append(f"- `{f}` ({len(ss)}): " + ", ".join(f"line {s['line']} {s['mutation']}" for s in ss))
    if not by_file:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ddi.mutation_full")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--repo", default=".")
    r.add_argument("--shard", type=int, default=0)
    r.add_argument("--of", type=int, default=1)
    r.add_argument("--timeout", type=float, default=180)
    r.add_argument("--out", required=True)
    m = sub.add_parser("merge")
    m.add_argument("shards", nargs="+")
    m.add_argument("--out", required=True)
    m.add_argument("--summary")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        res = run(Path(a.repo).resolve(), a.shard, a.of, a.timeout)
        Path(a.out).write_text(json.dumps(res, indent=2) + "\n")
        print(f"shard {a.shard}/{a.of}: {res['run']} of {res['total']} mutants, "
              f"{res['killed']} killed, {res['survived']} survived, {res['seconds']} s")
        return 0
    try:
        full = merge([json.loads(Path(p).read_text()) for p in a.shards])
    except ValueError as e:
        print(f"merge refused: {e}", file=sys.stderr)
        return 2
    Path(a.out).write_text(json.dumps(full, indent=2) + "\n")
    text = summary(full)
    if a.summary:
        Path(a.summary).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
