# Add cargo-binstall archive support to stage-release-artefacts

<!-- markdownlint-disable MD013 -->

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: COMPLETE

## Purpose / big picture

Projects that publish Rust command-line binaries often want `cargo-binstall` to
install prebuilt release assets instead of compiling from source.
`cargo-binstall` reads `[package.metadata.binstall]` from the crate manifest and
then downloads a release archive whose URL and internal binary path match that
metadata. Today this repository can build Rust binaries with
`.github/actions/rust-build-release` and can stage release files with
`.github/actions/stage-release-artefacts`, but it cannot create the
`{package}-{version}-{target}.tar.gz` archive shape used by the `dear-diary`
repository.

After this change, a workflow using `stage-release-artefacts` can opt in to a
`cargo-binstall` archive for a target. The action will copy normal artefacts as
it does today, create checksum sidecars, and additionally emit a tar.gz archive
whose filename and root-level binary entry match the common cargo-binstall
metadata:

```toml
[package.metadata.binstall.overrides.'cfg(target_os = "linux")']
pkg-url = "{ repo }/releases/download/v{ version }/{ name }-{ version }-{ target }.tar.gz"
bin-dir = "{ bin }{ binary-ext }"
pkg-fmt = "tgz"
```

Success is observable when running the staging action against a target with
binstall enabled produces a staged archive such as
`dist/myapp_linux_x86_64/myapp-1.2.3-x86_64-unknown-linux-gnu.tar.gz`, a
matching `.sha256` file, a `binstall-archive-path` output, and an archive whose
contents include `myapp` at the archive root.

The user approved implementation on 2026-05-20. Implementation is proceeding
milestone-by-milestone with the tolerances in this plan.

## Repository orientation

The target action is `.github/actions/stage-release-artefacts`. Its composite
action entrypoint is `.github/actions/stage-release-artefacts/action.yml`, which
runs `.github/actions/stage-release-artefacts/scripts/stage.py`.
Configuration parsing lives in
`.github/actions/stage-release-artefacts/scripts/stage_common/config.py`.
Copying, checksumming, and output emission live in
`.github/actions/stage-release-artefacts/scripts/stage_common/pipeline.py` and
`.github/actions/stage-release-artefacts/scripts/stage_common/output.py`.
Current unit tests are in
`.github/actions/stage-release-artefacts/tests/test_stage.py`.

Useful shared Cargo manifest helpers already exist in `cargo_utils.py`.
`export-cargo-metadata` uses those helpers to resolve package name, binary name,
and workspace-inherited version. The new staging feature should reuse those
helpers where manifest-derived values are needed rather than duplicating Cargo
TOML parsing.

The relevant external behaviour came from `dear-diary`: its crate manifest
declares a binstall override whose `pkg-url` ends with
`{ name }-{ version }-{ target }.tar.gz`, `bin-dir` is
`{ bin }{ binary-ext }`, and `pkg-fmt` is `tgz`. Its release helper writes a
tar.gz archive with the binary at the archive root and writes SHA-256 sidecars.
The upstream cargo-binstall documentation says metadata is optional, defaults
exist, but `pkg-url`, `bin-dir`, `pkg-fmt`, and target overrides are the right
metadata when a project needs explicit release asset layout.

Relevant documentation and skills to signpost during implementation:

- `AGENTS.md`, especially the command, commit, branch, and gateway rules.
- `/home/leynos/.codex/skills/execplans/SKILL.md`, because this document must
  remain current.
- `/home/leynos/.codex/skills/leta/SKILL.md`, because code navigation and
  refactors should prefer LSP-aware queries where practical.
- `/home/leynos/.codex/skills/rust-router/SKILL.md`, because the feature
  serves Rust release workflows and touches Cargo manifest concepts. No
  follow-on Rust language skill is required unless implementation starts
  modifying Rust code.
- `cargo-binstall` support documentation:
  `https://github.com/cargo-bins/cargo-binstall/blob/main/SUPPORT.md`.
- Existing action docs:
  `.github/actions/stage-release-artefacts/README.md`,
  `.github/actions/export-cargo-metadata/README.md`, and
  `.github/actions/upload-release-assets/README.md`.

## Constraints

- Keep `rust-build-release` focused on compiling Rust code. Do not move release
  packaging into that action unless implementation proves `stage-release-artefacts`
  cannot own the behaviour.
- Preserve all existing `stage-release-artefacts` configuration and outputs.
  Current users must not need to change their TOML files.
- Make binstall archive creation opt-in. A target without binstall config must
  behave exactly as it does now.
