# cv005-contracts

`cv005-contracts` holds the estate's CodeScene coverage contract (CV-005) and
the `codescene` environment contract in one place. A repository runs them
against its own workflows with one command. It never copies the rules.

Main owns CodeScene. One push-to-main publisher uploads coverage and writes the
ratchet baseline. Nothing a pull request can start talks to CodeScene or holds
its credential. Only the uploading job may declare the `codescene` environment.

## Running it

Pin a full shared-actions commit in one Makefile variable, and run the check
from the repository's workflow-contract target:

```make
CV005_CONTRACTS_REF ?= <40-hex shared-actions commit>
CV005_CONTRACTS = $(UV) tool run --python 3.13 \
  --from 'git+https://github.com/leynos/shared-actions@$(CV005_CONTRACTS_REF)#subdirectory=packages/cv005-contracts' \
  cv005-contracts

test-workflow-contracts:
	$(CV005_CONTRACTS) check --repository .
```

The command's exit status tells a violation from a fault:

| Status | Meaning                                                                                     |
| ------ | ------------------------------------------------------------------------------------------- |
| 0      | Every selected contract holds.                                                              |
| 1      | At least one clause is broken. Each violation is printed as one `clause: message` line.     |
| 2      | The tree or the configuration could not be read, such as a duplicate key or a missing file. |

A reading failure is never a pass. `--only` runs a subset of the families
`pull-request`, `publisher`, `token`, `coverage` and `environment`, for example
`cv005-contracts check --only environment`.

## Configuration

Each repository keeps its parameters in `.github/cv005.toml`. Only `repository`
is required. An unknown key is refused, so a misspelt parameter cannot fall
back to its default.

```toml
repository = "leynos/example"    # required; used to refuse this repository's own calls at a ref
publisher = "coverage-main.yml"  # the publisher's file name
interpreter = "3.13"             # the Python version every generator pins through UV_PYTHON;
                                 # omit it where the repository measures no Python
environment = true               # hold the `codescene` environment contract
[selection]                      # inputs each ratcheting publisher generator must carry
language = "python"
format = "cobertura"
```

The rest is estate policy and lives in the rules. That includes the check
step's command, the upload guard, the concurrency group, the permissions and
the retired checksum names.

## What it holds

- **Pull-request surface.** No workflow a pull request can start, directly
  or through local reusable workflows and local composite actions, names the
  CodeScene host, the credential, the client or the uploader. None forwards
  secrets wholesale, or reads the `secrets` context without naming one secret.
  - Seeding fails closed: every event outside `release`, `schedule`,
    `workflow_call`, `workflow_dispatch`, `workflow_run` and `push` seeds the
    closure. `workflow_run` workflows are seeded too, and so is a push not
    confined to `main` or tags.
- **Publisher.** It is triggered by a push to `main`, with dispatch allowed.
  A job declaring no `permissions` of its own inherits the workflow's. It is
  refused when the workflow declares none, since it then holds the default
  token, or when the workflow's grant can write.
  - Concurrency: one ref-keyed group that never cancels, at one scope only.
  - The upload runs in upload mode (`mode` absent, or `upload`; any other
    value is refused), reading the report its own job wrote
    earlier, from the same commit pin as the generator. An omitted `path` or
    `format` reads as the action's default: `cobertura`, and `coverage.xml`
    or, for `lcov`, `lcov.info`. A report merged from several legs counts
    when an earlier `run` step, after a generator of the same format,
    redirects its output to the uploaded file.
  - No publisher job may carry `if` or `continue-on-error`, and neither may
    the `generate-coverage` steps; the upload step may carry `if:` (its guard
    is asserted) but not `continue-on-error`. Other steps may carry both, but a
    report-merging `run` step that carries either does not count as the merge.
- **Token.** A check step runs exactly
  `echo "available=${{ secrets.CS_ACCESS_TOKEN != '' }}" >> "$GITHUB_OUTPUT"`.
  The upload runs only when that output is `true` and the ref is main, and it
  passes the secret straight to `access-token`. The token appears in no `env`.
  - The check step is the one whose `available` output the upload guard
    reads, under any id (`codescene-token` or `codescene-credential`). The
    guard reads exactly one such output.
  - A `defaults.run` on the workflow or the upload job may name only a
    `bash` or `sh` shell and a working directory, which leave the command as
    written.
