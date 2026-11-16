"""Three local demos, each a real `git push` through the installed pre-push hook. No network,
no cloud, no API keys: the remote is a bare repository in a temp directory.

    python examples/demos.py [--keep]

1. clean     a scoped, tested change: Rotor -> EEP slice -> solution check -> CRAP -> mutation
             -> pyramid -> verification artifact; the push goes through.
2. tripped   a weak test lets mutants survive: the push is blocked, a CAPA draft is written,
             and a second attempt updates the same draft instead of opening another.
3. conflict  two branches change the refund invariant differently. The push with conflict
             markers left in is blocked; the finished resolution passes, with a conflict draft
             recording both sides.

Prints a benchmark table (wall time, MTTD, decision) and exits non-zero if any demo's
outcome differs from what it is designed to show.
"""

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
from support import commit, install_hook, make_repo, sh  # noqa: E402

DISCOUNT = '''"""Billing: discounts on an invoice total."""

from billing.invoice import InvoiceError


def discounted(total_cents, percent):
    if percent < 0 or percent > 100:
        raise InvoiceError("a discount is between 0 and 100 percent")
    return total_cents - (total_cents * percent) // 100
'''

STRONG_TEST = '''import unittest

from billing.discount import discounted
from billing.invoice import InvoiceError


class Discount(unittest.TestCase):
    def test_ten_percent(self):
        self.assertEqual(discounted(1000, 10), 900)

    def test_zero_and_full(self):
        self.assertEqual(discounted(1000, 0), 1000)
        self.assertEqual(discounted(1000, 100), 0)

    def test_rounds_the_discount_down(self):
        self.assertEqual(discounted(999, 10), 900)

    def test_out_of_range_is_refused(self):
        for p in (-1, 101):
            with self.assertRaises(InvoiceError):
                discounted(1000, p)
'''

WEAK_TEST = '''import unittest

from billing.discount import discounted


class Discount(unittest.TestCase):
    def test_returns_a_number(self):
        self.assertIsNotNone(discounted(1000, 10))
'''


def push(repo, branch):
    t0 = time.time()
    p = sh(repo, "git", "push", "-u", "origin", branch, check=False)
    return p.returncode, round(time.time() - t0, 2), p.stdout + p.stderr


def latest(repo):
    return json.loads((repo / ".ddi/verification/latest.json").read_text())


def drafts(repo):
    d = repo / ".ddi/capa"
    return [json.loads(f.read_text()) for f in sorted(d.glob("*.json"))] if d.exists() else []


def demo_clean(tmp):
    repo = make_repo(tmp)
    install_hook(repo)
    sh(repo, "git", "checkout", "-q", "-b", "feat/discount")
    commit(repo, "Billing: a percentage discount on an invoice total\n\n"
                 "Premise: a discount is 0-100 percent and never makes a total negative.",
           {"src/billing/discount.py": DISCOUNT, "tests/unit/test_discount.py": STRONG_TEST})
    code, secs, out = push(repo, "feat/discount")
    a = latest(repo)
    ok = (code == 0 and a["decision"] == "push_allowed" and a["status"] == "staged"
          and a["trust"] == "unverified_claim" and a["ci_provenance"] is None
          and [g["gate"] for g in a["gauntlet"]] == ["crap", "mutation", "pyramid"])
    return {"demo": "clean", "expected": "push allowed", "push_exit": code, "seconds": secs,
            "mttd_s": a["mttd"]["seconds_to_first_error"], "decision": a["decision"],
            "gates": {g["gate"]: g["summary"] for g in a["gauntlet"]}, "drafts": len(drafts(repo)),
            "as_designed": ok, "repo": str(repo), "hook_output": out}


def demo_tripped(tmp):
    repo = make_repo(tmp)
    install_hook(repo)
    sh(repo, "git", "checkout", "-q", "-b", "feat/discount-weak")
    commit(repo, "Billing: a percentage discount on an invoice total\n\n"
                 "Premise: a discount is 0-100 percent.",
           {"src/billing/discount.py": DISCOUNT, "tests/unit/test_discount.py": WEAK_TEST})
    code1, secs, out = push(repo, "feat/discount-weak")
    a = latest(repo)
    d1 = drafts(repo)
    # A second attempt with the same weakness updates the same drafts (CAPA-002).
    code2, _, _ = push(repo, "feat/discount-weak")
    d2 = drafts(repo)
    remote_has = sh(repo, "git", "ls-remote", "origin", "feat/discount-weak").stdout.strip()
    ok = (code1 != 0 and code2 != 0 and a["decision"] == "push_blocked" and d1
          and all(d["status"] == "draft" and d["trust"] == "unverified_claim" for d in d2)
          and len(d1) == len(d2) and all(d["occurrences"] == 2 for d in d2) and not remote_has)
    mut = next((g for g in a["gauntlet"] if g["gate"] == "mutation"), None)
    return {"demo": "tripped", "expected": "push blocked + CAPA draft", "push_exit": code1,
            "seconds": secs, "mttd_s": a["mttd"]["seconds_to_first_error"], "decision": a["decision"],
            "gates": {g["gate"]: g["summary"] for g in a["gauntlet"]},
            "drafts": len(d2), "occurrences_after_retry": [d["occurrences"] for d in d2],
            "survivors": (mut or {}).get("metrics", {}).get("survivors", [])[:3],
            "branch_reached_remote": bool(remote_has), "as_designed": ok, "repo": str(repo),
            "hook_output": out}


