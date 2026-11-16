"""The single loop. One call is one iteration, from the push range to a decision:

  1. Rotor + EEP slice   scope the push; rank habits, DDD, CAPA and conflict rules for it
  2. Solution check      the slice's machine checks: premise, blast radius, test-first, minimal
                         diff, bounded contexts, unmerged paths, conflict markers
  3. Capture             every error event opens or updates a CAPA / conflict draft
  4. Gauntlet            CRAP -> mutation -> TDD pyramid, on the exact pushed tree, stop at first fail
  5. Feedback            the artifact, the drafts and the events feed the next iteration's slice;
                         Level 2/3 only behind their flags

Nothing here holds a credential or reaches a network. A decision is a claim about a local run.
"""

import io
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path

from . import artifact, capa, checks, config, eep, extensions, gitio, rotor
from .events import EventLog, previous_runs
from .gates import crap, mutation, pyramid
from .schema import check as schema_check


def export_tree(root: Path, sha: str, dest: Path) -> None:
    """The pushed commit's tree, exactly: no untracked files, no uncommitted edits."""
    p = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", sha], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"git archive {sha}: {p.stderr.decode().strip()}")
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as t:
        t.extractall(dest, filter="data")


def feedback(art_dir: Path) -> dict:
    """What earlier iterations learned: rules and gates that tripped, by how often."""
    tripped = {}
    for e in previous_runs(art_dir):
        if e["kind"] == "error" and e.get("source"):
            tripped[e["source"]] = tripped.get(e["source"], 0) + 1
    return tripped


def run(root: Path, *, remote_sha: str = "", local_sha: str = "", base: str = "", head: str = "",
        clock=None, log=print) -> dict:
    root = root.resolve()
    cfg = config.load(root)
    art_dir = root / cfg["artifacts"]["dir"]
    prior = feedback(art_dir)
    ev = EventLog(art_dir, **({"clock": clock} if clock else {}))
    drafts = capa.Drafts(art_dir, **({"clock": clock} if clock else {}))
    ext = extensions.load(cfg)
    caveats = list(ext["caveats"])

    rng, facts, scope, rules, sl, chunks = _scope_and_slice(root, cfg, art_dir, ev, prior, caveats, ext,
                                                             remote_sha, local_sha, base, head)
    results, blocked = _solution_check(root, cfg, facts, scope, rules, ev, drafts)
    gates = [] if blocked else _gauntlet(root, cfg, scope, rng, ev, drafts, caveats)
    blocked = blocked or any(not g.ok for g in gates)
    post = _post_checks(rules, gates, ev, drafts)
    blocked = _capture_post(post, ev, drafts) or blocked
    results += post

    decision = "push_blocked" if blocked else "push_allowed"
    ext_out = _seams(ext, ev, drafts)
    ev.state("push.allowed" if not blocked else "push.blocked", drafts=drafts.touched)
    art = artifact.build(run_id=ev.run_id, decision=decision, rng=rng, scope=scope, slice_=sl,
                         chunks=chunks, checks=results, gates=gates, drafts=drafts.touched,
                         events=ev, caveats=caveats, extensions=ext_out)
    j, m = artifact.write(art_dir, art)
    art["_paths"] = {"json": str(j), "markdown": str(m)}
    _report(art, log)
    return art


def _scope_and_slice(root, cfg, art_dir, ev, prior, caveats, ext, remote_sha, local_sha, base, head):
    """Phase 1: Rotor scopes the push; the EEP slice ranks rules for it."""
    if base or head:
        rng = gitio.PushRange(base, head or gitio.git(root, "rev-parse", "HEAD").strip())
    else:
        rng = gitio.resolve_range(root, cfg["rotor"]["base_ref"], remote_sha, local_sha)
    facts = gitio.facts(root, rng)
    scope = rotor.classify(facts, cfg)
    ev.state("rotor.scoped", tags=scope.tags, contexts=scope.contexts, risks=scope.risks,
             changed_lines=scope.changed_lines)
    if gitio.git(root, "status", "--porcelain", "--untracked-files=no").strip():
        caveats.append("the working tree has uncommitted changes: they are not in the push and were not tested")
    rules = eep.load_rules(root, cfg["eep"]["catalogs"])
    sl = eep.slice_rules(rules, scope, cfg["eep"]["slice_size"], prior)
    chunks = eep.chunk([eep.render_rule(r, cfg, scope) for r in sl], cfg["eep"]["chunk_chars"])
    (art_dir / "slice.md").write_text("# EEP slice for this push\n\n" + "\n\n---\n\n".join(chunks) + "\n")
    ev.state("eep.sliced", rules=[r.id for r in sl], chunks=len(chunks), feedback=prior)
    if "level2" in ext["loaded"]:
        caveats.append("level2 enabled: context lookup is a roadmap stub and was not queried")
    return rng, facts, scope, rules, sl, chunks


