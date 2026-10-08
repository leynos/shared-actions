# ExecPlan: validate and document the cargo-nextest 0.9.145 pin

Status: COMPLETE

Branch: `bump-nextest` (worktree `f0322c10-01e4-47d3-991d-1aef0209922e`). PR:
leynos/shared-actions `#509`, squash-merged to `main` as `37a39836` on
2026-09-20 with `reviewDecision=APPROVED` and all 40 checks green.

## Big picture

PR `#509` moved the `generate-coverage` action's pinned `cargo-nextest` release
from 0.9.120 to 0.9.145 to stop the
`warning: could not find build script output file at …/build/<hash>/output`
flood that a workspace package with a build script produced under Cargo's new
build-directory layout.

The pin, both checksum tables, and the changelog entry were committed first.
This plan then addressed three findings, all raised by CodeRabbit and none
requiring a change to action behaviour. All three are now resolved:

1. **Testing (unit and behavioural)** — the installer has no end-to-end test
   that validates the *real* pinned release. The existing end-to-end test
   builds a synthetic archive in `tmp_path` and derives its digests during the
   test, so it can never disagree with the script.
2. **User-facing documentation** — `docs/users-guide.md` does not state the
   pinned version nor why 0.9.145 was chosen.
3. **Developer documentation** — `docs/developers-guide.md` does not state the
   pin, that both checksum tables were refreshed, or what a future bump must
   update.

Constraints that shaped the design:

- **Scope**: `.github/actions/generate-coverage/tests/`, `.github/workflows/`
  only as required to run the new test in CI, `docs/users-guide.md`,
  `docs/developers-guide.md`. No behaviour change beyond the pinned release.
- **The new validation must not take the pin's word for it.** Every expected
  value (release URL, archive digest, extracted-executable digest, reported
  version) must be an independent literal, never a reference to
  `CARGO_NEXTEST_VERSION`, `CARGO_NEXTEST_RELEASE_ASSETS`, or
  `CARGO_NEXTEST_SHA256`.
- **`make test` must stay hermetic.** `uv run pytest` runs with no network
  sandbox and with `-n auto`; a test that downloads cannot live there.
- Repo rule: sub-agents never run repository gates; `scrutineer` is the
  exclusive gate-runner and runs them sequentially.

## Evidence gathered

### The pinned linux-x86_64-gnu release asset (0.9.145)

| Artefact                 | Value                                                                                                                                 |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| Archive URL              | `https://github.com/nextest-rs/nextest/releases/download/cargo-nextest-0.9.145/cargo-nextest-0.9.145-x86_64-unknown-linux-gnu.tar.gz` |
| Archive bytes            | 12,050,698                                                                                                                            |
| Archive SHA-256          | `32aa82416099eb12fffae9cf1a279ad201fecbd3f74826c613e32e9006b29867`                                                                    |
| Executable SHA-256       | `c4d4f4ad7eb50677b568aae324251e8bd6978e9fc6fe97aa1347a2afdd95e59c`                                                                    |
| Published checksum asset | `cargo-nextest-0.9.145-x86_64-unknown-linux-gnu.sha256` (120 bytes)                                                                   |

The archive digest was taken from the release's own published `.sha256` asset,
not from a self-computed value. Note the release publishes `<asset>.sha256`,
**not** `<asset>.tar.gz.sha256`; a 404 on the latter is not evidence the
checksum file is missing.

### Harness facts discovered

- `pytest.ini` `testpaths` already includes `.github/actions`, so a new test
  module under `.github/actions/generate-coverage/tests/` is collected by both
  `uv run pytest` (CI) and `make test` with no workflow change.
- The repo already carries large immutable fixtures
  (`.github/actions/upload-codescene-coverage/tests/fixtures/*.xml`, 1.1 MB),
  and `.gitattributes` marks those fixtures `-text` for integrity. So a
  checked-in fixture is an established pattern here.
- The lazy-module-import trick for hand-maintained constants already exists:
  `tests/workflows/test_coverage_lane_reading.py` loads
  `workflow_scripts/dependabot_metrics.py` by `importlib` path solely to hold
  its constants to expected literals. The same move lets the independent
  expectations and the installer's constants be compared in the one test whose
  job is that comparison, while the end-to-end test compares its expectations
  only against the real artefact.
- No test enumerates `.github/workflows/`, and adding a job to `ci.yml`
  breaks nothing.
- A new `ci.yml` job would be `disabled_manually` on a `pull_request` event by
  branch protection, and cannot be added to the branch-protection required list
  without a repository-settings change. A `scheduled` event shifts the blocking
  failure from the pull request to a nightly run and later to unrelated pull
  requests. Git history shows every such event doing exactly that.

### The `python-tests (macos-15)` failure

`test_run_rust_cranelift_project_uses_llvm_codegen_env` failed at
`test_scripts.py:284` with `assert 1 == 0`, from a cmd-mox IPC
`json.decoder.JSONDecodeError` plus `BrokenPipeError` in the stub server. The
same head SHA `c7318313` **passed** at run 35437609951 and failed at run
35444806468, which is direct evidence of nondeterminism rather than a
branch-caused break. Investigated by `wyvern` for corroboration across other
branches.

