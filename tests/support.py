"""Throwaway git repositories with a small generic domain project, for tests and demos.

The project is a ledger with one bounded context (billing): an invoice whose total is the sum of
its lines, and a refund that never exceeds its charge. Everything runs locally; the "remote" is a
bare repository in the same temp directory.
"""

import os
import subprocess
import textwrap
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.invalid",
       "GIT_COMMITTER_NAME": "Demo", "GIT_COMMITTER_EMAIL": "demo@example.invalid",
       "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

HARNESS_TOML = """\
schema = "ddi.harness.v1"

[project]
source_roots = ["src"]
test_roots = ["tests"]

[rotor]
base_ref = "origin/main"
risk_words = ["hotfix", "revert"]

[contexts.billing]
paths = ["src/billing/"]
terms = ["invoice", "line", "refund", "charge"]
invariants = ["an invoice total equals the sum of its lines", "a refund never exceeds its charge"]

[contexts.catalog]
paths = ["src/catalog/"]
terms = ["product", "sku"]
invariants = ["a sku is unique within the catalog"]

[gates.crap]
threshold = 15.0

[gates.mutation]
min_score = 0.80
max_mutants = 40
timeout_s = 30

[gates.pyramid]
layers = { unit = "tests/unit/*", integration = "tests/integration/*" }
min_unit_share = 0.5
max_e2e_share = 0.2
require_layers = ["unit"]
"""

INVOICE = '''\
"""Billing context: invoices and refunds."""


class InvoiceError(ValueError):
    pass


def invoice_total(lines):
    """An invoice total equals the sum of its lines (quantity x unit price, in cents)."""
    total = 0
    for qty, unit_cents in lines:
        if qty <= 0 or unit_cents < 0:
            raise InvoiceError("a line needs a positive quantity and a non-negative price")
        total += qty * unit_cents
    return total


def refund(charge_cents, amount_cents):
    """A refund never exceeds its charge."""
    if amount_cents > charge_cents:
        raise InvoiceError("a refund cannot exceed its charge")
    return charge_cents - amount_cents
'''

UNIT_TESTS = '''\
import unittest

from billing.invoice import InvoiceError, invoice_total, refund


class InvoiceTotal(unittest.TestCase):
    def test_total_is_sum_of_lines(self):
        self.assertEqual(invoice_total([(2, 150), (1, 99)]), 399)

    def test_empty_invoice_is_zero(self):
        self.assertEqual(invoice_total([]), 0)

    def test_zero_quantity_is_refused(self):
        with self.assertRaises(InvoiceError):
            invoice_total([(0, 100)])

    def test_one_quantity_is_accepted(self):
        self.assertEqual(invoice_total([(1, 0)]), 0)

    def test_negative_price_is_refused(self):
        with self.assertRaises(InvoiceError):
            invoice_total([(1, -1)])


class Refund(unittest.TestCase):
    def test_partial_refund_leaves_the_rest(self):
        self.assertEqual(refund(500, 200), 300)

    def test_full_refund_is_allowed(self):
        self.assertEqual(refund(500, 500), 0)

    def test_refund_over_charge_is_refused(self):
        with self.assertRaises(InvoiceError):
            refund(500, 501)
'''

INTEGRATION_TESTS = '''\
import unittest

from billing.invoice import invoice_total, refund


class InvoiceThenRefund(unittest.TestCase):
    def test_refund_against_an_invoice_total(self):
        self.assertEqual(refund(invoice_total([(3, 100)]), 100), 200)
'''


def sh(cwd, *args, check=True, input=None):
    p = subprocess.run(list(args), cwd=cwd, env=ENV, capture_output=True, text=True, input=input)
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({p.returncode}): {p.stdout}{p.stderr}")
    return p


def write(root: Path, rel: str, text: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text) if text.startswith("\n") else text)


def make_repo(tmp: Path) -> Path:
    """A repo with the ledger on main, pushed to a local bare 'origin'."""
    remote = tmp / "origin.git"
    sh(tmp, "git", "init", "-q", "--bare", "-b", "main", str(remote))
    repo = tmp / "ledger"
    sh(tmp, "git", "init", "-q", "-b", "main", str(repo))
    write(repo, "harness.toml", HARNESS_TOML)
    write(repo, ".gitignore", ".ddi/\n__pycache__/\n")
    write(repo, "src/billing/__init__.py", "")
    write(repo, "src/billing/invoice.py", INVOICE)
    write(repo, "tests/__init__.py", "")
    write(repo, "tests/unit/__init__.py", "")
    write(repo, "tests/unit/test_invoice.py", UNIT_TESTS)
    write(repo, "tests/integration/__init__.py", "")
    write(repo, "tests/integration/test_flow.py", INTEGRATION_TESTS)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "Ledger: invoices and refunds in the billing context")
    sh(repo, "git", "remote", "add", "origin", str(remote))
    sh(repo, "git", "push", "-q", "-u", "origin", "main")
    return repo


def commit(repo: Path, message: str, files: dict) -> str:
    for rel, text in files.items():
        write(repo, rel, text)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", message)
    return sh(repo, "git", "rev-parse", "HEAD").stdout.strip()


def install_hook(repo: Path) -> str:
    """`ddi install` into repo, as a user would run it."""
    import sys
    env = {**ENV, "PYTHONPATH": str(HOME)}
    p = subprocess.run([sys.executable, "-m", "ddi", "install", "--repo", str(repo)], cwd=repo,
                       env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr)
    return p.stdout
