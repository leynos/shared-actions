# Force LLVM codegen backend for coverage and test cranelift override

This ExecPlan (execution plan) is a living document. The sections
`Constraints`, `Tolerances`, `Risks`, `Progress`, `Surprises & Discoveries`,
`Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work
proceeds.

Status: DRAFT

## Purpose / big picture

Rust projects that use the Cranelift codegen backend for faster compile times
cannot generate LLVM source-based code coverage because the `-C
instrument-coverage` flag is an LLVM-only feature. When a project sets
`codegen-backend = "cranelift"` in its `.cargo/config.toml` (or `Cargo.toml`)
and then runs `cargo llvm-cov`, the build fails or produces no coverage data.

After this change, the generate-coverage action will explicitly force the LLVM
codegen backend for both `dev` and `test` profiles when invoking `cargo
llvm-cov`, regardless of what the consuming project has configured. This
ensures coverage generation works even when the project normally compiles with
Cranelift.

A new behavioural test will validate this by cloning the repository's toy Rust
fixture application, configuring it to use the Cranelift backend via
`.cargo/config.toml`, and then running the coverage script against that clone.
The test will confirm that coverage generation succeeds (meaning the LLVM
override took effect), despite the project being configured for Cranelift.

Observable success: running `make test` passes, and the new test (which
exercises a Cranelift-configured project) produces valid coverage output using
the LLVM backend.

## Constraints

- Follow repository policy in `AGENTS.md`: run `make check-fmt`, `make
  typecheck`, `make lint`, and `make test` before each commit that touches
  GitHub Action logic or Python code.
- Use `tee` and `set -o pipefail` to capture long command output into temporary
  log files for review after completion.
- Preserve the existing public interface of the generate-coverage action. No
  inputs or outputs may be added, removed, or have their default behaviour
  changed by this work.
- Keep changes scoped to the generate-coverage action's scripts and tests
  unless a supporting change is strictly required.
- Do not loosen security posture: keep third-party action pins and do not
  introduce unsigned downloads.
- The `--config` flags must be placed as top-level cargo arguments (before the
  `llvm-cov` subcommand), because `cargo llvm-cov` does not accept `--config`
  as its own flag. The syntax is:
  `cargo --config 'profile.dev.codegen-backend="llvm"' --config
  'profile.test.codegen-backend="llvm"' llvm-cov ...`.

## Tolerances (exception triggers)

- Scope: if implementation requires changes to more than 6 files or more than
  250 net new lines of code, stop and escalate.
- Interface: if any existing action inputs or outputs must change, stop and
  escalate.
- Dependencies: if a new external dependency is required beyond what is already
  declared in the action's scripts or `pyproject.toml`, stop and escalate.
- Iterations: if tests still fail after 3 full test cycles, stop and escalate
  with logs.
- Ambiguity: if the correct placement or behaviour of the `--config` flags is
  unclear after research, stop and present options with trade-offs.

## Risks

- Risk: `cargo --config 'profile.dev.codegen-backend="llvm"'` may behave
  differently across Rust toolchain versions, or the `codegen-backend` key may
  require the unstable `codegen-backend` feature flag on some toolchains.
  Severity: medium
  Likelihood: low
  Mitigation: the generate-coverage action already pins toolchain versions via
  `rust-toolchain.toml` in consuming projects. Stable Rust 1.79+ supports
  `codegen-backend` in profiles without `-Z` flags. The `--config` flag is
  stable since Rust 1.63. The action's CI tests will validate the flag works
  with the toolchain version used by the test fixture (`rust-toy-app` requires
  Rust 1.89). Even if the flag is silently ignored on an LLVM-only toolchain
  (which is the default), this is harmless since LLVM is already the active
  backend.

- Risk: installing the `rustc-codegen-cranelift-preview` component may fail on
  some platforms or toolchains (it is only available on nightly, or on stable
  as of Rust 1.73+ on limited platforms).
  Severity: medium
  Likelihood: medium
  Mitigation: the behavioural test only needs the component to be referenced in
  `.cargo/config.toml` to prove the override works. Since the test uses
  cmd-mox to stub `cargo`, the actual Cranelift component does not need to be
  installed on the test runner. The stub validates that the correct `--config`
  flags appear in the cargo arguments. If a future integration test (outside
  this plan's scope) needs the real component, it can be skipped on platforms
  where it is unavailable.

- Risk: prepending `--config` args before `llvm-cov` in the argument list could
  break existing tests that assert exact argument sequences.
  Severity: medium
  Likelihood: high
  Mitigation: update all affected test assertions to include the new `--config`
  flags in the expected argument list. The exploration phase identified that
  tests in `test_scripts.py` assert exact `cargo_args` lists; these must all be
  updated.

## Progress

- [ ] Drafted initial ExecPlan.
- [ ] Located all cargo argument assertions in test suite.
- [ ] Updated `get_cargo_coverage_cmd` in `run_rust.py` to prepend `--config`
      flags.
- [ ] Updated all existing tests to expect the new `--config` flags in the
      cargo argument list.
- [ ] Added new behavioural test for Cranelift-configured project.
- [ ] Ran required Makefile gateways and committed.

## Surprises & discoveries

- Observation: none yet.
  Evidence: N/A
  Impact: none.

## Decision log

- Decision: place `--config` flags as top-level cargo args before `llvm-cov`,
  not as `cargo llvm-cov` flags.
  Rationale: `cargo llvm-cov` does not recognise `--config` as its own flag
  (tested: `cargo llvm-cov --config '...'` returns `error: invalid option
  '--config'`). However, `cargo --config '...' llvm-cov ...` works because
  `--config` is a top-level cargo option processed before subcommand dispatch.
  In plumbum terms, `cargo["--config", "...", "--config", "...", "llvm-cov",
  ...]` produces the correct invocation.
  Date/Author: 2026-04-14 (DevBoxer)

- Decision: use cmd-mox stubs (not real cargo invocations) for the behavioural
  test.
  Rationale: the existing test infrastructure uses cmd-mox to stub shell
  commands. This approach is consistent with all other tests in
  `test_scripts.py`, avoids requiring the Cranelift component on CI runners,
  and keeps tests fast and deterministic. The test validates that the
  `--config` flags appear in the cargo argument list, which is sufficient to
  prove the override will take effect at runtime.
  Date/Author: 2026-04-14 (DevBoxer)

## Outcomes & retrospective

Not yet started.

## Context and orientation

The generate-coverage action lives at
`.github/actions/generate-coverage/` and is a composite GitHub Action that runs
test coverage for Rust, Python, and mixed projects. For Rust projects, it
invokes `cargo llvm-cov` (optionally with `nextest`) via the Python script
`.github/actions/generate-coverage/scripts/run_rust.py`.

The function `get_cargo_coverage_cmd()` (line 102 of `run_rust.py`) assembles
the argument list passed to `cargo`. Currently it produces arguments like:

```plaintext
["llvm-cov", "nextest", "--manifest-path", "Cargo.toml", "--workspace",
 "--summary-only", "--lcov", "--output-path", "cov.lcov"]
