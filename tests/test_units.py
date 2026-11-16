"""Unit tests: schemas and the trust boundary, the EEP engine, CAPA drafts, the gate maths."""

import ast
import io
import json
from contextlib import redirect_stderr, redirect_stdout
import sys
import tempfile
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOME))

from ddi import capa, eep, schema  # noqa: E402
from ddi.events import EventLog  # noqa: E402
from ddi.gates import crap, mutation, pyramid  # noqa: E402
from ddi.rotor import Scope  # noqa: E402

FIX = HOME / "tests" / "fixtures"
CATALOGS = ["eep-rules/habits.json", "eep-rules/ddd.json", "eep-rules/capa.json", "eep-rules/conflict.json"]


class NegativeFixtures(unittest.TestCase):
    """NEG-EEP-01..02, NEG-CAPA-01..04, NEG-EVT-01: each must fail, for its own reason."""

    EXPECT = {"NEG-EEP-01": "kind", "NEG-EEP-02": "check", "NEG-CAPA-01": "status",
              "NEG-CAPA-02": "trust", "NEG-CAPA-03": "ci_provenance", "NEG-CAPA-04": "ci_signed_evidence",
              "NEG-EVT-01": "ci_signed_evidence"}

    def test_every_negative_fixture_fails_for_its_reason(self):
        index = json.loads((FIX / "negative/index.json").read_text())
        self.assertEqual(sorted(index), sorted(self.EXPECT))
        for name, meta in index.items():
            with self.subTest(name):
                doc = json.loads((FIX / f"negative/{name}.json").read_text())
                errs = schema.errors(doc, schema.load(meta["schema"]))
                self.assertTrue(errs, f"{name} was accepted")
                self.assertTrue(any(self.EXPECT[name] in e for e in errs), errs)

    def test_positive_fixtures_pass(self):
        for f, s in [("eep-rule.json", "eep-rule.schema.json"), ("capa-draft.json", "capa-conflict-draft.schema.json"),
                     ("event.json", "ddi-event.schema.json")]:
            with self.subTest(f):
                schema.check(json.loads((FIX / "positive" / f).read_text()), s)

    def test_a_schema_keyword_the_validator_does_not_implement_is_an_error(self):
        with self.assertRaises(schema.SchemaError):
            schema.errors({}, {"type": "object", "oneOf": []})

    def test_bool_is_not_a_number(self):
        self.assertTrue(schema.errors(True, {"type": "number"}))


class EepEngine(unittest.TestCase):
    def setUp(self):
        self.rules = eep.load_rules(HOME, CATALOGS)

    def scope(self, **kw):
        s = Scope()
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def test_catalogs_hold_fifteen_rules_across_four_kinds(self):
        self.assertEqual(len(self.rules), 15)
        self.assertEqual({r.kind for r in self.rules}, {"habit", "ddd", "capa", "conflict"})
        self.assertEqual(sorted(r.id for r in self.rules if r.kind == "habit"),
                         ["HAB-001", "HAB-002", "HAB-003", "HAB-004", "HAB-005"])

    def test_a_duplicate_rule_id_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t, "a.json").write_text((HOME / "eep-rules/habits.json").read_text())
            Path(t, "b.json").write_text((HOME / "eep-rules/habits.json").read_text())
            with self.assertRaisesRegex(ValueError, "already defined"):
                eep.load_rules(Path(t), ["a.json", "b.json"])

    def test_scope_triggers_gate_applicability(self):
        no_src = self.scope(tags=["docs"])
        ids = {r.id for r in eep.slice_rules(self.rules, no_src, 50)}
        self.assertNotIn("HAB-003", ids)
        self.assertNotIn("CON-001", ids)
        self.assertIn("HAB-003", {r.id for r in eep.slice_rules(self.rules, self.scope(tags=["source"]), 50)})

    def test_a_conflict_puts_conflict_rules_first(self):
        top = eep.slice_rules(self.rules, self.scope(tags=["source", "conflict", "merge"]), 4)
        self.assertTrue(all(r.kind == "conflict" for r in top), [r.id for r in top])

    def test_feedback_from_earlier_runs_raises_a_rule(self):
        s = self.scope(tags=["source"])
        base = [r.id for r in eep.slice_rules(self.rules, s, 50)]
        fed = [r.id for r in eep.slice_rules(self.rules, s, 50, {"HAB-004": 4})]
        self.assertLess(fed.index("HAB-004"), base.index("HAB-004"))

    def test_the_slice_is_deterministic(self):
        s = self.scope(tags=["source", "tests"], risks=["hotfix"])
        runs = {tuple(r.id for r in eep.slice_rules(self.rules, s, 12)) for _ in range(5)}
        self.assertEqual(len(runs), 1)

    def test_chunks_respect_the_limit_and_never_split_a_rule(self):
        texts = ["a" * 300, "b" * 300, "c" * 300, "d" * 900]
        chunks = eep.chunk(texts, 700)
        self.assertEqual(chunks[0], "a" * 300 + "\n\n" + "b" * 300)
        self.assertIn("d" * 900, chunks)
        self.assertEqual("".join(chunks).count("a") + "".join(chunks).count("d"), 1200)