def demo_conflict(tmp):
    repo = make_repo(tmp)
    install_hook(repo)
    inv = (repo / "src/billing/invoice.py").read_text()
    # Branch A: a refund may not be zero. Branch B: a refund may not be negative.
    sh(repo, "git", "checkout", "-q", "-b", "a/refund-nonzero")
    commit(repo, "Billing: refuse a zero refund\n\nPremise: a refund of nothing is a client error.",
           {"src/billing/invoice.py": inv.replace(
               '    if amount_cents > charge_cents:\n',
               '    if amount_cents > charge_cents or amount_cents == 0:\n'),
            "tests/unit/test_refund_rules.py": "import unittest\n\nfrom billing.invoice import InvoiceError, refund\n\n\n"
            "class ZeroRefund(unittest.TestCase):\n    def test_zero_refund_is_refused(self):\n"
            "        with self.assertRaises(InvoiceError):\n            refund(500, 0)\n"})
    sh(repo, "git", "checkout", "-q", "main")
    sh(repo, "git", "checkout", "-q", "-b", "b/refund-nonnegative")
    commit(repo, "Billing: refuse a negative refund\n\nPremise: a negative refund would raise the charge.",
           {"src/billing/invoice.py": inv.replace(
               '    if amount_cents > charge_cents:\n',
               '    if amount_cents > charge_cents or amount_cents < 0:\n'),
            "tests/unit/test_refund_rules.py": "import unittest\n\nfrom billing.invoice import InvoiceError, refund\n\n\n"
            "class NegativeRefund(unittest.TestCase):\n    def test_negative_refund_is_refused(self):\n"
            "        with self.assertRaises(InvoiceError):\n            refund(500, -1)\n"})
    merge = sh(repo, "git", "merge", "a/refund-nonzero", check=False)
    unmerged = sh(repo, "git", "diff", "--name-only", "--diff-filter=U").stdout.split()
    # Step 1: commit with the markers still in (a hand resolution not finished).
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "--no-edit")
    code1, secs1, out1 = push(repo, "b/refund-nonnegative")
    a1 = latest(repo)
    # Step 2: finish it, keeping both invariants, and re-prove.
    commit(repo, "Billing: keep both refund rules from the merge\n\n"
                 "Resolution: a refund is neither zero nor negative; both sides' tests kept.",
           {"src/billing/invoice.py": inv.replace(
               '    if amount_cents > charge_cents:\n',
               '    if amount_cents > charge_cents or amount_cents <= 0:\n'),
            "tests/unit/test_refund_rules.py": "import unittest\n\nfrom billing.invoice import InvoiceError, refund\n\n\n"
            "class RefundRules(unittest.TestCase):\n    def test_zero_refund_is_refused(self):\n"
            "        with self.assertRaises(InvoiceError):\n            refund(500, 0)\n\n"
            "    def test_negative_refund_is_refused(self):\n"
            "        with self.assertRaises(InvoiceError):\n            refund(500, -1)\n\n"
            "    def test_smallest_refund_is_allowed(self):\n"
            "        self.assertEqual(refund(500, 1), 499)\n"})
    code2, secs2, out2 = push(repo, "b/refund-nonnegative")
    a2 = latest(repo)
    conflict_drafts = [d for d in drafts(repo) if d["kind"] == "conflict"]
    ok = (merge.returncode != 0 and unmerged and code1 != 0
          and any(c["rule"] == "CON-002" and not c["ok"] for c in a1["solution_check"])
          and code2 == 0 and a2["decision"] == "push_allowed"
          and any(d["source"] == "CON-003" for d in conflict_drafts)
          and any(d["source"] == "CON-002" for d in conflict_drafts))
    return {"demo": "conflict", "expected": "markers blocked, resolution allowed, conflict drafts",
            "push_exit": [code1, code2], "seconds": [secs1, secs2],
            "mttd_s": a1["mttd"]["seconds_to_first_error"], "decision": [a1["decision"], a2["decision"]],
            "unmerged_at_merge": unmerged,
            "conflict_drafts": [{"source": d["source"], "paths": d["conflicting_paths"]} for d in conflict_drafts],
            "as_designed": ok, "repo": str(repo), "hook_output": out1}


def main():
    keep = "--keep" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="ddi-demos-"))
    results = []
    try:
        for fn in (demo_clean, demo_tripped, demo_conflict):
            d = tmp / fn.__name__
            d.mkdir()
            t0 = time.time()
            r = fn(d)
            r["total_seconds"] = round(time.time() - t0, 2)
            results.append(r)
        print(f"{'demo':10} {'designed':9} {'decision':40} {'mttd_s':8} {'total_s':7}")
        for r in results:
            dec = r["decision"] if isinstance(r["decision"], str) else " -> ".join(r["decision"])
            print(f"{r['demo']:10} {'yes' if r['as_designed'] else 'NO':9} {dec:40} {str(r['mttd_s']):8} {r['total_seconds']:<7}")
        out = Path("demo-results.json")
        out.write_text(json.dumps([{k: v for k, v in r.items() if k != "hook_output"} for r in results],
                                  indent=2, default=str) + "\n")
        print(f"\ndetails: {out.resolve()}")
        if "-v" in sys.argv:
            for r in results:
                print(f"\n===== {r['demo']}: hook output =====\n{r['hook_output']}")
        return 0 if all(r["as_designed"] for r in results) else 1
    finally:
        if keep:
            print(f"kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
