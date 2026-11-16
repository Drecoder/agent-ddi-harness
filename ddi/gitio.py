"""The git facts Rotor reads. Plain `git` subprocess calls; no network, no credentials."""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ZERO = "0" * 40


def git(root: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


@dataclass
class PushRange:
    base: str           # exclusive; "" = the working tree vs HEAD (no push range)
    head: str


@dataclass
class Change:
    path: str
    added: int
    deleted: int


@dataclass
class RepoFacts:
    range: PushRange
    changes: list = field(default_factory=list)          # [Change]
    messages: list = field(default_factory=list)         # commit messages in range
    unmerged: list = field(default_factory=list)         # paths with conflicts in the index
    merges: list = field(default_factory=list)           # [{"sha", "parents"}] merge commits


def resolve_range(root: Path, base_ref: str, remote_sha: str = "", local_sha: str = "") -> PushRange:
    head = local_sha or git(root, "rev-parse", "HEAD").strip()
    if remote_sha and remote_sha != ZERO:
        return PushRange(remote_sha, head)
    # New branch: everything since it left base_ref, if base_ref exists; else the root commit.
    mb = git(root, "merge-base", base_ref, head, check=False).strip()
    if mb:
        return PushRange(mb, head)
    roots = git(root, "rev-list", "--max-parents=0", head).split()
    return PushRange(roots[-1] + "^" if _has_parent(root, roots[-1]) else "", head)


def _has_parent(root, sha):
    return bool(git(root, "rev-parse", "--verify", "-q", sha + "^", check=False).strip())


def facts(root: Path, rng: PushRange) -> RepoFacts:
    f = RepoFacts(rng)
    if rng.base:
        spec = f"{rng.base}..{rng.head}"
        num = git(root, "diff", "--numstat", rng.base, rng.head)
        log = git(root, "log", "--format=%H%x00%P%x00%B%x1e", spec)
    else:
        # Whole history up to head (a repository's first push).
        empty = git(root, "hash-object", "-t", "tree", "/dev/null").strip()
        num = git(root, "diff", "--numstat", empty, rng.head)
        log = git(root, "log", "--format=%H%x00%P%x00%B%x1e", rng.head)
    for line in num.splitlines():
        a, d, p = line.split("\t", 2)
        f.changes.append(Change(p, 0 if a == "-" else int(a), 0 if d == "-" else int(d)))
    for rec in log.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, body = rec.split("\x00", 2)
        f.messages.append(body.strip())
        ps = parents.split()
        if len(ps) > 1:
            f.merges.append({"sha": sha, "parents": ps})
    f.unmerged = sorted(set(git(root, "diff", "--name-only", "--diff-filter=U").split()))
    return f


def changed_by_both(root: Path, merge: dict) -> list:
    """Paths both parents of a merge changed since their merge base: where a conflict could be."""
    p1, p2 = merge["parents"][:2]
    mb = git(root, "merge-base", p1, p2, check=False).strip()
    if not mb:
        return []
    a = set(git(root, "diff", "--name-only", mb, p1).split())
    b = set(git(root, "diff", "--name-only", mb, p2).split())
    return sorted(a & b)