class CapaDrafts(unittest.TestCase):
    def test_a_recurrence_updates_the_same_draft(self):
        with tempfile.TemporaryDirectory() as t:
            capa.Drafts(Path(t)).upsert(source="gate.crap", subject="src/a.py::f", kind="capa", run_id="r1")
            d = capa.Drafts(Path(t)).upsert(source="gate.crap", subject="src/a.py::f", kind="capa", run_id="r2",
                                            correction="split f")
            self.assertEqual((d["occurrences"], d["runs"], d["correction"]), (2, ["r1", "r2"], "split f"))
            self.assertEqual(len(list(Path(t, "capa").glob("*.json"))), 1)

    def test_a_second_touch_in_the_same_run_is_not_a_recurrence(self):
        with tempfile.TemporaryDirectory() as t:
            dr = capa.Drafts(Path(t))
            dr.upsert(source="CON-002", subject="x", kind="conflict", run_id="r1")
            d = dr.upsert(source="CON-002", subject="x", kind="conflict", run_id="r1")
            self.assertEqual(d["occurrences"], 1)

    def test_a_forged_draft_on_disk_is_refused_not_carried_forward(self):
        with tempfile.TemporaryDirectory() as t:
            d = capa.Drafts(Path(t)).upsert(source="gate.crap", subject="s", kind="capa", run_id="r1")
            p = Path(t, "capa", f"{d['fingerprint']}.json")
            forged = json.loads(p.read_text()) | {"status": "verified", "ci_provenance": {"run": 1}}
            p.write_text(json.dumps(forged))
            with self.assertRaises(schema.SchemaError):
                capa.Drafts(Path(t)).upsert(source="gate.crap", subject="s", kind="capa", run_id="r2")

    def test_markdown_says_it_is_a_claim(self):
        with tempfile.TemporaryDirectory() as t:
            d = capa.Drafts(Path(t)).upsert(source="CON-001", subject="a.py", kind="conflict", run_id="r1",
                                            conflicting_paths=["src/a.py"])
            md = Path(t, "capa", f"{d['fingerprint']}.md").read_text()
            self.assertIn("unverified_claim", md)
            self.assertIn("Conflict draft", md)

    def test_an_unknown_field_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(KeyError):
                capa.Drafts(Path(t)).upsert(source="x", subject="y", kind="capa", run_id="r", verdict="fixed")


class Events(unittest.TestCase):
    def test_mttd_is_the_time_to_the_first_error(self):
        ticks = iter([100.0, 100.5, 101.25, 102.0])
        with tempfile.TemporaryDirectory() as t:
            ev = EventLog(Path(t), "r1", clock=lambda: next(ticks))
            ev.state("rotor.scoped")
            ev.error("gate.crap.blocked", source="gate.crap")
            ev.error("check.failed", source="HAB-001")
            self.assertEqual(ev.first_error_t(), 1.25)

    def test_an_event_claiming_ci_certification_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(schema.SchemaError):
                EventLog(Path(t)).state("push.allowed", ci_certified=True)