- Use only Python standard-library archive support unless an existing project
  dependency already provides the needed behaviour. Adding a runtime dependency
  for tar.gz creation is not allowed without escalation.
- Do not make network calls from the action to resolve cargo-binstall metadata.
  The action must operate from local files and workflow inputs only.
- The tar.gz archive must not allow destination path escapes. Archive entries
  must be controlled names under the archive root, not absolute paths or `..`
  paths.
- Generated checksum sidecars must remain in the existing
  `<artifact-name>.<algorithm>` format with lines containing
  `<digest>  <artifact-name>`.
- Follow repository command discipline. Use Makefile targets where available,
  run test, lint, format, and typecheck gates sequentially, and capture long
  outputs with `tee` under `/tmp`.
- Use `apply_patch` for manual file edits. Do not overwrite files with shell
  heredocs or ad hoc Python writers.
- Commit after each completed change milestone and gate the commit before
  creating it.

If satisfying the objective requires violating a constraint, stop, document the
conflict in `Decision Log`, and ask for direction.

## Tolerances (exception triggers)

- Scope: if implementation requires touching more than 12 files or more than
  500 net lines excluding snapshots and generated lockfile changes, stop and
  escalate.
- Interface: if an existing public input, output, or TOML key must be renamed or
  removed, stop and escalate.
- Dependencies: adding development-only dependencies for `pytest-bdd`,
  `syrupy`, and `hypothesis` is allowed because the requested validation needs
  them. Any runtime dependency or additional development dependency requires
  escalation.
- Test iterations: if the same gateway fails after 3 fix attempts, stop,
  document the failure and options, and ask for direction.
- Behavioural testing: if `act` is unavailable locally, keep the end-to-end
  workflow test in the tree, document that it was not run, and continue with
  unit, BDD, snapshot, property, typecheck, lint, and format gates.
- CodeRabbit: if `coderabbit review --agent` is unavailable or cannot run
  because of environment/authentication limits, document the exact failure and
  continue only after local gates pass. If CodeRabbit reports actionable
  concerns, address them before moving to the next milestone.
- Ambiguity: if the requested archive filename cannot be determined from either
  config fields or Cargo metadata without guessing, stop and present options.

## Risks

- Risk: The staging action may become overloaded if it learns too much about
  Cargo manifests.
  Severity: medium.
  Likelihood: medium.
  Mitigation: keep manifest use narrow: package name, binary name, version, and
  target only. Use optional explicit config overrides so non-Cargo projects can
  still use the action.

- Risk: Template configuration could allow unsafe archive member names.
  Severity: high.
  Likelihood: low.
  Mitigation: validate archive member names with the same path-escape discipline
  used for staged destinations. Reject absolute paths, `..`, and empty names.
  Add property tests over generated path fragments.

- Risk: Snapshot tests may be noisy because absolute temporary paths vary.
  Severity: low.
  Likelihood: medium.
  Mitigation: snapshot only stable output fragments or normalise temporary
  roots before assertion.

- Risk: The repository currently does not include `pytest-bdd`, `syrupy`, or
  `hypothesis` in the development dependency group.
  Severity: medium.
  Likelihood: high.
  Mitigation: add them to `[dependency-groups].dev` and update the Makefile
  `make test` inline `uv run --with ...` command only if required for the
  non-venv test path.

- Risk: End-to-end `act` tests may be slow or skipped on machines without a
  container runtime.
  Severity: low.
  Likelihood: medium.
  Mitigation: follow the existing opt-in `ACT_WORKFLOW_TESTS=1` convention and
  add a workflow-level test that is skipped unless the environment supports it.

## Design

Extend `stage-release-artefacts` with an optional `[common.binstall]` section
and optional per-target `[targets.<key>.binstall]` overrides. The default is no
binstall archive.

The proposed TOML shape is:

```toml
[common]
bin_name = "myapp"
dist_dir = "dist"
checksum_algorithm = "sha256"

[common.binstall]
enabled = true
manifest_path = "Cargo.toml"
archive_name = "{package_name}-{version}-{target}.tar.gz"
binary_source = "target/{target}/release/{bin_name}{bin_ext}"
binary_name = "{bin_name}{bin_ext}"
output = "binstall_archive_path"

[targets.linux-x86_64]
platform = "linux"
arch = "x86_64"
target = "x86_64-unknown-linux-gnu"

[targets.linux-x86_64.binstall]
enabled = true
```