## Approach

### Validation test

One new module,
`.github/actions/generate-coverage/tests/test_install_cargo_nextest_release.py`,
holding independent literal expectations.

**Fixture decision (settled).** Validation item 4 requires *successful archive
extraction* and the *extracted executable's digest*, and the executable's
version output must identify `cargo-nextest 0.9.145`. A 120-byte checksum file
cannot exercise any of those. The resolution:

- Check in the **real 12,050,698-byte release archive** as
  `fixtures/cargo-nextest-0.9.145-x86_64-unknown-linux-gnu.tar.gz`. It is a
  byte-exact copy of the official asset, so extraction, the executable digest,
  and the version banner are all genuinely exercised, hermetically.
- Also check in the release's own published checksum asset (120 bytes) so the
  archive digest's provenance is upstream rather than self-computed.
- Mark both `-text` in `.gitattributes` so a checkout cannot normalize the
  bytes the digest is taken over.

Two obstructions were checked before committing to this, and neither holds:

- *Spelling gate*: it defaults to `--scope markdown`
  (`typos-config-builder gate --repository .`), so the binary is never scanned.
  Confirmed by running it: exit 0 with the fixture present. (A non-default
  `--scope all` run does flag binary noise, but that is not the gate the
  repository runs.)
- *Repository size*: the repo already tracks a 680 KB fixture, `/` has 627 GB
  free, and `uv run pytest` copies nothing — the tests read the fixture in
  place.

The alternative (opt-in live test only) was rejected: item 1 demands the test
be **CI-gated**, and an opt-in path gated on an env var is not.

Tests:

1. `test_published_checksum_fixture_records_the_pinned_archive_digest` — the
   release's published `.sha256` records the literal archive digest against the
   literal archive filename. This is what makes the digest upstream's value.
2. `test_release_archive_fixture_matches_the_pinned_expectations` — the
   checked-in archive's size and digest are the pinned literals.
3. `test_installer_pin_matches_the_independent_expectations` — the installer's
   `_release_for_platform()` resolves exactly the literals, compared in this
   direction so a partial bump names the drifted field.
4. `test_installer_installs_the_pinned_release_archive` — the end-to-end test.
   `urlopen` is redirected to the fixture while recording the URL; then the real
   `install_cargo_nextest()` runs to completion into a temporary `CARGO_HOME`.
   Asserts the constructed URL, the installed executable's digest, the
   executable bit, and that no `.tmp` staging file survives.
5. `test_installed_executable_reports_the_pinned_version` — the installed
   binary runs and its banner reads `cargo-nextest 0.9.145`. Skipped off
   Linux/glibc/x86_64, where that binary cannot execute.
6. `test_expectations_are_independent_of_the_installer_pin_tables` — asserts
   the installer's pin-table names appear nowhere in the module's own source,
   so the expectations cannot be replaced by references that would make every
   assertion vacuous.

The module never imports the installer by name; the `install_nextest_module`
fixture supplies it. Version probing uses plumbum via
`test_support.plumbum_helpers.run_plumbum_command`, since ruff's
`flake8-tidy-imports` config bans `subprocess.run` in this repository.

### Failure-path coverage

Not replaced. The existing `test_install_nextest_install_order_invariants`
parametrization (`None` plus `download`, `archive-digest`, `extract`,
`binary-digest`), `test_install_nextest_download_failure`,
`test_install_nextest_download_rejects_oversized_archive`,
`test_install_nextest_digest_mismatch_preserves_destination`, and the two
stubbed archive-rejection tests all stay as they are. The new tests add a
real-artefact happy path; they remove nothing.

### Documentation

- `docs/users-guide.md`: state the pin (0.9.145) and that it ends the
  build-script output warning flood under Cargo's new build-directory layout,
  keeping the existing official-release and dual-checksum explanation.
- `docs/developers-guide.md`: state the pin, that both checksum tables were
  refreshed (archive digests from the release's published `.sha256` assets,
  executable digests computed from the verified archives), why the bump was
  made, that future bumps must update all four artefacts (version, archive
  checksums, executable checksums, independent validation expectations)
  together, and name the new test module.

### PR negotiation order

1. Land the code and documentation changes; run `scrutineer` for the full
   gate sweep.
2. Add a PR comment to `#509` with an evidence-backed disposition of each
   CodeRabbit finding, explicitly mentioning `@coderabbitai`, and with the
   macOS cmd-mox failure evidence. Do not request a further review.
3. Wait for CodeRabbit confirmation. Treat GitHub's historical
   `CHANGES_REQUESTED` as non-blocking once that confirmation is in hand.
4. Once every required check on the head is green, post a new top-level
   comment containing exactly `@coderabbitai approve`.
5. On CodeRabbit approval, squash-merge as `leynos`.

## Open items

None. Both items below were closed before the merge.

- ~~Add a `docs/execplans/bump-nextest.md` "References" note to the PR body at
  the end, per the original brief.~~ Done: the PR body carries a
  `## References` section linking the Lody session and this plan.
