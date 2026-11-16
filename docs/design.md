# Design notes

## One loop, governed by rules the owner writes

The harness is one loop with five phases, and every phase reads the same rule catalogs
(`eep-rules/*.json`, schema `schemas/eep-rule.schema.json`):

| Phase | What happens | Rules that drive it |
|---|---|---|
| 1. Rotor + EEP slice | Read the push range, the commit messages (intent), unmerged paths and merge commits; classify scope (bounded contexts, source/tests/docs, merge, conflict) and risk; rank the rules for that scope and cut them into prompt-sized chunks (`.ddi/slice.md`) | all, ranked |
| 2. Solution check | Run the slice's machine checks: premise, blast radius, test-first, minimal diff, context crossing, unmerged paths, conflict markers | `HAB-*`, `DDD-002`, `CON-*` |
| 3. Capture | Every error event opens or updates a CAPA / conflict draft (`.ddi/capa/`) | `CAPA-*`, `CON-003` |
| 4. Gauntlet | CRAP → mutation → TDD pyramid, on an exact export of the pushed commit; stop at the first failure | `CON-004`, gate thresholds |
| 5. Feedback | The verification artifact, the drafts and the event log feed the next iteration: rules and gates that tripped before rank higher in the next slice | `CAPA-002` |

The five inner-loop habits (Scope & Premise Check, Investigate & Blast Radius, TDD & Root
Cause, Minimal Diff & Architecture, Prove With Evidence), the DDD rules, the CAPA rules and the
merge-conflict rules are one schema. There is no separate checklist: a rule with a `check` is
enforced; a rule without one shapes the slice an agent works from.

## Event-driven and error-driven, in one log

`.ddi/events.jsonl` holds both kinds of event (`schemas/ddi-event.schema.json`):

- **state** events move a run through its phases (`rotor.scoped`, `eep.sliced`,
  `gate.crap.passed`, `push.allowed`);
- **error** events (`check.failed`, `gate.mutation.blocked`, `merge.both_sides_changed`) are
  first-class signals, and each one has a draft. `CAPA-001` checks that after every run.

MTTD is the time from the start of a run to its first error event, recorded in every artifact.

## Claims, not evidence

Everything the harness writes is a claim about a local run:

- CAPA / conflict drafts: `status: draft`, `trust: unverified_claim`, `ci_provenance: null`;
- verification artifacts: `status: staged`, `trust: unverified_claim`, `ci_provenance: null`.

The schemas fix those values (`const`) and refuse any `ci_*` or `certified`/`signed`/`verified`
field. A draft on disk is re-validated before it is updated, so a hand-edited draft claiming CI
certification stops the loop (exit 2) instead of being carried forward. Promotion to evidence
happens outside the harness: CI re-runs the gates on the same commit and signs the result, and a
person decides.

## Where it sits in an agentic software factory

The same loop runs in two places, from one source pinned to a commit:

- **Standalone:** a `pre-push` hook in any repository, for a person or an agent working in a
  local shell.
- **Inside a factory's build step:** an agent that turns a ticket into a pull request runs
  `ddi run` on its branch before opening the PR. A block sends the CAPA draft back into its next
  attempt instead of opening a PR. On a pass, the verification artifact and any drafts travel
  with the PR (for example as content-addressed claim checks), still marked as claims.

In that factory the trust boundary does not move:

- the agent holds no identity on the execution side; it reaches deployment only through a pull
  request, and a person approves every merge;
- CI's own gates and signature are the evidence; the harness's artifact is the agent's claim;
- failure signals from the factory's own plumbing (dead-letter queues, failed webhooks, PR
  merge conflicts) use the same draft schema, so they arrive in the loop in the same shape.

## Level 1, local-first

Level 1 needs Python 3.11+ and git. It configures no authentication and makes no network call:
any command it runs inherits the shell's existing credentials, and the gauntlet itself needs
none. The analyzers are built in for Python (stdlib tracer for coverage, AST for complexity and
mutants). Other stacks plug in through `project.test_command`, `project.coverage_report`
(coverage.py JSON) and `gates.mutation.engine = "report"` (a Stryker `mutation.json`).

## Level 2 and Level 3, behind flags

| Seam | Flag | Contract | State |
|---|---|---|---|
| Level 2: related past CAPA records from a vectorized CAPA graph (graphed first, then vectorized into an object-store vector index) | `extensions.level2_rag_s3_capa.enabled` | `CapaContextProvider.related(scope, top_k)` | stub; enabling it without configuration is a caveat, never a block |
| Level 3: CAPA graph JSON export for an intelligence model and tuner | `extensions.level3_capa_tuner.enabled` | `schemas/capa-graph-export.schema.json` | export works; the tuner is not built |

Both are off by default, and Level 1 never imports them unless enabled.

## What it does not do

- It does not make a design good: a wrong design with simple, well-tested code passes.
- It does not answer an ambiguous requirement; it makes the agent surface it earlier.
- It is a local hook: `git push --no-verify` skips it. CI and review remain the enforcement.
- It never closes a CAPA. Capture and the first correction are automatic; the verdict is not.
