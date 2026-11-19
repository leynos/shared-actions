# Migrating to the mutation environment preflight

This guide covers the mutation environment preflight, added in the next
release. It checks, once and before any mutant is enumerated, that the
environment a mutation run will use satisfies the requirements the repository
declares. Adopting it is optional: a caller that does not set `preflight: true`
is unchanged.

## Who should read this

Read this if a mutation lane occasionally fails before it exercises a mutant: a
missing file inside `mutants/`, a build or import error after a long setup, an
external command that was never installed, or an unsupported native build.
Those failures share a cause, and the preflight exists to name it before the
run starts rather than after the baseline.

## What it buys

On the mutmut lane the preflight compares the declaration against the sandbox
mutmut will actually build, not against the checkout. A path that exists but is
not copied fails the preflight with a reason, instead of failing the baseline
inside the copy. On both lanes it reports one verdict per phase, so a failure
names the phase, the path, the command or the interpreter that did not hold.

## Adopt the preflight

### 1. Enable the input

Set `preflight: true` in the caller workflow:

```yaml
jobs:
  mutation:
    uses: leynos/shared-actions/.github/workflows/mutation-mutmut.yml@<sha>
    with:
      preflight: true
```

The cargo lane takes the same input. It also takes `project-python` when the
mutated crate has Python bindings, so a declared `native.supported_python`
range is judged against the interpreter the bindings are built and tested with.

### 2. Write the declaration

Declare the requirements in `[tool.mutation-preflight]` in `pyproject.toml`, or
in `.github/mutation-preflight.toml`. Use one, never both: a checkout that
declares in both places is refused, so there is exactly one source of truth.
Declaring nothing is not a failure: the `manifest` phase reports `skipped`, the
interpreter is still checked, and the requirement phases report `skipped`.

### 3. Run it locally

The workflow sets the helper's inputs as environment variables. The same
variables run the helper by hand from a `shared-actions` checkout, with
`INPUT_REPOSITORY` pointing at the project:

```sh
INPUT_LANE=mutmut \
  INPUT_PROJECT_PYTHON=3.13 \
  INPUT_REPOSITORY=/path/to/project \
  uv run --script workflow_scripts/mutation_preflight.py
```

`INPUT_LANE` is `mutmut` or `cargo`. `INPUT_OUTPUT_DIR` names where the
diagnostics are written and defaults to `mutation-preflight`. The mutmut lane
also reads `INPUT_MUTMUT_VERSION`, because the sandbox contract belongs to the
selected release.

### 4. Read the diagnostics

The helper writes `mutation-preflight.json` and bounded per-check logs under
`checks/`. Both workflows upload the directory under `always()`:
`mutation-preflight-diagnostics-mutmut` for the mutmut lane, and
`mutation-preflight-diagnostics-<slug>-<shard>` for each cargo shard. The job
summary carries the same phases as a table. Each phase emits one
`mutation_preflight_phase` line on standard output, and every finding becomes
one `mutation_preflight_finding` line on standard error naming the phase it
came from. A failing run exits non-zero, so the mutation run never starts.

### 5. Fix the findings

Fix what each finding names: add the missing `also_copy` entry, install the
command, select an interpreter inside `requires-python`, pin the revision the
lock records, or repair the declared check. Re-run the helper locally until
every phase reports `pass`, `skipped` or `unknown`.

### 6. Keep it blocking

The input is blocking by design: any `fail` exits non-zero. Once the findings
are fixed, leave `preflight: true` on the default branch, so a requirement that
regresses stops the run before a mutant is enumerated. Setting the input back to
`false` restores the previous behaviour but gives up that guarantee.

## What the declaration contains

The full schema is in [Mutation environment preflight](mutation-preflight.md).
In brief:

- `sandbox_paths` (mutmut only) names paths the `mutants/` copy must contain.
  A path mutmut does not copy fails unless an `also_copy` entry carries it.
- `also_copy` is mutmut's own `[tool.mutmut]` key. The preflight reads it to
  decide what the copy will contain; it is not a `[tool.mutation-preflight]`
  key. Hidden trees such as `.github/workflows` are copied only when named.
- `require_commands` names external commands the run invokes, optionally with
  the version each must report.
- `native` names a locked native dependency, the revision `uv.lock` must pin,
  and the interpreter range it supports.
- `checks` names bounded commands to run from a declared `cwd`, environment
  and import root.

The cargo lane refuses `sandbox_paths`, because cargo-mutants copies the whole
working tree.

## The subprocess and nested-pytest fixture contract

A fixture that shells out, or runs a nested pytest, depends on more than the
checkout: a working directory, an executable on `PATH`, an environment and an
import root. The preflight makes that contract explicit, because the copy
mutmut builds is not the checkout.

- Declare the command the mutation run itself uses, in list form such as
  `["uv", "run", "python", "-m", "pytest", "-q", "tests"]`, rather than a bare
  `python`. A bare name resolves only what the helper's environment has
  installed.
- Set `cwd` to the directory the command expects. A test path is written
  relative to `cwd`, not to the checkout.
- Put a relative `PYTHONPATH` in `env` when the fixture needs an import root.
  The environment is layered over the ambient one.
- Declare every file the command reads in `sandbox_paths`, and add the
  matching `also_copy` entry for anything mutmut does not copy by default.

On the cargo lane the whole tree is copied, so a check runs in the checkout
itself and needs no `sandbox_paths` entry.

## Required dependencies and the cache backend

A required dependency fails a named phase. The optional cache backend does not:
a backend that is down keeps using the existing `setup-rust` uncached fallback
and publishes its status, so a backend outage cannot turn a real baseline
failure into a preflight failure, or the reverse. Backend ownership, wrapper
scope, runner placement and cache limits are unchanged.

## What does not need to change

A caller that leaves `preflight` at its default keeps the same job. The
baseline, mutant selection, result parsing and reporting are unchanged: the
preflight validates prerequisites and is not a replacement baseline.

## Troubleshooting

- `pass` — nothing to do; the phase established what it checks.
- `fail` — fix what the finding names: the `also_copy` entry, the command,
  the interpreter range, the pinned revision or the declared check.
- `skipped` — the phase does not apply to this lane, or nothing was declared
  for it. On the mutmut lane, `checks` also reports `skipped` when the
  interpreter phase failed, and the reason is recorded.
- `unknown` — the helper could not decide, and on its own the phase does not
  fail the run. Supply the missing input when the check matters:
  `project-python` on the cargo lane for a `native.supported_python` range, or a
  `supported_python` declaration for a native dependency. An unknown mutmut
  release is reported the same way rather than assumed to copy what a known
  release copies.

## Rolling back

Set `preflight` back to `false`, or remove it. The declaration can stay: it is
read only when the input is enabled, and nothing outside the job's workspace or
runner temporary directory is written.