- ~~Resolve the `python-tests (macos-15)` flake before merge.~~ Done by
  re-running, not by editing a test, exactly as the item prescribed. On the
  merged head `9c7c71e3` the job passed in 3m46s. The underlying `cmd_mox`
  startup race is pre-existing and out of scope here; CodeRabbit agreed it
  "does not implicate this PR's `cargo-nextest` changes" and it should be
  tracked as a separate upstream dependency reliability defect.

## Outcomes & Retrospective

Shipped to `main` as squash `37a39836` ("Bump pinned cargo-nextest to 0.9.145
(#509)"). All three CodeRabbit findings are resolved and CodeRabbit approved;
the historical `CHANGES_REQUESTED` review was superseded by that approval.
Codex raised no findings. 40 checks passed, 3 skipped, 0 failed.

What the change delivers:

- the pin moves to 0.9.145, with both checksum tables refreshed and the archive
  digests taken from each release's own published `.sha256` asset;
- six new tests validate the pin against the *real* release archive, hermetic
  because only `urllib.request.urlopen` is redirected;
- both guides state the pin, why it was chosen, and what a future bump must
  update.

What went well, and what to carry forward:

- **Independent literals were what made this test worth writing.** The existing
  end-to-end test derived its digests from synthetic content it had just built,
  so it could never disagree with the installer. Writing the expected values
  out as literals is what lets the new module fail on a wrong pin, which the
  mutation test demonstrated: bumping only `CARGO_NEXTEST_VERSION` to 0.9.146
  fails two tests, and corrupting only the archive-digest literal fails three.
- **Checking the suspected obstructions beat reasoning about them.** Two
  blockers looked fatal on inspection — a 12 MB binary fixture tripping the
  spelling gate and bloating the repository. Both were dismissed by measurement
  rather than argument: the gate defaults to `--scope markdown` and so never
  reads the fixture, and the repository already tracks large fixtures with the
  bytes held immutable by `-text`. Assuming either would have forced a weaker
  opt-in test that could not satisfy the CI-gated requirement.
- **The upstream naming trap is worth remembering.** The release publishes
  `cargo-nextest-<version>-<target>.sha256` with the archive extension
  *dropped*, so a 404 on `<archive>.sha256` is not evidence that no checksum
  exists. It briefly produced a missing-fixture failure here, and is now
  recorded in the developer guide.
- **A flake in a required check needs evidence, not a workaround.** Showing the
  same head SHA both pass and fail, with a different test failing each time,
  established the macOS `cmd_mox` failure as pre-existing. The correct action
  was to re-run the job and leave the tests untouched.

## Progress log

- (recon) PR state confirmed: zero inline review comments; the three findings
  live in CodeRabbit's reply comment 5752131262; Codex has no findings.
- (recon) Verified the release asset name, size, and both digests from the
  GitHub API and the release's own `.sha256` asset.
- (recon) Confirmed the macOS failure is a flake at a shared head SHA.
- (impl) Checked in the release archive and its published checksum; added
  `test_install_cargo_nextest_release.py` with six tests; marked both fixtures
  `-text`; updated both guides; committed as `a6affaf5`.
- (impl) ruff clean (`check` and `format --check`); the module passes (6/6);
  the whole generate-coverage suite passes (440 passed, 1 snapshot).
- (impl) Mutation-tested the module to prove it is load-bearing:
  - bumping only `CARGO_NEXTEST_VERSION` to 0.9.146 fails
    `test_installer_pin_matches_the_independent_expectations` and
    `test_installer_installs_the_pinned_release_archive` (the latter on the
    recorded URL), 2 failed / 4 passed;
  - corrupting only the archive-digest literal fails three tests, 3 failed /
    3 passed. Both mutants were reverted.
- (impl) Spelling gate green with the 12 MB fixture present (exit 0).
- (impl) `mdtablefix --check` rewraps applied; `markdownlint` clean on both
  edited docs.
- (impl) Discovered the published checksum asset is named
  `cargo-nextest-<version>-<target>.sha256` — the archive extension is dropped,
  so it is *not* `<archive>.sha256`. Recorded in the developer guide, since the
  same trap will catch the next person.
- (review) Posted an evidence-backed disposition of all three findings as PR
  comment 5752454806, mentioning `@coderabbitai`, without requesting a further
  review.
- (gate) Full sweep via `scrutineer`: `check-fmt`, `lint`, `typecheck`, `test`,
  `markdownlint`, `spelling`, `nixie` all EXIT=0; `make test` 2136 passed / 18
  skipped in 81.33s, the 18 being pre-existing platform-conditional skips.
- (ci) Pushed `9c7c71e3`. All required checks green, including the previously
  flaky `python-tests (macos-15)`; the newly added `coverage` lane passed in
  7m15s. 40 pass / 3 skip / 0 fail.
- (review) CodeRabbit confirmed all three findings addressed, then approved:
  "Comments resolved and changes approved." `reviewDecision=APPROVED`,
  `mergeStateStatus=CLEAN`.
- (merge) Posted the top-level `@coderabbitai approve` comment (5752511456),
  then squash-merged as requested. Branch `bump-nextest` deleted on merge.
