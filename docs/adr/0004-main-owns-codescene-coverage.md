# ADR 0004: main owns CodeScene coverage

**Status:** Accepted **Date:** 2026-09-21

## Context

This repository's pull-request lane generated coverage and then handed it to
`cs-coverage check`, with `CS_ACCESS_TOKEN` and a `fetch-depth: 0` checkout so
the CLI could reach the pull request's merge base. A second workflow,
`coverage-main.yml`, uploaded coverage after every push to `main`. A third,
`test-upload-codescene-coverage.yml`, proved on every pull request that a cold
runner can install the pinned CLI and that the pinned version parses
Slipcover's cobertura reports, which also needed the token.

Three of the repository's checks therefore depended on CodeScene being
reachable and correctly configured, on a code path that runs for every
contributor on every pull request. Twice in four days that dependency failed in
ways no branch caused. On 2026-09-16 an unpinned CLI version broke cobertura
parsing across the estate. On 2026-09-21 `cs-coverage check` began returning
"HTTP call succeeded, but the received project-config isn't valid. Lacks the
gates configuration." against project 72004, with no change on either side; a
re-run of each completed job reproduced it, so it was persistent server-side
state. Both the `coverage` and `cold-runner-contract` checks were red on every
pull request for the duration.

Neither failure told a contributor anything about their change, and neither was
actionable from a branch.

## Decision

No workflow a pull request can start contacts CodeScene.

The pull-request `coverage` job in `ci.yml` generates coverage and compares it
with the ratchet baseline, passing `with-ratchet: 'true'` and
`publish-artefact: 'false'`. It holds no credential, invokes no CodeScene
action, runs no `cs-coverage` command, and no longer needs full Git history.

`coverage-main.yml` is the single publisher. It runs on pushes to `main` and on
`workflow_dispatch`, regenerates the same coverage, saves the next ratchet
baseline, and uploads to CodeScene. Its upload step is guarded on
`github.ref == 'refs/heads/main'` as well as on the credential, because a
dispatch run selects its own ref and the push filter says nothing about it. The
workflow carries a `concurrency` group so two overlapping pushes cannot race to
write the baseline.

`test-upload-codescene-coverage.yml` becomes `workflow_dispatch` only, and its
job runs only from `refs/heads/main`. Its parser proof is the one place this
repository still calls `cs-coverage check`, and that call is what the second
outage broke. The trigger alone does not bound which ref's content runs: a
dispatch selects its own ref, and that ref's workflow file would execute with
the repository secret, so any write-access account could otherwise read
`CS_ACCESS_TOKEN` out of a branch it controls.

This is concordat rule CV-005, main-owned-codescene-coverage, adopted here on
the repository that supplies the actions the rule is written against.

## Consequences

A CodeScene outage no longer reddens a pull request. The ratchet is what a pull
request is measured against, and its baseline is written by the trunk push, so
the comparison is local to the runner and available whether or not CodeScene is
up.

Coverage still reaches CodeScene, once per commit on `main`, from a workflow
whose secret context is fixed at dispatch or push.

A pull request that changes `upload-codescene-coverage`, its CLI manifest, or
the pinned CLI version no longer gets the cold-runner install and parse proof
automatically. Run that workflow by hand when any of those change. The action's
offline behaviour stays covered by its own unit tests, which `ci.yml` runs on
every pull request. The alternative considered was splitting the workflow into
a tokenless pull-request job and a dispatch-only parser job; it was rejected
because it leaves a pull-request-startable file naming the credential, which a
static contract can only permit by understanding a job guard, and a guard a
contract has to reason about is the weaker boundary.

Both lanes name the same ratchet baseline path. Scoping the publisher to
`workflow_scripts` narrows the measured population, so the percentages in the
baseline earlier unscoped main runs wrote are not comparable with these; the
baseline moves to `.coverage-baseline.workflow-scripts.python`, which the
action seeds at zero when the restored cache does not carry it. The first
scoped run therefore sets the generation rather than failing the ratchet
against an incompatible number.

The ratchet baseline is keyed by `runner.os`. Any platform lane that arms the
ratchet on pull requests must also run on the trunk push, or its baseline is
never written and the pull-request comparison reads an empty file. This
repository generates coverage on Linux only, on both sides.

## Implementation Notes

`tests/workflows/test_main_owned_coverage.py` enforces this decision. It
enumerates `.github/workflows/*.yml` rather than naming files, so a workflow
added later is covered the day it appears, and it follows job-level `uses:`
into local reusable workflows, so a CodeScene call one file away from a
pull-request trigger is still inside the boundary.

Two readings are load-bearing and are driven directly on documents chosen
rather than found. PyYAML resolves an unquoted `on:` key to the boolean `True`,
so a reader consulting only the string key resolves no triggers anywhere and
every boundary drawn from it passes over an empty set; the contract reads both
keys. And the publisher predicate is "pushes to `main` **and** serves no pull
request", because a repository's `ci.yml` commonly declares both triggers and
the looser reading would make one file simultaneously required to upload and
forbidden from uploading.

Each quantified rule is paired with a presence half, since "every lane
ratchets" is satisfied by having no lanes at all. The reachability walk carries
property tests over generated workflow graphs, including cycles, which this
repository's own shallow forest cannot exercise.

`pytest.ini` names `tests/workflows` as a directory. It previously listed
individual files, and this contract was added without being listed, so the gate
reported a clean suite while the contract below it was collected by nothing.

## References

- Concordat rule CV-005, `main-owned-codescene-coverage` (concordat `#176`)
- PR `#503`; `#504` pinned the CLI at 1.0.101 through a manifest
- `docs/developers-guide.md`, "This repository's coverage publication"