```

These arguments are passed to plumbum's `cargo[args].popen(...)` in the
`_run_cargo()` function (line 304), which executes `cargo llvm-cov nextest
--manifest-path ... `.

The `--config` flag is a top-level cargo option (not a subcommand option). It
must appear before the `llvm-cov` subcommand in the argument list. The
resulting invocation will be:

```plaintext
cargo --config 'profile.dev.codegen-backend="llvm"' \
      --config 'profile.test.codegen-backend="llvm"' \
      llvm-cov nextest --manifest-path Cargo.toml ...
```

The test suite lives at `.github/actions/generate-coverage/tests/test_scripts.py`.
Tests use cmd-mox (via the `shell_stubs` fixture from `conftest.py`) to stub
the `cargo` command and capture the argument list. The `_run_rust_coverage_test`
helper (line 162) runs `run_rust.py` via `uv run --script` and then inspects
`shell_stubs.calls_of("cargo")` to assert the exact argument list passed.

The `RustCoverageConfig` dataclass (line 144) and `RustMainConfig` dataclass
(line 154) configure test variants. The `_run_rust_main_variant` helper (line
334) tests `main()` directly with a monkeypatched `_run_cargo`.

The toy Rust fixture at `rust-toy-app/` is a simple CLI application used as a
test fixture. It has no `.cargo/config.toml` file. For the new behavioural
test, a `.cargo/config.toml` will be created in a temporary copy of this
fixture to simulate a Cranelift-configured project.

Cranelift is an alternative codegen backend for the Rust compiler. It is faster
than LLVM for debug builds but does not support LLVM-specific features like
`-C instrument-coverage` (source-based code coverage). Projects using Cranelift
configure it via `.cargo/config.toml`:

```toml
[unstable]
codegen-backend = true