- **Coverage lanes.**
  - Each job measuring coverage, in a lane or the publisher, ratchets
    exactly one of its legs. The action keys the baseline by job.
  - Lanes publish no artefact and never set `publish-baseline`.
    A lane step uploading with `actions/upload-artifact` is judged by what
    each `path` line could select, not by whether it names the report: the
    workspace (`.`, `./`), a path climbing out with `..`, a directory holding
    the report, a glob matching it or a directory above it, an absolute or
    `~` path outside `/tmp/`, and an expression that is not wholly quoted
    `/tmp/` literals are all refused. A negation (`!path`), a directory or
    glob elsewhere, and a `/tmp/` path are not.
  - Each lane leg measures the selection of one publisher leg: the same
    inputs, and the same merged `env` (workflow, then job, then step).
    Inputs that name, ship or save the report are not compared. Nor are
    environment keys that pin or place another tool: names ending
    `_VERSION`, `_REV` or `_SHA256` (with any platform suffix), uv's tool and
    cache directories, and Cargo's network retry settings.
  - Each generator, in a lane or the publisher, pins the configured
    interpreter through `UV_PYTHON`, and no other source may name another:
    - `3.13` and `3.13.0` are the same version; `3.13.1` is not.
    - An empty `UV_PYTHON` on a step or job masks the pin an outer scope
      set, because the action reads an empty value as unset.
    - The `python-version` input outranks `UV_PYTHON`, so it must agree.
    - `setup-python` steps earlier in the same job must agree. A repeat that
      is guarded by `if:` or may fail green counts as well as the reliable
      setup before it, since either version may be on `PATH`. A guarded
      setup with no reliable one before it claims nothing.
    - A `generate-coverage` from another owner or repository at the same
      path is not the shared action and is not judged as one.
  - The configured interpreter must sit inside the project's `requires-python`
    where `pyproject.toml` declares one, since `uv sync` refuses an interpreter
    the project excludes. A bare `3.13` is accepted if any 3.13 patch is; a
    declaration that cannot be read or judged is refused.
  - Each lane runs read-only and cannot continue on error.
  - Only the publisher writes the baseline on a push.
- **Least privilege.** The upload job's permissions are exactly
  `contents: read`, and its checkouts set `persist-credentials: false`.
- **Environment.** Every uploading job declares `codescene`, in either form
  and in any case. No other job declares it, and no job a pull request can
  reach declares it. A job whose environment name is an expression is refused,
  because its placement cannot be proved.

`environment: codescene` refuses a branch `workflow_dispatch` of the publisher
for the whole job, rather than skipping the upload.

## Declaring what the estate rule cannot express

Two things go in `.github/cv005.toml`, and neither passes silently.

An **exception** waives one clause on the record. It names the ruling that
allows it and the reason, and may name a command the publisher must run in its
place. whitaker measures by `make coverage`, which the shared action cannot run:

```toml
[[exception]]
clause = "coverage.pull-request-lane"   # also publisher.wiring, coverage.selection-parity
ruling = "leynos/whitaker#444"
reason = "Coverage comes from `make coverage`, not generate-coverage."
requires = "make coverage"              # a publisher `run` step must contain this
```

The waived findings are printed under the exception on every run. An exception
is itself a finding when its clause is unknown, when it waives nothing (stale),
or when the publisher runs no step containing `requires`. One exception waives
one clause, never a family.

A **pairing** maps a lane leg to the publisher leg it ratchets against, where a
matrix job or a separate baseline job defeats inspection. A leg is named
`workflow-file:job-id:step-name`. rstest-bdd's Windows lanes are one:

```toml
[[pairing]]
lane = "ci.yml:build-test:Coverage (Windows, strict)"
publisher = "coverage-main.yml:coverage-baseline-windows:Generate coverage"
differs = ["features", "with-default-features"]   # exactly where the legs differ
guards = ["runner.os == 'Windows'", "matrix.features != ''"]  # besides the pull-request guard
[pairing.matrix]                                  # the cell whose values the leg reads
features = "strict"
with-default-features = "false"
```

The library checks that both legs exist; that the matrix cell exists in the
job's matrix and every value given is read; that the two legs differ on exactly
the keys in `differs` once the cell's values are read in; and that the leg's
`if:` is the pull-request guard plus exactly `guards`. Legs of one job count as
one ratchet only when their `guards` contradict pairwise, that is, compare one
operand with two different literals or with `==` in one and `!=` in the other,
since only then can at most one run in any cell. A pairing with matrix values
must declare `guards`, or nothing would restrict the leg to its cell, unless
the leg runs in every cell and takes only `with-ratchet` from the matrix
(`with-ratchet: ${{ matrix.ratchet }}`): the cells then differ in that flag
alone, the declared cell is read in, and the leg counts as the job's ratchet
where it ratchets there. The cell must survive the matrix's `exclude`, which
applies before `include`. A report-merging `run` step counts only when it
carries no `if` and no `continue-on-error`. A lane leg no pairing names falls
back to the estate rule, which refuses it unless its selection equals a
publisher leg's.

## Developing the library

The library has its own lane, `.github/workflows/test-cv005-contracts.yml`,
which runs only when this package or that workflow changes. Library changes
land alone, without action changes in the same pull request. From this
directory:

```sh
make check-fmt lint typecheck test   # the gates
make mutation-ledger                 # prove every clause by mutation
```

`tests/mutations.toml` records one exact substitution per clause, and the test
that must fail once it is applied. `scripts/mutation_ledger.py` applies each
substitution to a fresh copy of the package in a temporary directory, so the
working tree is never edited. It refuses an anchor that does not occur exactly
once.

Every new clause needs three things:

- a breaching case;
- a narrow case, showing the rule still accepts the shape it must accept;
- a ledger entry.