`enabled` defaults to `false` globally and inherits from common to target.
`manifest_path` defaults to `Cargo.toml` and resolves relative to
`GITHUB_WORKSPACE`. `archive_name` defaults to
`{package_name}-{version}-{target}.tar.gz`. `binary_source` defaults to the
target release binary path. `binary_name` defaults to `{bin_name}{bin_ext}`.
The `output` key defaults to `binstall_archive_path` and is included in the
existing `artefact-map` machinery under the output name exposed by the action as
`binstall-archive-path`.

If `manifest_path` is present, parse it with `cargo_utils.read_manifest`,
`cargo_utils.get_package_field`, `cargo_utils.get_bin_name`, and
`cargo_utils.resolve_version`. If `package_name`, `version`, or `bin_name` are
explicitly configured, those explicit values take precedence. This keeps the
feature useful for workspace crates while still allowing projects to avoid
manifest parsing when needed.

Add a small `BinstallConfig` dataclass to `stage_common.config` and include it
on `StagingConfig`. Add a packaging helper in `stage_common.pipeline` or a new
`stage_common.binstall` module. A separate module is preferable if the helper
needs more than a few functions because tar writing, member-name validation,
and manifest value resolution are separate from normal file copying.

The staging pipeline should run in this order:

1. Initialise the clean staging directory.
2. Stage configured artefacts exactly as today.
3. If binstall is enabled, resolve metadata, find the binary source, create the
   archive in the same staging directory, write its checksum sidecar, add the
   archive to `staged_files`, add its digest to `checksum_map`, and add the
   configured output key to `artefact_map`.
4. Export outputs through the existing output writer.

The action should expose one new top-level output in `action.yml` and README:
`binstall-archive-path`, the absolute path to the generated archive when
enabled.

## Implementation plan

### Milestone 1: Dependencies and test scaffolding

Add the requested validation tools to the development dependency group:
`pytest-bdd`, `syrupy`, and `hypothesis`. Check whether `uv run pytest` through
`make test` can resolve them from the synced environment. If the Makefile's
inline `uv run --with ... pytest` command bypasses the development group for
some environments, add these packages to the `--with` list as well.

Create a feature file under
`.github/actions/stage-release-artefacts/tests/features/binstall_archive.feature`
describing the user-visible behaviour:

```gherkin
Feature: cargo-binstall archive staging
  Scenario: staging a Linux target creates a cargo-binstall archive
    Given a workspace with a Cargo package named "myapp" at version "1.2.3"
    And a release binary for target "x86_64-unknown-linux-gnu"
    And stage-release-artefacts has cargo-binstall archive creation enabled
    When the staging action runs for target "linux-x86_64"
    Then the staged files include "myapp-1.2.3-x86_64-unknown-linux-gnu.tar.gz"
    And the archive contains "myapp" at the root
    And a SHA-256 sidecar exists for the archive
    And the GitHub output includes "binstall_archive_path"
```

Add failing pytest unit tests that describe configuration parsing and archive
creation before implementing the feature. Add a snapshot test for the stable
`GITHUB_OUTPUT` shape when binstall is enabled. Add a property test for archive
member-name validation; for generated path-like strings, valid simple relative
names are accepted and absolute or parent-traversal names are rejected.

Validation after this milestone:

```bash
make check-fmt 2>&1 | tee /tmp/check-fmt-shared-actions-feat-binstall-metadata-audit.out
make typecheck 2>&1 | tee /tmp/typecheck-shared-actions-feat-binstall-metadata-audit.out
make lint 2>&1 | tee /tmp/lint-shared-actions-feat-binstall-metadata-audit.out
make test 2>&1 | tee /tmp/test-shared-actions-feat-binstall-metadata-audit.out
```

The new tests are expected to fail before implementation if they are run
selectively. Do not commit a deliberately failing full suite. Commit this
milestone only after either marking the new tests as pending with a clear
implementation marker or combining it with Milestone 2 so the full gates pass.

Run:

```bash
coderabbit review --agent
```

Address all actionable concerns before continuing. If the command cannot run,
record the failure in `Surprises & Discoveries`.

### Milestone 2: Config model and metadata resolution

Implement `BinstallConfig` parsing in `stage_common.config`. The config loader
must support common defaults and per-target overrides. It must reject invalid
types with `StageError` and report the offending config key.

Implement metadata resolution. Use explicit config values when present and
Cargo manifest values otherwise. Resolve workspace-inherited versions with
`cargo_utils.resolve_version`. Add unit tests for direct version, workspace
version inheritance, explicit value precedence, missing manifest errors, and
target-level disable overriding common enable.