[profile.dev]
codegen-backend = "cranelift"
```

When `cargo llvm-cov` is run on such a project, it fails because the
Cranelift backend does not support instrumentation. The fix is to override the
codegen backend to LLVM via `--config` flags on the cargo command line, which
take precedence over `.cargo/config.toml` settings.

## Plan of work

### Stage A: update `get_cargo_coverage_cmd` to prepend `--config` flags

In `.github/actions/generate-coverage/scripts/run_rust.py`, modify
`get_cargo_coverage_cmd()` to prepend two `--config` arguments before
`"llvm-cov"` in the returned argument list. The function currently builds
`args` starting with `["llvm-cov"]`. Change this to start with the config
overrides:

```python
args = [
    "--config",
    'profile.dev.codegen-backend="llvm"',
    "--config",
    'profile.test.codegen-backend="llvm"',
    "llvm-cov",
]
```

The rest of the function remains unchanged. This ensures every `cargo llvm-cov`
invocation forces the LLVM backend, harmlessly overriding any project-level
Cranelift configuration and having no effect when LLVM is already the active
backend.

Validation: the change is purely additive to the argument list. No behaviour
changes for projects already using LLVM. Run existing tests (they will fail
because they assert exact argument lists — this is expected and fixed in Stage
B).

### Stage B: update all existing test assertions

Every test in `test_scripts.py` that asserts the exact cargo argument list must
be updated to include the four new leading elements (`"--config"`,
`'profile.dev.codegen-backend="llvm"'`, `"--config"`,
`'profile.test.codegen-backend="llvm"'`) before `"llvm-cov"`.

The affected tests and locations (all in
`.github/actions/generate-coverage/tests/test_scripts.py`):

1. `test_run_rust_success` (line ~218): update `expected_args` list.
2. `test_run_rust_nextest_command` (line ~250): update `expected_args` list.
3. `test_run_rust_uses_detected_manifest_path` (line ~273): update the
   `cargo_args[0:3]` slice assertion — the prefix now has four additional
   elements, so the slice index must shift.
4. `test_get_cargo_coverage_cmd_variants` (line ~277): update both
   parametrised `expected` lists.
5. `test_run_rust_main_nextest_variants` (line ~370): update the `args[:2]`
   assertion (now `args[:6]` or similar) and the `args[0]` assertion for the
   non-nextest path.
6. Any cucumber-related tests that assert cargo argument lists (search for
   `"llvm-cov"` in assertions).

Approach: define a module-level constant in the test file for the config prefix
to avoid repeating the four-element list in every test:

```python
_LLVM_CONFIG_PREFIX = [
    "--config",
    'profile.dev.codegen-backend="llvm"',
    "--config",
    'profile.test.codegen-backend="llvm"',
]
```

Then use `[*_LLVM_CONFIG_PREFIX, "llvm-cov", ...]` in expected argument lists.

Validation: run `make test` and confirm all existing tests pass with the
updated assertions.

### Stage C: add new behavioural test for Cranelift-configured project

Add a new test function `test_run_rust_cranelift_project_uses_llvm_codegen` to
`test_scripts.py`. This test will:

1. Create a temporary directory with a `.cargo/config.toml` that configures
   Cranelift:

   ```toml
   [unstable]
   codegen-backend = true

   [profile.dev]
   codegen-backend = "cranelift"

   [profile.test]
   codegen-backend = "cranelift"
   ```

2. Copy (or symlink) the `rust-toy-app/Cargo.toml` into the temporary
   directory to satisfy the manifest path requirement (or just use the existing
   `RustCoverageConfig` with a stub cargo).

3. Use `_run_rust_coverage_test` (or a variant of it) with `shell_stubs` to
   stub `cargo` and capture the argument list.

4. Assert that the captured cargo arguments contain the `--config
   'profile.dev.codegen-backend="llvm"'` and `--config
   'profile.test.codegen-backend="llvm"'` flags before the `llvm-cov`
   subcommand.

5. Assert that coverage output is generated successfully (the stub returns
   valid coverage data).

The test proves that even when a project has Cranelift configured in its
`.cargo/config.toml`, the action's cargo invocation includes the LLVM override
flags. The actual override is handled by cargo's `--config` precedence rules
(command-line `--config` takes precedence over `.cargo/config.toml`), which is
cargo's documented behaviour and does not need to be tested here.

The test should also ensure the Cranelift rustup component would be installed
in a real scenario. Since we are using cmd-mox stubs, this is represented by a
comment or docstring explaining that the real-world prerequisite is `rustup
component add rustc-codegen-cranelift-preview`. The test's purpose is to
validate the action's argument construction, not the Cranelift component
installation.

Validation: run `make test` and confirm the new test passes. The new test
should be visible in the pytest output as
`test_run_rust_cranelift_project_uses_llvm_codegen`.

### Stage D: run gating commands and finalise

Run the full suite of repository gating commands:

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/shared-actions-check-fmt.log
make typecheck 2>&1 | tee /tmp/shared-actions-typecheck.log
make lint 2>&1 | tee /tmp/shared-actions-lint.log
make test 2>&1 | tee /tmp/shared-actions-test.log
```