def _solution_check(root, cfg, facts, scope, rules, ev, drafts):
    """Phases 2 and 3: the slice's machine checks; every failure is an error event and a draft."""
    results = checks.run(rules, checks.Ctx(root, cfg, facts, scope))
    by_id = {r.id: r for r in rules}
    blocked = False
    for res in results:
        if not res.ok:
            rule = by_id[res.rule]
            ev.error("check.failed", source=res.rule, message=res.message, blocking=rule.blocking)
            _draft_for_check(drafts, ev, cfg, scope, rule, res)
            blocked = blocked or rule.blocking
        elif res.details.get("merges"):
            _record_merges(drafts, ev, cfg, scope, res)
    ev.state("check.done", blocked=blocked, results=len(results))
    return results, blocked


def _draft_for_check(drafts, ev, cfg, scope, rule, res):
    drafts.upsert(source=res.rule, subject=_subject(res), kind="conflict" if rule.kind == "conflict" else "capa",
                  run_id=ev.run_id, domain_context=", ".join(scope.contexts) or None,
                  event={"rule": res.rule, "title": rule.title, "message": res.message},
                  conflicting_paths=res.details.get("paths") or list(res.details.get("markers", {})) or None,
                  assumptions=f"{rule.title}: {rule.text}",
                  root_cause="(to confirm) " + res.message,
                  invariant_check=_invariants(cfg, scope),
                  correction=res.fix,
                  verification=f"re-run `ddi run`: {res.rule} passes",
                  sync_notes="paste-free: link this file from the PR or issue; the harness does not post")


def _record_merges(drafts, ev, cfg, scope, res):
    """CON-003: a merge whose parents both changed the same paths gets a conflict draft."""
    for sha, m in res.details["merges"].items():
        if not m["changed_by_both"]:
            continue
        ev.error("merge.both_sides_changed", source=res.rule, merge=sha, paths=m["changed_by_both"])
        drafts.upsert(source=res.rule, subject=f"merge {sha}", kind="conflict", run_id=ev.run_id,
                      domain_context=", ".join(scope.contexts) or None,
                      event={"merge": sha, "parents": m["parents"]},
                      conflicting_paths=m["changed_by_both"],
                      assumptions="both parents changed these paths since their merge base",
                      invariant_check=_invariants(cfg, scope),
                      correction="record which side each hunk kept and why",
                      verification="the gauntlet below runs on the merged tree (CON-004)",
                      sync_notes="paste-free: link this file from the PR")


def _gauntlet(root, cfg, scope, rng, ev, drafts, caveats):
    """Phase 4: CRAP -> mutation -> pyramid on the exact pushed tree; stop at the first failure."""
    gates = []
    with tempfile.TemporaryDirectory(prefix="ddi-tree-") as tmp, \
            tempfile.TemporaryDirectory(prefix="ddi-work-") as work:
        tree = Path(tmp)
        export_tree(root, rng.head, tree)
        c = crap.gate(tree, cfg, scope, Path(work))
        gates.append(c)
        if c.ok:
            gates.append(mutation.gate(tree, cfg, scope, Path(work)))
        if all(g.ok for g in gates):
            gates.append(pyramid.gate(tree, cfg, scope, Path(work), suite_ok=c.metrics["suite"]["ok"]))
    for g in gates:
        caveats += [f"{g.gate}: {x}" for x in g.caveats]
        if g.ok:
            ev.state(f"gate.{g.gate}.passed", summary=g.summary, seconds=g.seconds)
        else:
            ev.error(f"gate.{g.gate}.blocked", source=f"gate.{g.gate}", summary=g.summary, seconds=g.seconds)
            for f in g.findings[:10]:
                _draft_for_gate(drafts, ev, cfg, scope, g, f)
    return gates


def _draft_for_gate(drafts, ev, cfg, scope, g, f):
    drafts.upsert(source=f"gate.{g.gate}", subject=f["subject"], kind="capa", run_id=ev.run_id,
                  domain_context=", ".join(scope.contexts) or None,
                  event={"gate": g.gate, "message": f["message"], "metrics": _small(g.metrics)},
                  assumptions=f"{g.gate} gate thresholds in harness.toml",
                  root_cause="(to confirm) " + f["message"],
                  invariant_check=_invariants(cfg, scope),
                  correction=f["fix"],
                  verification=f"re-run `ddi run`: gate {g.gate} passes",
                  caveats=g.caveats or None,
                  sync_notes="paste-free: link this file from the PR or issue")


