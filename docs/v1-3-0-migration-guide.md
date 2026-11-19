# Migration guide: v1.2.2 to v1.3.0

This guide covers one step: v1.2.2 to v1.3.0, the release that adds the
mutation environment preflight. The preflight checks, once and before any
mutant is enumerated, that the environment a mutation run will use satisfies
the requirements the repository declares. Adopting it is optional: a caller
that does not set `preflight: true` is unchanged.

## Who should read this

Read this if a mutation lane occasionally fails before it exercises a mutant: a
missing file inside `mutants/`, a build or import error after a long setup, an
external command that was never installed, or an unsupported native build.
Those failures share a cause, and the preflight names it before the run starts
rather than after the baseline.

Read it as well if you maintain a caller workflow, because the new optional
inputs sit on `mutation-mutmut.yml` and `mutation-cargo.yml` rather than on an
action the caller composes itself.

## Impact at a glance

| Priority           | Area              | What to do                                                                                                |
| ------------------ | ----------------- | --------------------------------------------------------------------------------------------------------- |
| Optional           | Preflight input   | Set `preflight: true` in a mutation caller that should check its declared requirements                    |
| Required if called | Declaration       | Write the requirements in exactly one of `[tool.mutation-preflight]` or `.github/mutation-preflight.toml` |
| Required if called | Cargo interpreter | Supply `project-python` when a mutated crate has Python bindings and the declaration names a native range |
| Review if enabled  | Sandbox paths     | Add an `also_copy` entry for every declared path mutmut does not copy by default                          |
| Review if enabled  | Required commands | Make sure each `require_commands` name resolves on the job's `PATH`                                       |
| Review if enabled  | Native dependency | Pin the revision `uv.lock` records and declare `supported_python` for the interpreter in use              |
| Review if enabled  | Declared checks   | Give each check its `cwd`, `env` and a test path written relative to `cwd`                                |
| Recommended        | Diagnostics       | Read the uploaded diagnostics directory while adopting, rather than only the job summary                  |
| Recommended        | Local runs        | Run the helper by hand until every phase reports `pass`, `skipped` or `unknown`                           |

_Table 1: Obligations when moving from v1.2.2 to v1.3.0._

## 1. Enable the input

Both reusable workflows take the same boolean input:

```yaml
jobs:
  mutation:
    uses: leynos/shared-actions/.github/workflows/mutation-mutmut.yml@<sha>
    with:
      preflight: true
```

The cargo lane accepts the same input. It also accepts `project-python` when
the mutated crate has Python bindings, so a declared `native.supported_python`
range is judged against the interpreter the bindings are built and tested with.

Leave the input at its default until the declaration in step 2 is complete.
Setting it to `true` with no declaration is not an error, but it checks only
the interpreter and reports the requirement phases as `skipped`.

## 2. Write the declaration

Declare the requirements in `[tool.mutation-preflight]` in `pyproject.toml`, or
in `.github/mutation-preflight.toml` for a repository without a
`pyproject.toml`. Use one, never both: a checkout that declares in both places
is refused, so there is exactly one source of truth. An unknown key is refused
too, so a typo cannot read as a requirement that is being checked.

The schema, per-phase rules and tested examples are in
[Mutation environment preflight](mutation-preflight.md). In brief:

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

## 3. Declare the checks a subprocess fixture needs

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

## 4. Run it locally

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

## 5. Read the diagnostics

The helper writes `mutation-preflight.json` and bounded per-check logs under
`checks/`. Both workflows upload the directory under `always()`:
`mutation-preflight-diagnostics-mutmut` for the mutmut lane, and
`mutation-preflight-diagnostics-<slug>-<shard>` for each cargo shard. The job
summary carries the same phases as a table. Each phase emits one
`mutation_preflight_phase` line on standard output, and every finding becomes
one `mutation_preflight_finding` line on standard error naming the phase it
came from. A failing run exits non-zero, so the mutation run never starts.

