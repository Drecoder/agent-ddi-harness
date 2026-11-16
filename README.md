# agent-ddi-harness

A local-first **pre-push harness for agents and CLI workflows**: one loop, governed by rules you
write, that verifies a change before it leaves the machine and turns every failure into a
structured CAPA draft instead of a log someone has to copy.

It is a reference implementation of **Domain-Driven Intelligence (DDI)**: domain-driven design,
test-driven development, and event-, error- and evidence-driven development applied to how an
intelligent agent scopes, executes, verifies and learns from engineering work. The rule it
enforces: *intelligence works inside the domain's boundaries; it does not get to redefine them.*

Level 1 is complete and needs only **Python 3.11+ and git**. No cloud, no API keys, no auth
setup, no network calls, no dependencies.

```text
git push
  └─ pre-push hook
       1. Rotor + EEP slice    scope the push, rank the rules for it
       2. Solution check       premise, blast radius, test-first, minimal diff, bounded
                               contexts, unmerged paths, conflict markers
       3. Capture              every error event opens or updates a CAPA / conflict draft
       4. Gauntlet             CRAP → mutation → TDD pyramid, on the exact pushed commit
       5. Feedback             artifact + drafts + events feed the next attempt
  └─ push allowed, or blocked with the diagnosis and the draft
```

## Quick start

```sh
git clone https://github.com/Drecoder/agent-ddi-harness
cd your-project
cp ../agent-ddi-harness/examples/harness.example.toml harness.toml   # then edit it
PYTHONPATH=../agent-ddi-harness python -m ddi install --repo .          # writes .git/hooks/pre-push
git push                                                               # the loop runs here
```

Run the loop without pushing, or see the rules an agent should work from for the current push:

```sh
PYTHONPATH=/path/to/agent-ddi-harness python -m ddi run --repo .
PYTHONPATH=/path/to/agent-ddi-harness python -m ddi slice --repo .
```

Everything it writes goes to `.ddi/` in your repository (git-ignored):

| Path | What |
|---|---|
| `.ddi/verification/latest.md` / `.json` | the verification artifact: scope, slice, checks, each gate with the command it ran, exit code and time, MTTD, caveats |
| `.ddi/capa/<fingerprint>.md` / `.json` | one CAPA or conflict draft per failure fingerprint; a recurrence updates it and counts |
| `.ddi/events.jsonl` | every state and error event, across runs |
| `.ddi/slice.md` | the ranked EEP rules for this push, in prompt-sized chunks |

## The rules

Rules live in `eep-rules/*.json`, one schema for all of them (`schemas/eep-rule.schema.json`).
A rule with a `check` is enforced; a rule without one shapes the slice an agent reads.

| Kind | Rules |
|---|---|
| Inner-loop habits | `HAB-001` Scope & Premise Check · `HAB-002` Investigate & Blast Radius · `HAB-003` TDD & Root Cause · `HAB-004` Minimal Diff & Architecture · `HAB-005` Prove With Evidence |
| DDD | `DDD-001` ubiquitous language · `DDD-002` declare a cross-context change · `DDD-003` hold the context's invariants |
| CAPA | `CAPA-001` every block is a CAPA signal · `CAPA-002` one draft per fingerprint · `CAPA-003` a draft is a claim |
| Merge conflicts | `CON-001` no unmerged paths · `CON-002` no conflict markers · `CON-003` a resolution records both sides · `CON-004` re-prove after resolving |

Bounded contexts, their terms and invariants, and every threshold are in `harness.toml`.
Override a catalog by vendoring it at the same path in your repository.

## The gauntlet

| Gate | Python, built in | Other stacks |
|---|---|---|
| CRAP | `c² × (1 − cov)³ + c` per function in changed files; complexity from the AST, coverage from a stdlib tracer | `project.test_command` + `project.coverage_report` (coverage.py JSON) |
| Mutation | one-token AST mutants of changed files, run against the suite in a scratch copy, sampled evenly up to `max_mutants` | `engine = "report"` reads a Stryker `mutation.json` |
| TDD pyramid | tests counted per layer by path glob; required layers, unit share, e2e share, suite passes | same |

The gates run in that order on an exact `git archive` of the pushed commit, and stop at the
first failure. Uncommitted changes are never tested; the artifact says so when they exist.

## Claims, not evidence

Everything the harness writes is a claim about a local run. Drafts are `status: draft`, artifacts
are `status: staged`; both carry `trust: unverified_claim` and `ci_provenance: null`. The schemas
fix those values and reject any `ci_*`, `certified`, `signed` or `verified` field, and a draft on
disk is re-validated before it is updated: a hand-edited draft claiming CI certification stops
the loop (exit 2). Evidence comes from CI re-running the gates on the same commit, and a person
promotes a draft. The harness captures failures and drives the first correction automatically;
it never closes a CAPA.

## Demos and benchmarks

All local, all free: the "remote" is a bare repository in a temp directory, and every demo is a
real `git push` through the installed hook.