def _capture_post(post, ev, drafts) -> bool:
    """Post-gauntlet rules (HAB-005, CAPA-001, CAPA-003): a failure is an error with a draft."""
    blocked = False
    for p in post:
        if not p.ok:
            ev.error("check.failed", source=p.rule, message=p.message, blocking=True)
            drafts.upsert(source=p.rule, subject=p.rule, kind="capa", run_id=ev.run_id,
                          event={"rule": p.rule, "message": p.message},
                          root_cause="(to confirm) " + p.message,
                          correction="fix the harness or the gate so this run's evidence is complete",
                          verification=f"re-run `ddi run`: {p.rule} passes",
                          sync_notes="paste-free: link this file from the PR or issue")
            blocked = True
    return blocked


def _seams(ext, ev, drafts) -> dict:
    """Phase 5: Level 2 / Level 3, only behind their flags."""
    out = {"level2": "enabled (stub)" if "level2" in ext["loaded"] else "disabled", "level3": "disabled"}
    if "level3" not in ext["loaded"]:
        return out
    nodes, edges = [{"id": ev.run_id, "type": "run"}], []
    for fp in drafts.touched:
        d = drafts.load(fp)
        nodes.append({"id": fp, "type": "draft"})
        if not any(n["id"] == d["source"] for n in nodes):
            nodes.append({"id": d["source"], "type": "gate" if d["source"].startswith("gate.") else "rule"})
        edges += [{"from": ev.run_id, "to": fp, "type": "emitted"},
                  {"from": fp, "to": d["source"], "type": "tripped_by"}]
    schema_check({"schema": "ddi.capa_graph_export.v1", "nodes": nodes, "edges": edges},
                 "capa-graph-export.schema.json", "level3 export")
    out["level3"] = ext["loaded"]["level3"].export(nodes, edges)
    return out


def _subject(res) -> str:
    d = res.details
    if d.get("paths"):
        return ",".join(d["paths"][:5])
    if d.get("markers"):
        return ",".join(list(d["markers"])[:5])
    if d.get("source_files"):
        return ",".join(d["source_files"][:5])
    return res.rule


def _invariants(cfg, scope):
    inv = [f"{c}: {i}" for c in scope.contexts for i in cfg["contexts"][c].get("invariants", [])]
    return inv or None


def _small(metrics):
    return {k: v for k, v in metrics.items() if k in ("score", "worst", "threshold", "killed", "survived", "counts", "suite")}


def _post_checks(rules, gates, ev, drafts):
    out = []
    wanted = {r.check: r.id for r in rules if r.check in checks.POST_GAUNTLET}
    if "evidence_recorded" in wanted:
        bare = [g.gate for g in gates if not g.commands and not g.caveats and g.gate != "pyramid"]
        out.append(checks.Result(wanted["evidence_recorded"], not bare,
                                 "every gate that ran recorded its command and exit code" if not bare
                                 else f"gate(s) with no recorded command: {', '.join(bare)}"))
    if "capa_on_block" in wanted:
        errs = [e for e in ev.events if e["kind"] == "error" and e.get("source")]
        srcs = {drafts.load(fp)["source"] for fp in drafts.touched}
        missing = sorted({e["source"] for e in errs} - srcs)
        out.append(checks.Result(wanted["capa_on_block"], not missing,
                                 f"{len(errs)} error event(s), each with a draft" if not missing
                                 else f"error event(s) with no draft: {', '.join(missing)}"))
    if "draft_is_claim" in wanted:
        bad = [fp for fp in drafts.touched if drafts.load(fp)["status"] != "draft"]
        out.append(checks.Result(wanted["draft_is_claim"], not bad,
                                 f"{len(drafts.touched)} draft(s), all status=draft, trust=unverified_claim"
                                 if not bad else f"draft(s) not in draft status: {bad}"))
    return out


def _report(a, log):
    ok = a["decision"] == "push_allowed"
    log(f"ddi: {'push allowed' if ok else 'PUSH BLOCKED'} (run {a['run_id']}, "
        + (f"first error at {a['mttd']['seconds_to_first_error']} s, " if a['mttd']['seconds_to_first_error'] is not None else "no errors, ")
        + f"run {a['mttd']['run_seconds']} s)")
    for c in a["solution_check"]:
        if not c["ok"]:
            log(f"  FAIL {c['rule']}: {c['message']}" + (f"\n       fix: {c['fix']}" if c["fix"] else ""))
    for g in a["gauntlet"]:
        log(f"  {'pass' if g['ok'] else 'FAIL'} {g['gate']}: {g['summary']}")
        if not g["ok"]:
            for f in g["findings"][:5]:
                log(f"       {f['subject']}: {f['message']}\n       fix: {f['fix']}")
    for fp in a["capa_drafts"]:
        log(f"  draft: .ddi/capa/{fp}.md")
    for c in a["caveats"]:
        log(f"  caveat: {c}")
    log(f"  artifact: {a['_paths']['markdown']}" if "_paths" in a else "")