Validation after this milestone:

```bash
make check-fmt 2>&1 | tee /tmp/check-fmt-shared-actions-feat-binstall-metadata-audit.out
make typecheck 2>&1 | tee /tmp/typecheck-shared-actions-feat-binstall-metadata-audit.out
make lint 2>&1 | tee /tmp/lint-shared-actions-feat-binstall-metadata-audit.out
make test 2>&1 | tee /tmp/test-shared-actions-feat-binstall-metadata-audit.out
coderabbit review --agent
```

Commit the passing milestone with a focused message.

### Milestone 3: Archive creation and outputs

Implement tar.gz archive creation. The archive path is rendered from
`archive_name`, and the member name is rendered from `binary_name`. The source
binary path is rendered from `binary_source` and resolved under
`GITHUB_WORKSPACE` using the existing source resolution behaviour where
reasonable. Use `tarfile.open(..., "w:gz")` from the Python standard library.

Add archive outputs to the existing `StageResult`, `staged_files`,
`checksum_map`, and `artefact_map` flow. Add the action output
`binstall-archive-path` to `.github/actions/stage-release-artefacts/action.yml`
and document it in the README.

Unit tests must verify archive filename, archive member name, checksum content,
output map key, disabled behaviour, and failure when the binary source is
missing. The syrupy snapshot should pin the output file format after normalising
temporary paths.

Validation after this milestone:

```bash
make check-fmt 2>&1 | tee /tmp/check-fmt-shared-actions-feat-binstall-metadata-audit.out
make typecheck 2>&1 | tee /tmp/typecheck-shared-actions-feat-binstall-metadata-audit.out
make lint 2>&1 | tee /tmp/lint-shared-actions-feat-binstall-metadata-audit.out
make test 2>&1 | tee /tmp/test-shared-actions-feat-binstall-metadata-audit.out
coderabbit review --agent
```

Commit the passing milestone with a focused message.

### Milestone 4: Behavioural and end-to-end workflow coverage

Add pytest-bdd step definitions for the feature file from Milestone 1. The BDD
test should exercise the public `stage_artefacts` behaviour through a realistic
temporary workspace and should assert archive contents, checksum sidecar, and
GitHub output keys.

Update `.github/workflows/test-stage-release-artefacts.yml` with an opt-in
workflow job that creates a small Cargo manifest and release binary, enables
binstall in the staging TOML, runs the composite action, and verifies:

- `binstall-archive-path` is non-empty.
- The archive exists.
- The archive checksum sidecar exists.
- `tar -tzf` shows the binary name at the archive root.

Update `tests/workflows/test_action_behaviors.py` so the act-based behavioural
test recognises the new workflow job and checks for the output in logs. This is
an end-to-end test because the change affects the externally observable
composite-action contract.

Validation after this milestone:

```bash
make check-fmt 2>&1 | tee /tmp/check-fmt-shared-actions-feat-binstall-metadata-audit.out
make typecheck 2>&1 | tee /tmp/typecheck-shared-actions-feat-binstall-metadata-audit.out
make lint 2>&1 | tee /tmp/lint-shared-actions-feat-binstall-metadata-audit.out
make test 2>&1 | tee /tmp/test-shared-actions-feat-binstall-metadata-audit.out
ACT_WORKFLOW_TESTS=1 make test 2>&1 | tee /tmp/act-test-shared-actions-feat-binstall-metadata-audit.out
coderabbit review --agent
```

If `ACT_WORKFLOW_TESTS=1 make test` cannot run because act or a container
runtime is unavailable, record that fact in this plan and include the skipped
validation in the final report.

Commit the passing milestone with a focused message.

### Milestone 5: Documentation, examples, and final gates

Update `.github/actions/stage-release-artefacts/README.md` with the new
configuration section, template variables, outputs, and a minimal example that
matches cargo-binstall metadata. Mention that the Cargo manifest must publish
compatible `[package.metadata.binstall]` metadata; the action creates release
assets, it does not mutate the crate manifest.

Update `.github/actions/stage-release-artefacts/CHANGELOG.md` with an unreleased
entry. If root documentation lists capabilities for the action, update it too.

Run final gates sequentially:

```bash
make check-fmt 2>&1 | tee /tmp/check-fmt-shared-actions-feat-binstall-metadata-audit.out
make typecheck 2>&1 | tee /tmp/typecheck-shared-actions-feat-binstall-metadata-audit.out
make lint 2>&1 | tee /tmp/lint-shared-actions-feat-binstall-metadata-audit.out
make test 2>&1 | tee /tmp/test-shared-actions-feat-binstall-metadata-audit.out
make markdownlint 2>&1 | tee /tmp/markdownlint-shared-actions-feat-binstall-metadata-audit.out
coderabbit review --agent
```

Commit the final documentation and cleanup milestone. Record the final commit
IDs, validation commands, and any skipped validations in `Outcomes &
Retrospective`.

## Progress

- [x] 2026-05-19: Investigated `dear-diary`, upstream cargo-binstall
  conventions, and the current `stage-release-artefacts` action shape.
- [x] 2026-05-19: Chose `stage-release-artefacts` as the integration point
  because it already owns staging, checksums, output maps, and workflow-visible
  artefacts.
- [x] 2026-05-19: Drafted this ExecPlan.
- [x] 2026-05-20: User approved implementation; status changed to
  `IN PROGRESS`.
- [x] 2026-05-20: Milestone 1 complete: dependencies and specification tests in
  place.
- [x] 2026-05-20: Milestone 2 complete: config model and metadata resolution
  implemented.
- [x] 2026-05-20: Milestone 3 complete: archive creation and outputs
  implemented locally; focused stage-release tests pass with snapshots.
- [x] 2026-05-20: Milestone 4 complete: the act workflow now has a
  cargo-binstall job and the act behavioural recogniser checks the new public
  output. Focused local validation passes; local act execution is skipped
  because `act` or a container runtime is unavailable.
- [x] 2026-05-20: Milestone 5 implementation complete: README and changelog
  document cargo-binstall archive staging, the new output, and the manifest
  metadata boundary.
- [x] 2026-05-20: Milestone 5 complete: documentation, final gates, CodeRabbit
  review, and final commits complete.

## Surprises & Discoveries

- 2026-05-19: `stage-release-artefacts` already has a generic checksum and
  output pipeline, making it a better integration point than
  `rust-build-release`.
- 2026-05-19: The repository currently has pytest unit tests for the staging
  action, but no local pytest-bdd feature files, syrupy snapshots, or
  hypothesis property tests for this action.
- 2026-05-19: `export-cargo-metadata` and `cargo_utils.py` already resolve
  workspace-inherited Cargo versions, so binstall archive naming does not need
  new manifest-parsing primitives.
- 2026-05-20: The Makefile test target uses ad hoc `uv run --with` dependency
  flags, so the requested test-only dependencies also need to be added there,
  not only to `[dependency-groups].dev`.
- 2026-05-20: The existing action output convention uses underscored internal
  `GITHUB_OUTPUT` keys and hyphenated composite action outputs. The new
  internal key is `binstall_archive_path`; the public action output is
  `binstall-archive-path`.
- 2026-05-20: `make test` currently fails in unrelated
  `.github/actions/rust-build-release` tests even when rerun under Python 3.13.
  The failures are outside the touched files and include direct Typer command
  calls receiving `OptionInfo` defaults plus existing cross/runtime assertions.
  The focused `stage-release-artefacts` test module passes.
- 2026-05-20: The existing act workflow tests assert outputs by matching log
  text, so the cargo-binstall end-to-end case echoes `binstall-archive-path`
  before asserting the archive and checksum sidecar on disk.
- 2026-05-20: `act` is not installed or not runnable with a container runtime
  in this environment. The new opt-in act test collects and is skipped by the
  repository's existing `skip_unless_act` guard.
- 2026-05-20: Updating both README and CHANGELOG would have exceeded the
  12-file tolerance if `BinstallConfig` stayed exported from
  `stage_common.__init__`. That export was unnecessary because internal tests
  already import from `stage_common.config`, so it was removed and focused
  stage tests still pass.

## Decision Log

- 2026-05-19: Integrate with `stage-release-artefacts`, not
  `rust-build-release`. Rationale: cargo-binstall support is release asset
  staging and packaging behaviour, while `rust-build-release` is documented as
  a build-only action.
- 2026-05-19: Make the feature opt-in through staging TOML. Rationale: current
  users must keep existing behaviour, and cargo-binstall metadata is optional
  upstream.
- 2026-05-19: Create tar.gz archives with the binary at the archive root.
  Rationale: this matches `dear-diary` and the common metadata
  `bin-dir = "{ bin }{ binary-ext }"`.
- 2026-05-19: Use development dependencies for pytest-bdd, syrupy, and
  hypothesis. Rationale: the user explicitly requested those validation styles,
  and they are test-only tools.