class GateMaths(unittest.TestCase):
    def fn(self, src):
        return ast.parse(src).body[0]

    def test_complexity_counts_decisions(self):
        self.assertEqual(crap.complexity(self.fn("def f(x):\n    return x\n")), 1)
        f = self.fn("def f(x, y):\n    if x and y:\n        return 1\n    for i in x:\n        pass\n"
                    "    return [a for a in y if a]\n")
        self.assertEqual(crap.complexity(f), 1 + 1 + 1 + 1 + 2)

    def test_nested_functions_do_not_count_toward_their_parent(self):
        f = self.fn("def f():\n    def g(x):\n        if x:\n            return 1\n    return g\n")
        self.assertEqual(crap.complexity(f), 1)

    def test_crap_formula(self):
        self.assertEqual(crap.crap(5, 0.0), 30.0)
        self.assertEqual(crap.crap(5, 1.0), 5.0)
        self.assertEqual(crap.crap(10, 0.5), 22.5)

    def test_statement_lines_skip_the_docstring(self):
        f = self.fn('def f():\n    """doc"""\n    x = 1\n    return x\n')
        self.assertEqual(crap.statement_lines(f), {3, 4})

    def test_mutation_sites_are_ordered_and_each_mutant_differs_by_one_token(self):
        tree = ast.parse("def f(a, b):\n    return a + b if a < b else a - 1\n")
        sites = mutation.sites(tree)
        self.assertEqual([s[3] for s in sites], [s[3] for s in mutation.sites(tree)])
        descs = {s[3] for s in sites}
        self.assertTrue({"Add->Sub", "Lt->LtE", "Sub->Add", "1->2"} <= descs, descs)
        for s in sites:
            self.assertNotEqual(ast.unparse(mutation.mutate(tree, s)), ast.unparse(tree))

    def test_sampling_is_even_and_deterministic(self):
        self.assertEqual(mutation.sample(list(range(10)), 5), [0, 2, 4, 6, 8])
        self.assertEqual(mutation.sample([1, 2], 5), [1, 2])

    def test_pyramid_layers_by_glob(self):
        layers = {"unit": "tests/unit/*", "integration": "tests/integration/*"}
        self.assertEqual(pyramid.layer_of("tests/unit/test_a.py", layers), "unit")
        self.assertEqual(pyramid.layer_of("tests/test_b.py", layers), "e2e")


class SchemaBranches(unittest.TestCase):
    S = {"type": "object", "required": ["a"], "additionalProperties": False,
         "properties": {"a": {"type": "string", "minLength": 2, "pattern": "^x"},
                        "n": {"type": "integer", "minimum": 1, "maximum": 3},
                        "l": {"type": "array", "minItems": 1, "items": {"enum": [1, 2]}}}}

    def test_each_keyword_reports_its_own_error(self):
        errs = schema.errors({"a": "y", "n": 9, "l": [], "z": 1}, self.S)
        for want in ("shorter than 2", "does not match", "above 3", "fewer than 1", "$.z: not allowed"):
            self.assertTrue(any(want in e for e in errs), (want, errs))
        self.assertTrue(any("missing a" in e for e in schema.errors({}, self.S)))
        self.assertTrue(any("below 1" in e for e in schema.errors({"a": "xx", "n": 0}, self.S)))
        self.assertTrue(any("not in" in e for e in schema.errors({"a": "xx", "l": [3]}, self.S)))
        self.assertEqual(schema.errors({"a": "xx", "n": 2, "l": [1]}, self.S), [])

    def test_a_wrong_type_stops_further_checks(self):
        self.assertEqual(schema.errors(5, self.S), ["$: expected object, got int"])