Inspect each log file. All must pass. If any fail, fix the issue and re-run.

## Concrete steps

1. Edit `.github/actions/generate-coverage/scripts/run_rust.py`, function
   `get_cargo_coverage_cmd` (line 112). Change:

   ```python
   args = ["llvm-cov"]
   ```

   to:

   ```python
   args = [
       "--config",
       'profile.dev.codegen-backend="llvm"',
       "--config",
       'profile.test.codegen-backend="llvm"',
       "llvm-cov",
   ]
   ```

2. Edit `.github/actions/generate-coverage/tests/test_scripts.py`. Add a
   constant near the top of the file (after the imports and before the helper
   functions):

   ```python
   _LLVM_CONFIG_PREFIX = [
       "--config",
       'profile.dev.codegen-backend="llvm"',
       "--config",
       'profile.test.codegen-backend="llvm"',
   ]
   ```

3. Update `test_run_rust_success` expected args from `["llvm-cov", ...]` to
   `[*_LLVM_CONFIG_PREFIX, "llvm-cov", ...]`.

4. Update `test_run_rust_nextest_command` expected args similarly.

5. Update `test_run_rust_uses_detected_manifest_path` slice assertion from
   `cargo_args[0:3]` to `cargo_args[0:7]` and update the expected value to
   `[*_LLVM_CONFIG_PREFIX, "llvm-cov", "--manifest-path",
   "rust-toy-app/Cargo.toml"]`.

6. Update `test_get_cargo_coverage_cmd_variants` parametrised expected lists to
   include `_LLVM_CONFIG_PREFIX` at the start. Note: since the test uses
   `run_rust_module.get_cargo_coverage_cmd(...)` directly (not via shell
   stubs), the assertion is on the return value of the function.

7. Update `test_run_rust_main_nextest_variants` assertions: change `args[:2]`
   to `args[:6]` and update expected values to include the config prefix.
   Change `args[0] == "llvm-cov"` to `args[4] == "llvm-cov"`.

8. Search for any other tests asserting cargo argument lists containing
   `"llvm-cov"` and update them. This includes cucumber-related tests:
   `test_run_rust_with_cucumber`, `test_run_rust_with_cucumber_nextest`,
   `test_run_rust_with_cucumber_cobertura`.