- 2026-05-20: Keep the cargo-binstall implementation inside the existing
  `stage_common.config` and `stage_common.pipeline` modules instead of adding a
  separate production module. Rationale: this keeps the touched file count
  within the ExecPlan tolerance and the new behaviour is still small enough to
  keep readable in the existing staging pipeline.
- 2026-05-20: Do not export `BinstallConfig` from the `stage_common` package
  facade. Rationale: the dataclass is an internal config detail, tests can
  import it from `stage_common.config`, and this keeps documentation work
  within the 12-file tolerance.

## Outcomes & Retrospective

Implementation is complete. Milestones 1-3 have local focused validation:
`uv run --with pytest-bdd --with syrupy --with hypothesis --with pytest-xdist
pytest .github/actions/stage-release-artefacts/tests/test_stage.py
--snapshot-update -v` passed with 45 tests. `make check-fmt`, `make lint`, and
`UV_PYTHON=3.13 make typecheck` pass. `UV_PYTHON=3.13 make test` fails in
unrelated `rust-build-release` tests; this is recorded as a gate exception
pending either a pre-existing-failure waiver or a separate fix outside the
current feature scope. The latest full run reported 773 passed, 14 skipped, and
12 failed, all in `.github/actions/rust-build-release`.
`coderabbit review --agent` initially hit a recoverable rate limit, then passed
with 0 findings after the requested wait. The focused post-snapshot test command
`UV_PYTHON=3.13 uv run --with pytest-bdd --with syrupy --with hypothesis
--with pytest-xdist pytest
.github/actions/stage-release-artefacts/tests/test_stage.py -v` passed with 45
tests and 1 snapshot. Milestone 4 added the
`test-stage-artefacts-binstall` workflow job and an act behavioural assertion
for `binstall-archive-path`. The focused changed-surface command
`UV_PYTHON=3.13 uv run --with pytest-bdd --with syrupy --with hypothesis
--with pytest-xdist pytest
.github/actions/stage-release-artefacts/tests/test_stage.py
tests/workflows/test_action_behaviors.py -k 'stage_release_artefacts or
stage-release-artefacts or stage_artefacts or binstall' -v` passed with 45
tests, 1 snapshot, 3 skipped act tests, and 8 deselected. The opt-in act
selection
`ACT_WORKFLOW_TESTS=1 UV_PYTHON=3.13 uv run --with typer --with packaging
--with plumbum --with pyyaml --with pytest-xdist --with pytest-bdd --with
syrupy --with hypothesis pytest tests/workflows/test_action_behaviors.py -k
'stage-release-artefacts-binstall' -v` collected the new end-to-end case and
skipped it because `act` or a container runtime is unavailable locally.
`coderabbit review --agent` for milestone 4 also initially hit a recoverable
rate limit, then passed with 0 findings after the requested wait.
Milestone 5 documentation updates have been made to the action README and
CHANGELOG. Final validation results:

- `make check-fmt` passed.
- `UV_PYTHON=3.13 make typecheck` passed.
- `make lint` passed.
- `UV_PYTHON=3.13 uv run --with pytest-bdd --with syrupy --with hypothesis
  --with pytest-xdist pytest
  .github/actions/stage-release-artefacts/tests/test_stage.py
  tests/workflows/test_action_behaviors.py -k 'stage_release_artefacts or
  stage-release-artefacts or stage_artefacts or binstall' -v` passed with 45
  passed, 3 skipped, and 8 deselected.
- `UV_PYTHON=3.13 make test` still fails only in unrelated
  `.github/actions/rust-build-release` tests, with 773 passed, 14 skipped, and
  12 failed.
- `make markdownlint` passed.
- `coderabbit review --agent` initially hit a recoverable 1 minute 3 second
  rate limit, then passed with 0 findings after the requested wait. After
  README markdownlint cleanup, the adjusted final review hit a recoverable
  2 minute 46 second rate limit and then passed with 0 findings.

Local act execution remains skipped because `act` or a container runtime is not
available in this environment.

Commits on `feat/binstall-metadata-audit`:

- `f26860a` Plan cargo-binstall archive staging support
- `48c09cc` Add cargo-binstall archive staging support
- `126c539` Add cargo-binstall workflow coverage
- `81e521c` Keep binstall config internal
- `8f9f4e5` Document cargo-binstall archive staging
- final checkpoint commit: records README markdownlint cleanup and this
  validation update

The final branch touches 12 non-snapshot files plus one syrupy snapshot,
matching the file-count tolerance.
