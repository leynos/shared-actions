# Mutation environment preflight

Checks a repository's mutation environment once, before any mutant is
enumerated, and reports every phase as a machine-readable envelope whether the
mutation results exist or not.

## Why it exists

Every failure recorded in issue #591 happened before a mutant was exercised:

| Failure                                 | What it looked like                      |
| --------------------------------------- | ---------------------------------------- |
| A copied sandbox lacked a file          | baseline error inside `mutants/`         |
| The interpreter was out of range        | build or import error after a long setup |
| An external command was never installed | baseline error, attributed to a mutant   |
| A native dependency was unsupported     | an ABI build error after a full baseline |

Each was repaired one `also_copy` entry, one `apt-get` line or one version pin
at a time, so the same class of failure kept returning. The repair here is a
declaration the repository owns and one helper that checks it, rather than a
new installer per repository or a per-repository copy of the checks.

## Opting in

Both reusable workflows expose `preflight`, default `false`, so no existing
caller starts checking requirements it has not declared:

```yaml
jobs:
  mutation:
    uses: leynos/shared-actions/.github/workflows/mutation-mutmut.yml@<sha>
    with:
      preflight: true
```

The mutmut lane runs the preflight after change detection (an unchanged day
costs nothing) and before the mutation run. The cargo lane runs it per shard,
because the commands a caller declares are installed by steps in that job;
preflighting anywhere else would inspect a different environment from the one
the mutants run in.

## Where the declaration lives

One declaration per repository, never both:

- `[tool.mutation-preflight]` in the caller's `pyproject.toml`; or
- `.github/mutation-preflight.toml` for a repository without a
  `pyproject.toml`.

Declaring in both places is refused rather than ranked, because which one binds
the run would otherwise be a fact about the helper's precedence rather than the
repository's intent. Unknown keys are refused too, so a typo cannot read as a
requirement that is being checked when it is not.

A repository that declares nothing is not a failure: the manifest phase reports
`skipped`, the interpreter phase still runs, and the remaining phases report
`skipped`.

## Schema

### `sandbox_paths` (mutmut lane only)

Paths that must survive into mutmut's `mutants/` copy. Each entry is:

- relative to the checkout (an absolute path or one containing `..` is
  refused);
- present in the checkout (a missing path is a finding, not a pass);
- not inside build output or a cache (`.venv`, `target`, `.pytest_cache`,
  `mutants` and similar are refused);
- either covered by mutmut's default copy set, by a root-level `test*.py`
  glob, or by an `also_copy` entry.

The last rule is the one that matters: the phase compares the declaration
against what mutmut will **actually copy**, not against what exists. mutmut
3.6.0 copies `tests/`, `test/`, `setup.cfg`, `pyproject.toml`, root `test*.py`,
`source_paths` and `also_copy` — nothing else. A path that exists but is not
copied fails here, with the reason spelled out, instead of failing the baseline
inside the sandbox.

```toml
[tool.mutation-preflight]
sandbox_paths = [
  "conftest.py",
  ".github/workflows",
  "src/pkg/_generated/sibling.py",
  "rust-toolchain.toml",
]
```

Because `.github/workflows` and a generator sibling are not in the default copy
set, each needs a matching `also_copy` entry; hidden trees are not special
cased, they are copied only when declared.

`source_paths` count as copied, so a package's own `src/` tree can be declared
if a test reads a sibling module through a path rather than an import.

The cargo lane copies the whole working tree, so `sandbox_paths` is refused
there; declare the tools the run needs instead.

### `require_commands`

External commands the run invokes, and optionally the version each must report:

```toml
[[tool.mutation-preflight.require_commands]]
name = "mold"
version = "2.40.4"

[[tool.mutation-preflight.require_commands]]
name = "mdtablefix"
version_args = ["--version"]
```

- `name` is resolved through `PATH`. A command that does not resolve is a
  finding naming the command and suggesting the install or the removal of the
  declaration.
- `version` is matched as a whole dotted version token in the command's
  output, so `2.4` rejects `2.41.0` while accepting `2.4.1`.
- `version_args` defaults to `["--version"]`.

### `native`

A locked native dependency whose interpreter support the run depends on:

```toml
[tool.mutation-preflight.native]
package = "tei-rapporteur"
revision = "a1b2c3d4"
supported_python = ">=3.9,<3.15"
probe = ["python", "-c", "import tei_rapporteur"]
```

- `package` is looked up in `uv.lock` case- and separator-insensitively
  (`tei-rapporteur` matches `tei_rapporteur`). A package the lock does not
  contain fails the phase.
- `revision`, when declared, must appear in the locked source, so the lock is
  known to pin the revision whose support was established.
- `supported_python`, when declared, is a PEP 508 range judged against the
  project interpreter. Leaving it out does not pass: it reports `unknown`,
  naming the package, because compatibility that cannot be established is not
  compatibility that holds.
- `probe` is the smallest build/import check that exercises the extension. It
  runs from the checkout root after the lock identity is checked, and a
  non-zero exit fails the phase with the first diagnostic the probe printed.
