"""Integration tests: the whole loop on throwaway git repositories, one designed-to-fail case per
rule or gate, plus the three demos. Each case asserts the decision, the reason and the draft."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(HOME / "tests"))
sys.path.insert(0, str(HOME / "examples"))

from support import ENV, commit, install_hook, make_repo, sh  # noqa: E402

from ddi.loop import run  # noqa: E402

TESTED = {"tests/unit/test_extra.py": "import unittest\n\nfrom billing.invoice import refund\n\n\n"
          "class Extra(unittest.TestCase):\n    def test_refund(self):\n        self.assertEqual(refund(5, 1), 4)\n"}


class LoopCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name))
        sh(self.repo, "git", "checkout", "-q", "-b", "work")

    def tearDown(self):
        self.tmp.cleanup()

    def run_loop(self):
        return run(self.repo, log=lambda *_: None)

    def failed(self, art):
        return {c["rule"] for c in art["solution_check"] if not c["ok"]}

    def drafts(self):
        return [json.loads(p.read_text()) for p in sorted((self.repo / ".ddi/capa").glob("*.json"))]

    def test_placeholder_intent_blocks_HAB_001(self):
        commit(self.repo, "wip", {"docs/notes.md": "notes\n"})
        art = self.run_loop()
        self.assertEqual(art["decision"], "push_blocked")
        self.assertIn("HAB-001", self.failed(art))
        self.assertEqual(art["gauntlet"], [])               # blocked before the gauntlet

    def test_source_without_a_test_blocks_HAB_003_with_a_draft(self):
        inv = (self.repo / "src/billing/invoice.py").read_text()
        commit(self.repo, "Billing: name the zero total", {"src/billing/invoice.py": inv + "\nZERO = 0\n"})
        art = self.run_loop()
        self.assertIn("HAB-003", self.failed(art))
        d = [x for x in self.drafts() if x["source"] == "HAB-003"]
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["status"], d[0]["trust"], d[0]["ci_provenance"]), ("draft", "unverified_claim", None))
        self.assertIn("billing: an invoice total equals the sum of its lines", d[0]["invariant_check"])

    def test_an_undeclared_cross_context_change_blocks_DDD_002(self):
        files = {"src/catalog/__init__.py": "", "src/catalog/sku.py": "def norm(s):\n    return s.strip().upper()\n",
                 "tests/unit/test_sku.py": "import unittest\n\nfrom catalog.sku import norm\n\n\nclass Sku(unittest.TestCase):\n"
                 "    def test_norm(self):\n        self.assertEqual(norm(' ab '), 'AB')\n",
                 "src/billing/tax.py": "RATE = 0\n"}
        commit(self.repo, "Add sku normalising and a tax rate", files)
        self.assertIn("DDD-002", self.failed(self.run_loop()))
        sh(self.repo, "git", "commit", "-q", "--amend", "-m",
           "Add sku normalising and a tax rate\n\nContexts: billing, catalog")
        self.assertNotIn("DDD-002", self.failed(self.run_loop()))

    def test_unmerged_paths_block_CON_001(self):
        inv = (self.repo / "src/billing/invoice.py").read_text()
        commit(self.repo, "Billing: one side", {"src/billing/invoice.py": inv.replace("return total", "return int(total)")})
        sh(self.repo, "git", "checkout", "-q", "-b", "other", "main")
        commit(self.repo, "Billing: other side", {"src/billing/invoice.py": inv.replace("return total", "return abs(total)")})
        self.assertNotEqual(sh(self.repo, "git", "merge", "work", check=False).returncode, 0)
        art = self.run_loop()
        self.assertIn("CON-001", self.failed(art))
        d = [x for x in self.drafts() if x["source"] == "CON-001"][0]
        self.assertEqual(d["kind"], "conflict")
        self.assertEqual(d["conflicting_paths"], ["src/billing/invoice.py"])

    def test_a_complex_untested_function_trips_the_crap_gate(self):
        branchy = "def classify(n):\n" + "".join(
            f"    if n == {i}:\n        return {i}\n" for i in range(8)) + "    return -1\n"
        commit(self.repo, "Billing: classify a line code", {"src/billing/codes.py": branchy, **TESTED})
        art = self.run_loop()
        gates = {g["gate"]: g for g in art["gauntlet"]}
        self.assertFalse(gates["crap"]["ok"])
        self.assertNotIn("mutation", gates)                   # strict order: stop at the first failure
        self.assertTrue(any(x["source"] == "gate.crap" and "classify" in x["subject"] for x in self.drafts()))

    def test_a_suite_with_no_unit_layer_trips_the_pyramid(self):
        sh(self.repo, "git", "mv", "tests/unit/test_invoice.py", "tests/integration/test_invoice_moved.py")
        commit(self.repo, "Tests: move invoice tests up a layer", {})
        art = self.run_loop()
        gates = {g["gate"]: g for g in art["gauntlet"]}
        self.assertTrue(gates["crap"]["ok"] and gates["mutation"]["ok"])
        self.assertFalse(gates["pyramid"]["ok"])

    def test_a_test_root_that_is_not_a_package_is_still_measured(self):
        for f in ("tests/__init__.py", "tests/unit/__init__.py", "tests/integration/__init__.py"):
            sh(self.repo, "git", "rm", "-q", f)
        commit(self.repo, "Tests: drop the package markers from the test roots", TESTED)
        art = self.run_loop()
        crap_gate = next(g for g in art["gauntlet"] if g["gate"] == "crap")
        self.assertTrue(crap_gate["metrics"]["suite"]["ok"], crap_gate)
        self.assertEqual(crap_gate["metrics"]["suite"]["ran"], 10)

    def test_a_merge_of_unrelated_histories_has_no_shared_paths(self):
        from ddi.gitio import changed_by_both, facts, PushRange
        sh(self.repo, "git", "checkout", "-q", "--orphan", "other")
        sh(self.repo, "git", "rm", "-rqf", ".")
        commit(self.repo, "Other history", {"other.txt": "x\n"})
        sh(self.repo, "git", "checkout", "-q", "work")
        sh(self.repo, "git", "merge", "-q", "--no-edit", "--allow-unrelated-histories", "other")
        head = sh(self.repo, "git", "rev-parse", "HEAD").stdout.strip()
        f = facts(self.repo, PushRange(sh(self.repo, "git", "rev-parse", "main").stdout.strip(), head))
        self.assertEqual(len(f.merges), 1)
        self.assertEqual(changed_by_both(self.repo, f.merges[0]), [])

    def test_merge_records_use_twelve_character_shas(self):
        from ddi import checks
        from ddi.gitio import facts, PushRange
        inv = (self.repo / "src/billing/invoice.py").read_text()
        commit(self.repo, "Billing: side a", {"src/billing/invoice.py": inv + "\nA = 1\n"})
        sh(self.repo, "git", "checkout", "-q", "-b", "side", "main")
        commit(self.repo, "Billing: side b", {"src/billing/invoice.py": "B = 2\n" + inv})
        sh(self.repo, "git", "merge", "-q", "--no-edit", "work")
        head = sh(self.repo, "git", "rev-parse", "HEAD").stdout.strip()
        f = facts(self.repo, PushRange(sh(self.repo, "git", "rev-parse", "main").stdout.strip(), head))
        r = checks.conflict_record("CON-003", checks.Ctx(self.repo, {}, f, None))
        (sha, m), = r.details["merges"].items()
        self.assertEqual((len(sha), {len(p) for p in m["parents"]}), (12, {12}))
        self.assertEqual(m["changed_by_both"], ["src/billing/invoice.py"])

    def test_level3_export_is_written_only_when_enabled_and_is_valid(self):
        commit(self.repo, "wip", {"docs/n.md": "n\n"})
        self.run_loop()
        self.assertFalse((self.repo / ".ddi/level3").exists())
        toml = (self.repo / "harness.toml").read_text() + "\n[extensions.level3_capa_tuner]\nenabled = true\n"
        (self.repo / "harness.toml").write_text(toml)
        art = self.run_loop()
        doc = json.loads((self.repo / ".ddi/level3/capa_graph_export.json").read_text())
        self.assertEqual(doc["schema"], "ddi.capa_graph_export.v1")
        self.assertTrue(any(e["type"] == "tripped_by" and e["to"] == "HAB-001" for e in doc["edges"]))
        ids = [n["id"] for n in doc["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))                       # each node once
        self.assertTrue(all(e["from"] in ids and e["to"] in ids for e in doc["edges"]))
        self.assertTrue(art["extensions"]["level3"].endswith("capa_graph_export.json"))

    def test_level2_enabled_without_config_is_a_caveat_not_a_block(self):
        commit(self.repo, "Billing: a refund test", TESTED)
        (self.repo / "harness.toml").write_text((self.repo / "harness.toml").read_text()
                                                + "\n[extensions.level2_rag_s3_capa]\nenabled = true\n")
        art = self.run_loop()
        self.assertEqual(art["decision"], "push_allowed")
        self.assertTrue(any("level2 enabled but unavailable" in c for c in art["caveats"]))

    def test_uncommitted_changes_are_a_caveat_and_are_not_tested(self):
        commit(self.repo, "Billing: a refund test", TESTED)
        (self.repo / "src/billing/invoice.py").write_text("raise SystemExit('not pushed')\n")
        art = self.run_loop()
        self.assertEqual(art["decision"], "push_allowed")    # the pushed tree is what was tested
        self.assertTrue(any("uncommitted" in c for c in art["caveats"]))

    def test_a_forged_draft_makes_the_cli_fail_closed(self):
        commit(self.repo, "wip", {"docs/n.md": "n\n"})
        self.run_loop()
        p = next((self.repo / ".ddi/capa").glob("*.json"))
        p.write_text(json.dumps(json.loads(p.read_text()) | {"trust": "ci_certified"}))
        env = {**ENV, "PYTHONPATH": str(HOME)}
        r = subprocess.run([sys.executable, "-m", "ddi", "run", "--repo", str(self.repo)], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("trust", r.stderr)

    def test_install_will_not_overwrite_a_foreign_hook(self):
        hooks = self.repo / ".git/hooks"
        (hooks / "pre-push").write_text("#!/bin/sh\necho theirs\n")
        env = {**ENV, "PYTHONPATH": str(HOME)}
        r = subprocess.run([sys.executable, "-m", "ddi", "install", "--repo", str(self.repo)], env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("echo theirs", (hooks / "pre-push").read_text())

    def test_a_blocked_push_never_reaches_the_remote(self):
        install_hook(self.repo)
        commit(self.repo, "wip", {"docs/n.md": "n\n"})
        p = sh(self.repo, "git", "push", "origin", "work", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("PUSH BLOCKED", p.stdout + p.stderr)
        self.assertEqual(sh(self.repo, "git", "ls-remote", "origin", "work").stdout, "")


class PrePushCommand(unittest.TestCase):
    """`ddi pre-push` in-process, fed what git writes to a pre-push hook's stdin."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def pre_push(self, stdin_text):
        import io
        from contextlib import redirect_stdout
        from ddi.__main__ import main
        old, sys.stdin = sys.stdin, io.StringIO(stdin_text)
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code = main(["pre-push", "--repo", str(self.repo)])
        finally:
            sys.stdin = old
        return code, out.getvalue()

    def test_nothing_to_push(self):
        self.assertEqual(self.pre_push(""), (0, "ddi: nothing to push\n"))

    def test_a_branch_delete_is_not_verified(self):
        code, out = self.pre_push(f"(delete) {'0' * 40} refs/heads/old {'1' * 40}\n")
        self.assertEqual(code, 0)
        self.assertIn("is a delete", out)

    def test_re_pushing_the_same_commit_is_allowed(self):
        sha = sh(self.repo, "git", "rev-parse", "main").stdout.strip()
        code, out = self.pre_push(f"refs/heads/main {sha} refs/heads/main {sha}\n")
        self.assertEqual(code, 0, out)

    def test_the_worst_ref_decides(self):
        main_sha = sh(self.repo, "git", "rev-parse", "main").stdout.strip()
        sh(self.repo, "git", "checkout", "-q", "-b", "bad")
        bad = commit(self.repo, "wip", {"docs/n.md": "n\n"})
        lines = (f"refs/heads/main {main_sha} refs/heads/main {main_sha}\n"
                 f"refs/heads/bad {bad} refs/heads/bad {'0' * 40}\n")
        code, out = self.pre_push(lines)
        self.assertEqual(code, 1)
        self.assertIn("PUSH BLOCKED", out)


class FullMutationRun(unittest.TestCase):
    def test_two_shards_of_a_real_project_merge_into_the_whole_plan(self):
        from ddi.mutation_full import merge, plan_mutants, run, source_files
        from ddi import config
        with tempfile.TemporaryDirectory() as t:
            repo = make_repo(Path(t))
            total = len(plan_mutants(repo, source_files(repo, config.load(repo))))
            shards = [run(repo, k, 2, 30) for k in range(2)]
        full = merge(shards)
        self.assertEqual(full["total"], total)
        self.assertEqual(total, 9)                              # the ledger fixture's plan
        self.assertEqual([s["run"] for s in shards], [5, 4])
        self.assertEqual(full["killed"] + full["survived"], total)
        self.assertEqual(full["trust"], "unverified_claim")


class Demos(unittest.TestCase):
    def test_the_three_demos_behave_as_designed(self):
        import demos
        for fn in (demos.demo_clean, demos.demo_tripped, demos.demo_conflict):
            with self.subTest(fn.__name__), tempfile.TemporaryDirectory() as t:
                r = fn(Path(t))
                self.assertTrue(r["as_designed"], {k: v for k, v in r.items() if k != "hook_output"})


if __name__ == "__main__":
    unittest.main()