class CovrunPieces(unittest.TestCase):
    def project(self, t, package):
        root = Path(t)
        (root / "tests/sub").mkdir(parents=True)
        if package:
            (root / "tests/__init__.py").write_text("")
            (root / "tests/sub/__init__.py").write_text("")
        (root / "tests/test_a.py").write_text("import unittest\nclass A(unittest.TestCase):\n    def test_a(self): pass\n")
        (root / "tests/sub/test_b.py").write_text("import unittest\nclass B(unittest.TestCase):\n    def test_b(self): pass\n")
        return root

    def test_both_layouts_find_every_test(self):
        from ddi.gates import covrun
        for package in (True, False):
            with self.subTest(package=package), tempfile.TemporaryDirectory() as t:
                root = self.project(t, package)
                sys.path.insert(0, str(root))
                try:
                    self.assertEqual(covrun.build_suite(str(root), ["tests", "missing"]).countTestCases(), 2)
                finally:
                    sys.path.remove(str(root))
                    for m in [m for m in sys.modules if m.startswith(("tests", "ddi_t_"))]:
                        del sys.modules[m]

    def test_a_test_file_that_cannot_import_is_a_failing_test(self):
        from ddi.gates import covrun
        with tempfile.TemporaryDirectory() as t:
            Path(t, "tests").mkdir()
            Path(t, "tests/test_x.py").write_text("import no_such_module_anywhere\n")
            suite = covrun.build_suite(t, ["tests"])
            res = unittest.TestResult()
            suite.run(res)
            self.assertEqual((res.testsRun, len(res.errors)), (1, 1))

    def test_result_and_tracer(self):
        from ddi.gates import covrun
        hits = {}
        tr = covrun.make_tracer(("/p/src/",), hits)

        class F:
            f_lineno = 7
            f_code = type("C", (), {"co_filename": "/p/src/m.py"})()
        self.assertIs(tr(F, "line", None), tr)            # inside the sources: keep tracing
        self.assertIs(tr(F, "call", None), tr)
        F.f_code = type("C", (), {"co_filename": "/elsewhere.py"})()
        self.assertIsNone(tr(F, "line", None))
        res = unittest.TestResult()
        res.testsRun = 0
        out = covrun.result(res, hits, "/p", "x" * 5000)
        self.assertEqual(out["lines"], {"src/m.py": [7]})
        self.assertFalse(out["ok"])                       # zero tests is not a pass
        self.assertEqual(len(out["output_tail"]), 4000)


class MutationReport(unittest.TestCase):
    def gate(self, report):
        cfg = {"gates": {"mutation": {"engine": "report", "report_path": "m.json", "min_score": 0.8}}}
        with tempfile.TemporaryDirectory() as t:
            if report is not None:
                Path(t, "m.json").write_text(json.dumps(report))
            return mutation.gate(Path(t), cfg, Scope(), Path(t))

    def mutants(self, *statuses):
        return {"files": {"a.ts": {"mutants": [{"status": s} for s in statuses]}}}

    def test_a_stryker_report_is_scored(self):
        r = self.gate(self.mutants("Killed", "Killed", "Timeout", "Killed", "Survived"))
        self.assertTrue(r.ok)
        self.assertEqual(r.metrics["score"], 0.8)
        r = self.gate(self.mutants("Killed", "NoCoverage", "Survived", "Ignored"))
        self.assertFalse(r.ok)
        self.assertEqual((r.metrics["killed"], r.metrics["survived"]), (1, 2))

    def test_a_missing_report_blocks(self):
        r = self.gate(None)
        self.assertFalse(r.ok)
        self.assertIn("does not exist", r.summary)

    def test_a_report_with_nothing_scored_passes_with_a_caveat(self):
        r = self.gate(self.mutants("Ignored", "CompileError"))
        self.assertTrue(r.ok)
        self.assertTrue(r.caveats)


