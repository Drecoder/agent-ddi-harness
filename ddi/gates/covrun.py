"""Run a project's unittest suite and record line coverage, with the standard library only.

    python -I covrun.py ROOT OUT.json --tests tests --sources src [--no-trace]

Writes {"ran", "failures", "errors", "ok", "failed_tests", "output_tail", "lines": {relpath:
[line, ...]}}. Exit 0 when the suite passed, 1 when it did not. Run as a subprocess so the
project's imports never touch the harness process. main() only wires the pieces below, which
are tested in-process; main itself installs a tracer, which an in-process test cannot nest.
"""

import argparse
import importlib.util
import io
import json
import os
import sys
import threading
import unittest


def _import_failed(name, err):
    def import_failed():
        raise ImportError(f"{name}: {err!r}")
    return unittest.FunctionTestCase(import_failed, description=f"import {name}")


def _load_file(loader, root, path):
    """One test file, by path, with its own directory importable for its siblings."""
    d = os.path.dirname(path)
    if d not in sys.path:
        sys.path.insert(0, d)
    name = "ddi_t_" + os.path.relpath(path, root)[:-3].replace(os.sep, "_").replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001 — an import error is a failing test, not a missing one
        return _import_failed(name, e)
    return loader.loadTestsFromModule(mod)


def build_suite(root, test_roots, loader=None):
    """A package test root is discovered from the project root (tests import as tests.x). A test
    root that is not a package would not be recursed by discovery, so its files load by path."""
    loader = loader or unittest.TestLoader()
    suite = unittest.TestSuite()
    for t in test_roots:
        d = os.path.join(root, t)
        if not os.path.isdir(d):
            continue
        if os.path.exists(os.path.join(d, "__init__.py")):
            suite.addTests(loader.discover(d, pattern="test*.py", top_level_dir=root))
            continue
        for dirpath, dirnames, filenames in os.walk(d):
            dirnames[:] = sorted(x for x in dirnames if x != "__pycache__")
            for fn in sorted(f for f in filenames if f.startswith("test") and f.endswith(".py")):
                suite.addTests([_load_file(loader, root, os.path.join(dirpath, fn))])
    return suite


def make_tracer(prefixes, hits):
    def tracer(frame, event, arg):
        fn = frame.f_code.co_filename
        if not fn.startswith(prefixes):
            return None
        if event in ("line", "call"):
            hits.setdefault(fn, set()).add(frame.f_lineno)
        return tracer
    return tracer


def result(res, hits, root, output):
    return {"ran": res.testsRun, "failures": len(res.failures), "errors": len(res.errors),
            "ok": res.wasSuccessful() and res.testsRun > 0,
            "failed_tests": [str(t) for t, _ in res.failures + res.errors][:50],
            "output_tail": output[-4000:],
            "lines": {os.path.relpath(f, root).replace(os.sep, "/"): sorted(v) for f, v in sorted(hits.items())}}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("out")
    ap.add_argument("--tests", nargs="+", required=True)
    ap.add_argument("--sources", nargs="+", required=True)
    ap.add_argument("--no-trace", action="store_true")
    a = ap.parse_args(argv)
    root = os.path.realpath(a.root)
    src_dirs = [os.path.join(root, s) for s in a.sources]
    sys.path[:0] = src_dirs + [root]
    os.chdir(root)
    suite = build_suite(root, a.tests)
    hits, stream = {}, io.StringIO()
    tracer = None if a.no_trace else make_tracer(tuple(os.path.join(d, "") for d in src_dirs), hits)
    threading.settrace(tracer)
    sys.settrace(tracer)
    try:
        res = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    finally:
        sys.settrace(None)
        threading.settrace(None)
    out = result(res, hits, root, stream.getvalue())
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