## 6. Fix the findings

Fix what each finding names: add the missing `also_copy` entry, install the
command, select an interpreter inside `requires-python`, pin the revision the
lock records, or repair the declared check. Re-run the helper locally until
every phase reports `pass`, `skipped` or `unknown`.

`pass` means the phase established what it checks. `skipped` means it does not
apply to this lane, or nothing was declared for it; on the mutmut lane `checks`
also reports `skipped` when the interpreter phase failed, and the reason is
recorded. `unknown` means the helper could not decide, and on its own does not
fail the run: supply the missing input when the check matters, such as
`project-python` on the cargo lane or a `supported_python` declaration for a
native dependency. An unknown mutmut release is reported the same way rather
than assumed to copy what a known release copies.

## 7. Keep it blocking

The input is blocking by design: any `fail` exits non-zero. Once the findings
are fixed, leave `preflight: true` on the default branch, so a requirement that
regresses stops the run before a mutant is enumerated. Setting the input back to
`false` restores the previous behaviour but gives up that guarantee.

## What does not need to change

A caller that leaves `preflight` at its default keeps the same job. The
baseline, mutant selection, result parsing and reporting are unchanged: the
preflight validates prerequisites and is not a replacement baseline.

The optional cache backend is also unchanged. A backend that is down keeps
using the existing `setup-rust` uncached fallback and publishes its status, so
a backend outage cannot turn a real baseline failure into a preflight failure,
or the reverse. Backend ownership, wrapper scope, runner placement and cache
limits are unchanged.

The declaration can stay in place even if the input is later disabled: it is
read only when the input is enabled, and nothing outside the job's workspace or
runner temporary directory is written.

## Migration checklist

- [ ] Decide which mutation callers adopt the preflight.
- [ ] Write one declaration, in `[tool.mutation-preflight]` or
      `.github/mutation-preflight.toml`, and not both.
- [ ] Name the commands, paths, native package and checks the run depends on.
- [ ] Supply `project-python` on the cargo lane where a native range is
      declared.
- [ ] Run the helper locally until every phase reports `pass`, `skipped` or
      `unknown`.
- [ ] Set `preflight: true` and confirm the diagnostics artefact is uploaded.
- [ ] Leave the input enabled on the default branch so a regression blocks.

## Common errors and fixes

- **Error:** `required command 'mdtablefix' is not on PATH; install it before
  the mutation run, or stop declaring it`
  - **Fix:** install the command in the job, or remove the declaration entry.

- **Error:** `required command 'mold' does not report '2.40.4': it printed
  'mold 2.41.0'`
  - **Fix:** pin the version the installed tool actually reports, or install the
    release the declaration names.

- **Error:** `sandbox path 'fixtures/data.json' is not materialized in
  mutants/: add it to [tool.mutmut] also_copy`
  - **Fix:** add the path to `[tool.mutmut] also_copy`, or point the declaration
    at a path mutmut already copies.

- **Error:** `the declared native dependency 'tei-rapporteur' is not in uv.lock`
  - **Fix:** run `uv lock` so the dependency is recorded, and check the declared
    name against the lock entry.

- **Error:** `uv.lock pins tei-rapporteur at revision 'abc123', which is not the
  declared revision 'def456'`
  - **Fix:** declare the revision the lock records, or relock at the revision
    the declaration names.

- **Error:** `the native probe for tei-rapporteur failed with exit code 1: <first
  diagnostic>`
  - **Fix:** repair the build or import the probe exercises, under the project
    interpreter the run selected.

- **Error:** `check 'collect' failed with exit code 2 from '.': <last line>`
  - **Fix:** repair the declared check, or correct its `cwd`, `env` or test
    selection so it runs the way the mutation run does.

- **Error:** `the repository '/path/to/project' is not a directory`
  - **Fix:** point `INPUT_REPOSITORY`, or the workflow's checkout, at the
    repository root.