9. Add the new test function `test_run_rust_cranelift_project_uses_llvm_codegen`
   to `test_scripts.py`:

   ```python
   def test_run_rust_cranelift_project_uses_llvm_codegen(
       tmp_path: Path,
       shell_stubs: StubManager,
       monkeypatch: pytest.MonkeyPatch,
   ) -> None:
       """Coverage forces LLVM codegen even when project configures Cranelift.

       When a Rust project uses the Cranelift codegen backend (configured in
       .cargo/config.toml), the coverage action must still invoke cargo with
       --config flags that override the codegen backend to LLVM, because
       source-based code coverage (-C instrument-coverage) is an LLVM-only
       feature.

       In a real-world scenario, the Cranelift component would be installed via
       ``rustup component add rustc-codegen-cranelift-preview``.
       """
       # Simulate a Cranelift-configured project
       cargo_config_dir = tmp_path / ".cargo"
       cargo_config_dir.mkdir()
       (cargo_config_dir / "config.toml").write_text(
           '[unstable]\ncodegen-backend = true\n\n'
           '[profile.dev]\ncodegen-backend = "cranelift"\n\n'
           '[profile.test]\ncodegen-backend = "cranelift"\n',
       )

       cargo_args, _out, _gh = _run_rust_coverage_test(
           tmp_path,
           shell_stubs,
           RustCoverageConfig(use_nextest=True),
           monkeypatch=monkeypatch,
       )

       # The config prefix must appear before llvm-cov to override Cranelift
       assert cargo_args[:4] == _LLVM_CONFIG_PREFIX
       assert cargo_args[4] == "llvm-cov"
       assert cargo_args[5] == "nextest"
   ```

10. Run gating commands (step-by-step from Stage D).

## Validation and acceptance

Acceptance is met when the following are true:

- `get_cargo_coverage_cmd()` returns an argument list where the first four
  elements are the two `--config` key-value pairs forcing `profile.dev` and
  `profile.test` codegen backends to `"llvm"`, followed by `"llvm-cov"`.

- All existing tests in `test_scripts.py` pass with updated assertions that
  include the config prefix.

- The new test `test_run_rust_cranelift_project_uses_llvm_codegen` passes,
  confirming that the LLVM override flags are present even when a project has
  Cranelift configured in `.cargo/config.toml`.

- `make check-fmt`, `make typecheck`, `make lint`, and `make test` all succeed.

Quality criteria:

- Tests: all required Makefile gates pass and the new test covers the Cranelift
  override behaviour.
- Lint/typecheck: no new warnings or failures.

Quality method:

```bash
set -o pipefail
make check-fmt 2>&1 | tee /tmp/shared-actions-check-fmt.log
make typecheck 2>&1 | tee /tmp/shared-actions-typecheck.log
make lint 2>&1 | tee /tmp/shared-actions-lint.log
make test 2>&1 | tee /tmp/shared-actions-test.log
```

## Idempotence and recovery

All steps are re-runnable. The code changes are additive (prepending arguments
to a list). Test assertions are updated to match. If tests fail, fix the issue
and re-run the same Makefile targets. No temporary files are created outside of
`/tmp/` and the test's `tmp_path` fixture (which pytest cleans up
automatically).

## Artifacts and notes

Expected cargo argument list after the change (example with nextest):

```plaintext
["--config", "profile.dev.codegen-backend=\"llvm\"",
 "--config", "profile.test.codegen-backend=\"llvm\"",
 "llvm-cov", "nextest",
 "--manifest-path", "Cargo.toml",
 "--workspace", "--summary-only",
 "--lcov", "--output-path", "cov.lcov"]
```

Verified by manual testing that `cargo --config
'profile.dev.codegen-backend="llvm"' --config
'profile.test.codegen-backend="llvm"' llvm-cov --lcov --output-path /tmp/test-
cov.lcov` compiles and runs successfully in the `rust-toy-app` directory.

Also verified that `cargo llvm-cov --config '...'` (config flag after
`llvm-cov`) fails with `error: invalid option '--config'`, confirming the flags
must be placed before the subcommand.

## Interfaces and dependencies

No new dependencies. No new action inputs or outputs. The only interface change
is internal: `get_cargo_coverage_cmd()` now returns four additional leading
elements in its argument list.

Files to modify:

- `.github/actions/generate-coverage/scripts/run_rust.py` (the
  `get_cargo_coverage_cmd` function, approximately 1 line changed to 6 lines).
- `.github/actions/generate-coverage/tests/test_scripts.py` (constant
  addition, assertion updates across ~8–12 test functions, and one new test
  function of approximately 30 lines).