```sh
python examples/demos.py        # add -v for the hook output, --keep to inspect the repos
python -m unittest tests.test_units tests.test_loop
```

Measured on a Linux container, Python 3.13 (your numbers will differ):

| Demo | Shows | Result | MTTD | Wall |
|---|---|---|---|---|
| clean | scoped, tested change → all three gates pass → artifact | push allowed | none | 0.86 s |
| tripped | a weak test lets 9 of 9 mutants survive → push blocked, CAPA draft; a retry updates the same draft (2 occurrences) and the branch never reaches the remote | push blocked | 0.58 s | 1.57 s |
| conflict | two branches change the refund invariant differently. The half-resolved push (conflict markers left in) is blocked (`CON-002`); the completed resolution is allowed, and its conflict draft identifies the paths both sides changed (`CON-003`) | blocked → allowed | 0.03 s | 1.25 s |

Tests: 66 (45 unit, 21 integration), including one designed-to-fail case per enforced rule and
gate, and the negative fixtures `NEG-EEP-01..02`, `NEG-CAPA-01..04` and `NEG-EVT-01`
(`tests/fixtures/negative/`): an unknown rule kind, an unknown check, and drafts or events
forging `status: verified`, `trust: ci_certified`, CI provenance or a `ci_signed_evidence` field.
Each is rejected for its stated reason.

**The harness gates its own pushes** (`harness.toml` at the root). Its first run on itself
blocked twice, both real: an undeclared bounded-context crossing in the commit, and a coverage
bug that ran zero tests for projects whose `tests/` is not a package. It then found five CRAP
violations and 20 surviving mutants in its own code, each fixed with code or a test.

### Three mutation results, three different populations

They answer different questions and are not added together:

| Result | Population | What it shows |
|---|---|---|
| **Targeted replay: 17 of 17 killed** | the 20 mutants that survived the first self-run; 17 still applied after the fixes and were re-applied one by one against the new suite; the other 3 were in code removed as dead or unneeded | those specific weaknesses are closed. It says nothing about any other mutant |
| **Sampled gate run: 8 survived of 40 (80%)** | an independent, evenly spaced sample of 40 of the 528 mutants of the changed code, drawn by the pre-push gate on the published self-run (commit `67d5500`; the code had changed, so the sample differs from the first run's) | an estimate for the whole population, at exactly the 80% bar. Its 8 survivors are listed in that run's artifact |
| **Full population: every mutant, no sampling** | all mutants of every source file, in CI's `mutation` job (four disjoint shards, merged; the merge refuses a partial count) | the complete survivor count. **Pending its first run in the pinned image**; this table is updated from that run |

The published self-run at `67d5500`: CRAP worst 30.0 (threshold 30), mutation 80% on the 40-of-528
sample (minimum 80%), pyramid 41 unit / 20 integration, push allowed in 3 m 54 s, on host
Python 3.13.

## CI: the same checks, reproducibly

The pre-push hook is local and can be skipped; `.github/workflows/ci.yml` is the check that
cannot. Every job runs in one image: `python:3.12-slim` pinned by digest, plus `git` pinned to an
exact Debian version (`ci/Dockerfile`), as a non-root user with no network.

| Job | When | What |
|---|---|---|
| `tests` | every push and pull request | the full suite, the three demos, and every negative fixture rejected for its stated reason |
| `mutation` | daily, on demand, or on a pull request labelled `full-mutation` | every mutant of every source file in four shards, merged into one complete count (job summary and a `mutation-full` artifact) |

`ci/image.sh` checks before every build that the git pin is still the base image's current
Debian candidate; when a security update ships a newer git, CI fails with the version to repin
to, rather than drifting.

## Roadmap: Levels 2 and 3

| Level | What | Flag | State |
|---|---|---|---|
| 1 | Local pre-push harness | always on | complete |
| 2 | Related past CAPA records from a vectorized CAPA graph (graphed first, then vectorized into an object-store vector index), fed into the slice | `extensions.level2_rag_s3_capa.enabled` | contract and stub; enabling it unconfigured is a caveat, never a block |
| 3 | CAPA graph JSON export for an intelligence model and tuner that proposes rule and threshold changes | `extensions.level3_capa_tuner.enabled` | export works and is schema-checked; the tuner is not built |

Both are off by default; Level 1 never imports them unless enabled, and the harness never
configures credentials: anything a future extension calls inherits the shell's.

## Known limits

- It makes work checkable, not good: a wrong design with simple, well-tested code passes.
- Built-in analyzers are Python-only; other stacks bring their own coverage and mutation reports.
- The pre-push gate samples mutants (`max_mutants`) to stay fast; the complete count comes from
  CI's full run. The 8 survivors of the published sample are the next tests to write.
- It is a local hook: `git push --no-verify` skips it. CI and review remain the enforcement.

See [`docs/design.md`](docs/design.md) for the design, including where the loop sits inside an
agentic software factory's build step.

## License

MIT
