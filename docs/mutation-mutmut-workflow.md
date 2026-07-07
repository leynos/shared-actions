# Mutation testing reusable workflow (mutmut)

Runs scheduled, informational mutation testing for Python repositories
with [`mutmut`](https://mutmut.readthedocs.io/). A change-detection
guard makes quiet days a cheap no-op; scoped runs mutate only modules
whose files changed recently; manual dispatch runs mutate everything.
Results are reported in the job summary and as artefacts. The workflow
never gates pull requests.

## Prerequisites

The caller must be a **uv-managed project** (a `pyproject.toml` that
`uv run` can sync) with mutmut configured:

```toml
[tool.mutmut]
source_paths = ["src/"]
pytest_add_cli_args_test_selection = ["tests/"]
runner = "python -m pytest -x -q --no-header -p no:cacheprovider"
```

mutmut is injected at run time via `uv run --with mutmut==<pin>`, so it
must not be added to the project's own dependencies. pip-only projects
are not supported by this workflow's first release.

## Behaviour

- **Scheduled runs** scan commits reachable from `base-ref` within
  `window-hours` and translate changed `*.py` files into mutant-name
  globs (mutmut 3.x rejects file paths as run arguments): with the
  default `module-prefix-strip` of `src/`, `src/pkg/mod.py` scopes the
  run to `pkg.mod.*`. When nothing relevant changed, the run writes a
  skip message and finishes in seconds.
- **`workflow_dispatch` runs** bypass the guard and mutate everything
  in `source_paths`. There is no shard fan-out — mutmut has no shard
  equivalent — so size `timeout-minutes` to the suite.
- **Exit codes:** mutmut already exits 0 when mutants survive, so no
  masking is needed; a non-zero `mutmut run` indicates a failing
  baseline or usage error and fails the job.
- **Reports:** the job summary lists per-status counts (killed,
  survived, no tests, timeout, ...) and a survivors table; inspect a
  survivor locally with `uv run mutmut show <name>`. The raw results
  listing and `mutants/mutmut-stats.json` upload as the
  `mutation-report-mutmut` artefact.

## Required permissions

The caller job must grant:

```yaml
permissions:
  contents: read
  id-token: write # resolves the pinned workflow source via OIDC
```

## Usage

```yaml
name: Mutation testing

on:
  schedule:
    - cron: "45 4 * * *" # daily; guard makes quiet days a no-op
  workflow_dispatch:

concurrency:
  group: mutation-testing-${{ github.ref }}
  cancel-in-progress: false

jobs:
  mutation:
    permissions:
      contents: read
      id-token: write
    uses: leynos/shared-actions/.github/workflows/mutation-mutmut.yml@<pinned-sha>
    with:
      paths: "src/"
```

## Inputs

| Input | Default | Purpose |
| ----- | ------- | ------- |
| `paths` | `src/` | Path prefixes containing mutable Python source. |
| `window-hours` | `25` | Detection window; keep one hour wider than the cadence. |
| `base-ref` | `origin/main` | Reference scanned for changes. |
| `timeout-minutes` | `90` | Job ceiling. |
| `mutmut-version` | pinned | Tool version; the results parser is validated against it. |
| `module-prefix-strip` | `src/` | Prefix removed before module-glob translation. |
| `extra-args` | (empty) | Extra `mutmut run` arguments (shell-lexed). |
| `python-version` | `3.13` | Python for uv; must be **3.13 or newer** (the workflow's helper scripts require it — the job fails fast otherwise) and must satisfy the project's `requires-python` (e.g. set `"3.14"` for `>=3.14` projects). |

## Notes

- The `mutmut-version` default is pinned because the results output and
  configuration surface change between releases (3.6 renamed
  `paths_to_mutate` to `source_paths` and dropped file-path run
  arguments). Override only alongside a parser check.
- mutmut runs the suite inside a `mutants/` working copy that contains
  only `tests/`, `test/`, `pyproject.toml`, `setup.cfg`, root
  `test*.py`, and whatever `[tool.mutmut] also_copy` lists. Tests that
  read other repo-root paths fail the baseline there — notably a
  workflow contract test reading `.github/workflows/`. Guard such
  tests with a module-level
  `pytest.mark.skipif(not <path>.exists(), ...)`, exclude them from
  mutmut's test selection, or add the paths to `also_copy`. (Observed
  live on the estate's first scheduled polythene run.)
- "No tests" survivors mean mutmut found no covering test for the
  function; they are coverage gaps rather than assertion gaps.
- Some survivors are equivalent mutants; triage before turning the
  table into test tasks, and use `# pragma: no mutate` (with a comment)
  for deliberate suppressions.
- GitHub disables cron triggers after 60 days of repository
  inactivity; an acceptable failure mode for an informational workflow.

## Local validation

Guard-path integration tests run under [`act`] via the shared harness:

```sh
ACT_WORKFLOW_TESTS=1 make test
```

See `tests/workflows/test_mutation_workflows.py` and
`docs/local-validation-of-github-actions-with-act-and-pytest.md`. The
mutation-run path itself is covered by the `workflow_scripts` unit
tests, which fake the `uv`/mutmut boundary.

[`act`]: https://github.com/nektos/act
