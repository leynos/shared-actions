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
write the baseline. The group never cancels a running publisher, because a
cancelled publisher abandons its upload and its baseline write together. It is
not a queue either: GitHub keeps one pending run per group, a newer push
replaces it, and the newest push's baseline wins.

The uploader's own contract splits along the same line rather than leaving the
pull-request lane. `test-upload-codescene-coverage.yml` keeps everything that
contacts nothing and stays on every pull request: the cold-runner proof, the
`install` mode, the offline version assertion, the fixture check and the
installer's refusals. `test-codescene-parser-proof.yml` takes the one call that
reads the project configuration, `cs-coverage check`, which is what the second
outage broke. It is dispatch-only and its job runs only from `refs/heads/main`:
the trigger alone does not bound which ref's content runs, because a dispatch
selects its own ref, and that ref's workflow file would execute with the
repository secret, so any write-access account could otherwise read
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
the pinned CLI version still gets the cold-runner install proof, the offline
version assertion, the fixture check and the installer's refusals. What it no
longer gets is the parse of those fixtures against the project configuration,
because that call is the service contact. Run `test-codescene-parser-proof.yml`
by hand from `main` when any of those change.

Dispatch-only for the whole workflow was considered first and rejected: it
takes a proof off the lane that costs nothing to run there. The split needs two
files rather than one guarded job, because a static contract that had to reason
about a job guard to decide whether a file holds the credential is the weaker
boundary; two files keep the rule a property of the file.

One workflow advances the baseline, and it serves no pull request.
`publish-baseline` defaults to `auto`, which saves on a push to `main` whatever
started the run, so a pull-request lane that also runs on such a push is a
second writer and races the publisher. The contract rejects that shape.

No workflow a pull request can reach may name `codescene.io`, hold
`CS_ACCESS_TOKEN`, run `cs-coverage check` or `cs-coverage upload`, or use the
CodeScene action in any mode but `install`. The host clause is the one that
matters most: a step can reach the project API with a plain `curl` naming none
of the others, and every remaining assertion would still pass. The host
comparison is case-insensitive because a DNS name is, while the credential is
compared exactly because an environment variable name is case-sensitive; the
fold lives in the reader, so removing it fails a test rather than passing at
every call site.

The scan reads the parse rather than the file text. A comment contacts nothing,
and explaining in prose why a lane must not name the credential made the lane
name it. Walking the parse keeps the `run:` bodies, which is the hiding place
that mattered, and drops what GitHub itself drops.

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
