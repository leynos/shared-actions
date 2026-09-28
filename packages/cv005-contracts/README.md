# cv005-contracts

`cv005-contracts` holds the estate's CodeScene coverage contract (CV-005) and
the `codescene` environment contract in one place. A repository runs them
against its own workflows with one command. It never copies the rules.

Main owns CodeScene. One push-to-main publisher uploads coverage and writes
the ratchet baseline. Nothing a pull request can start talks to CodeScene or
holds its credential. Only the uploading job may declare the `codescene`
environment.

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

| Status | Meaning                                                                                      |
| ------ | -------------------------------------------------------------------------------------------- |
| 0      | Every selected contract holds.                                                               |
| 1      | At least one clause is broken. Each violation is printed as one `clause: message` line.      |
| 2      | The tree or the configuration could not be read, such as a duplicate key or a missing file. |

A reading failure is never a pass. `--only` runs a subset of the families
`pull-request`, `publisher`, `token`, `coverage` and `environment`, for
example `cv005-contracts check --only environment`.

## Configuration

Each repository keeps its parameters in `.github/cv005.toml`. Only
`repository` is required. An unknown key is refused, so a misspelt parameter
cannot fall back to its default.

```toml
repository = "leynos/example"    # required; used to refuse this repository's own calls at a ref
publisher = "coverage-main.yml"  # the publisher's file name
interpreter = "3.13"             # the Python version every generator pins through UV_PYTHON;
                                 # omit it where the repository measures no Python
environment = true               # hold the `codescene` environment contract
[selection]                      # generator inputs the publisher must carry exactly
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
  secrets wholesale, or reads the `secrets` context without naming one
  secret.
  - Seeding fails closed: every event outside `release`, `schedule`,
    `workflow_call`, `workflow_dispatch`, `workflow_run` and `push` seeds the
    closure. `workflow_run` workflows are seeded too, and so is a push not
    confined to `main` or tags.
- **Publisher.** It is triggered by a push to `main`, with dispatch allowed,
  and has workflow permissions `{}`.
  - Concurrency: one ref-keyed group that never cancels, at one scope only.
  - The upload runs in upload mode, reading the report its own job wrote
    earlier, from the same commit pin as the generator.
  - Nothing in the publisher may carry `continue-on-error`. Only the upload
    step may carry an `if:`.
- **Token.** A check step runs exactly
  `echo "available=${{ secrets.CS_ACCESS_TOKEN != '' }}" >> "$GITHUB_OUTPUT"`.
  The upload runs only when that output is `true` and the ref is main, and it
  passes the secret straight to `access-token`. The token appears in no
  `env`.
- **Coverage lanes.**
  - Each pull-request lane ratchets and publishes no artefact.
  - Each lane measures the publisher's selection under the same merged
    `env`: workflow, then job, then step.
  - Each lane pins the configured interpreter.
  - Each lane runs read-only and cannot continue on error.
  - Only the publisher writes the baseline on a push.
- **Least privilege.** The upload job's permissions are exactly
  `contents: read`, and its checkouts set `persist-credentials: false`.
- **Environment.** Every uploading job declares `codescene`, in either form
  and in any case. No other job declares it, and no job a pull request can
  reach declares it. A job whose environment name is an expression is
  refused, because its placement cannot be proved.

`environment: codescene` refuses a branch `workflow_dispatch` of the
publisher for the whole job, rather than skipping the upload.

## Developing the library

The library has its own lane, `.github/workflows/test-cv005-contracts.yml`,
which runs only when this package or that workflow changes. Library changes
land alone, without action changes in the same pull request. From this
directory:

```sh
make check-fmt lint typecheck test   # the gates
make mutation-ledger                 # prove every clause by mutation
```

`tests/mutations.toml` records one exact substitution per clause, and the
test that must fail once it is applied. `scripts/mutation_ledger.py` applies
each substitution to a fresh copy of the package in a temporary directory, so
the working tree is never edited. It refuses an anchor that does not occur
exactly once.

Every new clause needs three things:
- a breaching case;
- a narrow case, showing the rule still accepts the shape it must accept;
- a ledger entry.