- `PYO3_USE_ABI3_FORWARD_COMPATIBILITY` is reported but never treated as proof
  of support; the ABI forward-compatibility escape hatch skips a version check
  rather than passing one.

The detail records the dependency identity and revision, the interpreter range
and the probe's first diagnostic, so a failure names the pinned package and the
support boundary before any mutant is enumerated.

### `checks`

Bounded commands run from a declared boundary:

```toml
[[tool.mutation-preflight.checks]]
name = "subprocess-fixture"
command = ["python", "-m", "pytest", "-q", "tests/test_cli.py", "-k", "subprocess"]
cwd = "tests"
timeout_seconds = 120
env = { PYTHONPATH = "..:/declared" }
```

This is the contract a subprocess or nested-pytest fixture needs, made explicit:
`cwd` is the working directory, `env` is layered over the ambient environment,
and a relative `PYTHONPATH` (resolved against `cwd` by the child) is how the
fixture declares its import root. `cwd` must be a directory inside the checkout
and is subject to the same escape rules as `sandbox_paths`.

`timeout_seconds` defaults to `300` and is capped at `900`, so a hung command
cannot outlive the job. Each check's first 4 KiB of each stream is kept; later
bytes are dropped and counted.

An installed-wheel smoke check and an in-process mutant attribution are
distinct checks: declare the wheel smoke check here, and leave mutant
attribution to mutmut. Never add mutmut to the project's production
dependencies to make either work.

## Phases

| Phase         | What it establishes                                                                                                                |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `manifest`    | The declaration parses, names only known keys, and asks nothing the lane cannot check.                                             |
| `interpreter` | The project interpreter satisfies `requires-python`; the helper runtime is reported separately and held to its own 3.13 floor.     |
| `native`      | The locked native dependency is the declared revision and the project interpreter is in range; unestablished support is `unknown`. |
| `sandbox`     | Every declared path exists, stays inside the checkout, is not build output, and is one mutmut will copy.                           |
| `commands`    | Every required command resolves and reports the declared version.                                                                  |
| `checks`      | Each bounded command succeeds from its declared working directory, environment and import root.                                    |

Each phase reports one of four statuses: `pass`, `fail`, `skipped` (not
applicable, or nothing declared for it) or `unknown` (could not decide; says so
rather than guessing). A run fails only on `fail`; `unknown` is surfaced but
does not block.

### Interpreter separation

The helper's runtime and the project's runtime are different questions and are
reported separately:

- `helper_python` — the interpreter running the preflight. The workflow helpers
  require 3.13 or newer; below that is a finding.
- `project_python` — the interpreter the caller selected for the project. It is
  judged against the project's `requires-python`, using the same rule the
  cv005-contracts coverage clause uses (#565), so a bare `X.Y` is accepted only
  when some patch release of that minor satisfies the range.
- `same_runtime` — whether the two are the same interpreter, reported rather
  than assumed.

The coverage clause's contract covers its own callers; it does not, by being
reused here, cover mutation callers. Mutation callers get this phase.

When `INPUT_PROJECT_PYTHON` is not supplied, the phase reports `unknown` rather
than failing: a lane that does not know its interpreter cannot be told it chose
wrongly.

## Diagnostics

Every phase emits one `mutation_preflight_phase` line, then each finding as
`mutation_preflight_finding` on standard error, then `mutation_preflight_result`
(`pass` or `fail`). The lines are emitted as each phase finishes, so a crash
in a later phase still leaves the earlier verdicts in the log — the envelope
does not depend on mutation results existing.

The output directory receives:

- `mutation-preflight.json` — lane, both interpreters, the verdict, and every
  phase's status, findings and detail;
- `checks/<check>.stdout.log` and `checks/<check>.stderr.log` — bounded, so a
  noisy command cannot make the diagnostics unreadable.

The Markdown summary is appended to the job summary when `GITHUB_STEP_SUMMARY`
is set. Both workflows upload the directory under `always()`, because a failed
preflight is exactly the case whose diagnostics need publishing.

The environment variables a workflow sets are `INPUT_LANE`,
`INPUT_PROJECT_PYTHON`, `INPUT_HELPER_PYTHON`, `INPUT_REPOSITORY` and
`INPUT_OUTPUT_DIR`. The helper can be run by hand the same way:

```sh
INPUT_LANE=mutmut INPUT_PROJECT_PYTHON=3.13 \
  uv run --script workflow_scripts/mutation_preflight.py
```

## Limits

The preflight validates prerequisites; it is not a baseline. Collection and the
unmodified baseline still come from the mutation tool, run from the same
sandbox, working directory, interpreter, environment and test selection, and
reported once rather than twice.

Required dependencies fail a named phase. An optional cache backend does not: a
cache backend that is down keeps using the existing `setup-rust` uncached
fallback and publishes its status, so a backend outage cannot turn a real
baseline failure into a preflight failure, or the reverse. Backend ownership,
wrapper scope, runner placement and cache limits are unchanged by this check.

Roll out one sandbox canary and one representative native consumer before
pinning the input across consumers; the failure classes are shared, but the
declarations are not, and an estate-wide build campaign is not needed to
validate a declaration.