class KilledSurvivors(unittest.TestCase):
    """Assertions added for the mutants that survived the harness's run on itself."""

    def test_artifacts_and_events_are_byte_stable_json(self):
        from ddi import artifact
        with tempfile.TemporaryDirectory() as t:
            nested = Path(t, "fresh", "dir")                    # parents are created
            art = {"schema": "ddi.verification_artifact.v1", "run_id": "r1", "decision": "push_allowed",
                   "status": "staged", "trust": "unverified_claim", "ci_provenance": None,
                   "range": {"base": "", "head": "abc"}, "scope": {"tags": [], "contexts": [], "risks": [],
                   "changed_lines": 0}, "eep_slice": [], "eep_chunks": 0, "solution_check": [], "gauntlet": [],
                   "capa_drafts": [], "mttd": {"seconds_to_first_error": None, "run_seconds": 0.1},
                   "caveats": [], "z_last": 1, "a_first": 2}
            j, _ = artifact.write(nested, art)
            text = j.read_text()
            self.assertEqual(text, json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n")
            self.assertEqual(Path(nested, "verification/latest.json").read_text(), text)
            ev = EventLog(Path(t), "r1", clock=lambda: 1.0)
            ev.state("rotor.scoped", zeta=1, alpha=2)
            line = (Path(t) / "events.jsonl").read_text().splitlines()[0]
            self.assertEqual(line, json.dumps(json.loads(line), sort_keys=True))

    def test_module_names_for_blast_radius(self):
        from ddi.checks import _module_of
        self.assertEqual(_module_of("src/billing/invoice.py", ["src"]), "billing.invoice")
        self.assertEqual(_module_of("src/billing/__init__.py", ["src/"]), "billing")
        self.assertIsNone(_module_of("src/billing/rates.json", ["src"]))
        self.assertIsNone(_module_of("lib/x.py", ["src"]))

    def test_test_with_source_can_be_turned_off(self):
        from ddi import checks
        cfg = {"solution_check": {"require_test_with_source": False}}
        s = Scope(source_files=["src/a.py"])
        self.assertTrue(checks.test_with_source("HAB-003", checks.Ctx(Path("."), cfg, None, s)).ok)
        cfg["solution_check"]["require_test_with_source"] = True
        self.assertFalse(checks.test_with_source("HAB-003", checks.Ctx(Path("."), cfg, None, s)).ok)

    def test_config_defaults_apply_without_a_file(self):
        from ddi import config
        with tempfile.TemporaryDirectory() as t:
            cfg = config.load(Path(t))
        p = cfg["gates"]["pyramid"]
        self.assertEqual((p["min_unit_share"], p["max_e2e_share"], p["require_layers"]), (0.6, 0.1, ["unit"]))
        self.assertEqual(cfg["gates"]["crap"]["threshold"], 30.0)
        self.assertEqual(cfg["gates"]["mutation"]["min_score"], 0.8)
        self.assertEqual(len(cfg["eep"]["catalogs"]), 4)

    def test_rule_scores_are_exact(self):
        rules = {r.id: r for r in eep.load_rules(HOME, CATALOGS)}
        hab3 = rules["HAB-003"]                                  # weight 1.0, scope trigger: source
        self.assertEqual(eep.score(hab3, Scope(tags=["docs"])), 1.0)
        self.assertEqual(eep.score(hab3, Scope(tags=["source"])), 2.0)
        self.assertEqual(eep.score(hab3, Scope(tags=["source"]), {"HAB-003": 2}), 2.5)
        self.assertEqual(eep.score(hab3, Scope(tags=["source"]), {"HAB-003": 100}), 3.0)   # capped at +1
        self.assertEqual(eep.score(hab3, Scope(tags=["source"], risks=["a", "b"])), 2.5)
        self.assertEqual(eep.score(rules["CON-002"], Scope(tags=["conflict"])), 3.0)

    def test_crap_rounds_to_two_places(self):
        self.assertEqual(crap.crap(7, 1 / 3), 21.52)

    def test_a_crashing_runner_is_reported_as_text_with_honest_timing(self):
        with tempfile.TemporaryDirectory() as t:
            fake = Path(t, "crash.py")
            fake.write_text("import sys\nsys.stderr.write('runner exploded')\nsys.exit(3)\n")
            cfg = {"project": {"test_command": "", "test_roots": ["tests"], "source_roots": ["src"]}}
            old, crap.COVRUN = crap.COVRUN, fake
            try:
                res = crap.run_suite(Path(t), cfg, Path(t, "missing.json"))
            finally:
                crap.COVRUN = old
        self.assertFalse(res["ok"])
        self.assertEqual(res["output_tail"], "runner exploded")
        self.assertEqual(res["command"]["exit"], 3)
        self.assertLess(res["command"]["seconds"], 30)

    def test_pyramid_counts_and_shares_are_exact(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for rel, n in [("tests/unit/test_a.py", 3), ("tests/integration/test_b.py", 1)]:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("".join(f"def test_{i}():\n    pass\n" for i in range(n)))
            cfg = {"project": {"test_roots": ["tests"]},
                   "gates": {"pyramid": {"layers": {"unit": "tests/unit/*", "integration": "tests/integration/*"},
                                         "min_unit_share": 0.5, "max_e2e_share": 0.0, "require_layers": ["unit"]}}}
            r = pyramid.gate(root, cfg, Scope(), root)
        self.assertEqual(r.metrics["counts"], {"unit": 3, "integration": 1, "e2e": 0})
        self.assertEqual(r.metrics["share"], {"unit": 0.75, "integration": 0.25, "e2e": 0.0})
        self.assertTrue(r.ok)


class FullMutationMerge(unittest.TestCase):
    """The full-population run: shards partition the plan, and merge refuses partial counts."""

    def plan(self, n):
        return [(f"f{k % 3}.py", None, (k, "op", None, "Add->Sub", k + 1)) for k in range(n)]

    def shard(self, k, of=3, plan_id="p1", total=10, run=None, killed=0, survived=None, survivors=()):
        run = run if run is not None else len(range(k, total, of))
        survived = run - killed if survived is None else survived
        return {"plan_id": plan_id, "of": of, "shard": k, "total": total, "run": run, "killed": killed,
                "survived": survived, "survivors": list(survivors)}

    def test_shards_are_disjoint_and_cover_the_plan(self):
        from ddi.mutation_full import shard_of
        plan = self.plan(10)
        parts = [shard_of(plan, k, 3) for k in range(3)]
        self.assertEqual([len(p) for p in parts], [4, 3, 3])
        flat = [m for p in parts for m in p]
        self.assertEqual(sorted(m[2][0] for m in flat), list(range(10)))
        with self.assertRaises(ValueError):
            shard_of(plan, 3, 3)

    def test_plan_id_depends_on_every_mutant(self):
        from ddi.mutation_full import plan_id
        a, b = self.plan(5), self.plan(5)
        self.assertEqual(plan_id(a), plan_id(b))
        b[4] = ("f1.py", None, (4, "op", None, "Sub->Add", 5))
        self.assertNotEqual(plan_id(a), plan_id(b))

    def test_merge_counts_everything_once(self):
        from ddi.mutation_full import merge, summary
        surv = {"file": "f1.py", "line": 2, "mutation": "Add->Sub"}
        full = merge([self.shard(0, killed=4), self.shard(1, killed=2, survivors=[surv]), self.shard(2, killed=3)])
        self.assertEqual((full["total"], full["killed"], full["survived"], full["score"]), (10, 9, 1, 0.9))
        self.assertIn("`f1.py` (1): line 2 Add->Sub", summary(full))

    def test_merge_refuses_anything_but_one_complete_plan(self):
        from ddi.mutation_full import merge
        ok = [self.shard(k) for k in range(3)]
        for bad, why in [(ok[:2], "exactly once"), (ok + [self.shard(2)], "exactly once"),
                         ([ok[0], ok[1], self.shard(2, plan_id="p2")], "different plans"),
                         ([ok[0], ok[1], self.shard(2, run=2, survived=2)], "ran 9 mutants"),
                         ([], "no shards")]:
            with self.subTest(why), self.assertRaisesRegex(ValueError, why):
                merge(bad)


class MutationGateDetails(unittest.TestCase):
    """Survivors of the full-mutation change: the gate's arithmetic, limits and records."""

    def gate_with(self, killed, survived, n_survivors=0, files=("src/a.py",)):
        survivors = [{"file": "src/a.py", "line": i, "mutation": "Add->Sub"} for i in range(n_survivors)]
        cfg = {"project": {"test_roots": ["tests"], "source_roots": ["src"], "test_command": ""},
               "gates": {"mutation": {"engine": "builtin", "min_score": 0.8, "max_mutants": 100, "timeout_s": 5}}}
        ticks = iter([10.0, 11.23456])
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "src").mkdir()
            (Path(t) / "src/a.py").write_text("def f(x):\n    return x + 1\n")
            (Path(t) / "src/rates.json").write_text("{}")
            old_run, old_time = mutation.run_mutants, mutation.time.time
            mutation.run_mutants = lambda *a: (killed, survived, survivors, 0.5)
            mutation.time.time = lambda: next(ticks)
            try:
                return mutation.gate(Path(t), cfg, Scope(source_files=list(files)), Path(t))
            finally:
                mutation.run_mutants, mutation.time.time = old_run, old_time

    def test_score_rounding_cap_and_record(self):
        r = self.gate_with(2, 1, n_survivors=30)
        self.assertEqual(r.metrics["score"], 0.667)
        self.assertEqual(len(r.metrics["survivors"]), 25)
        self.assertEqual(r.commands[0]["exit"], 0)
        self.assertEqual(r.seconds, 1.23)
        self.assertFalse(r.ok)
        self.assertIn("30 mutant(s) survived", r.findings[0]["message"])

    def test_exactly_the_minimum_passes(self):
        r = self.gate_with(4, 1, n_survivors=1)
        self.assertEqual(r.metrics["score"], 0.8)
        self.assertTrue(r.ok)
        self.assertEqual(r.findings, [])

    def test_only_existing_python_files_are_mutated(self):
        r = self.gate_with(1, 0, files=("src/a.py", "src/rates.json", "src/deleted.py"))
        self.assertEqual(r.metrics["files"], ["src/a.py"])

    def test_a_boolean_flips_and_a_number_moves(self):
        tree = ast.parse("X = True\nY = 1\n")
        by = {s[3]: s for s in mutation.sites(tree)}
        self.assertEqual(ast.unparse(mutation.mutate(tree, by["True->False"])), "X = False\nY = 1")
        self.assertEqual(ast.unparse(mutation.mutate(tree, by["1->2"])), "X = True\nY = 2")


class FullMutationCli(unittest.TestCase):
    def test_shard_range_error_names_the_range(self):
        from ddi.mutation_full import shard_of
        with self.assertRaisesRegex(ValueError, r"^shard 3 is not in 0\.\.2$"):
            shard_of([], 3, 3)

    def test_merge_refuses_shards_that_disagree_on_the_plan_size(self):
        from ddi.mutation_full import merge
        a = {"plan_id": "p", "of": 2, "shard": 0, "total": 4, "run": 2, "killed": 2, "survived": 0, "survivors": []}
        with self.assertRaisesRegex(ValueError, "disagree on the plan size"):
            merge([a, a | {"shard": 1, "total": 5}])

    def test_arguments_are_required_and_defaulted(self):
        from ddi import mutation_full
        for argv in ([], ["merge", "x.json"], ["run"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    mutation_full.main(argv)
        seen = {}
        old = mutation_full.run
        mutation_full.run = lambda root, shard, of, timeout: seen.update(timeout=timeout, shard=shard, of=of) or \
            {"run": 0, "total": 0, "killed": 0, "survived": 0, "seconds": 0}
        try:
            with tempfile.TemporaryDirectory() as t, redirect_stdout(io.StringIO()):
                mutation_full.main(["run", "--out", str(Path(t, "s.json"))])
        finally:
            mutation_full.run = old
        self.assertEqual(seen, {"timeout": 180.0, "shard": 0, "of": 1})

    def test_merge_cli_writes_json_and_summary_or_refuses(self):
        from ddi import mutation_full
        shard = {"plan_id": "p", "of": 1, "shard": 0, "total": 2, "run": 2, "killed": 2, "survived": 0, "survivors": []}
        with tempfile.TemporaryDirectory() as t:
            Path(t, "s0.json").write_text(json.dumps(shard))
            with redirect_stdout(io.StringIO()):
                code = mutation_full.main(["merge", str(Path(t, "s0.json")), "--out", str(Path(t, "f.json")),
                                           "--summary", str(Path(t, "f.md"))])
            self.assertEqual(code, 0)
            text = Path(t, "f.json").read_text()
            self.assertTrue(text.endswith("}\n"))
            self.assertEqual(json.loads(text)["score"], 1.0)
            self.assertIn("killed: **2**", Path(t, "f.md").read_text())
            Path(t, "s0.json").write_text(json.dumps(shard | {"run": 1}))
            with redirect_stderr(io.StringIO()):
                self.assertEqual(mutation_full.main(["merge", str(Path(t, "s0.json")), "--out", str(Path(t, "g.json"))]), 2)


if __name__ == "__main__":
    unittest.main()
