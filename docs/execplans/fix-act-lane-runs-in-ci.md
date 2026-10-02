# Make the act lane run in CI, and fix what it finds

Branch: `fix/act-lane-runs-in-ci` (bottom of a two-PR stack; the top is
`issue-515-fix-generate-coverage-composite-input-handling-under-nektos-act`,
which becomes PR #516 re-targeted onto this branch. This branch's PR targets
`main`.)

## The request

> Please fix these in a PR stacked under this one using `/github-stacks` (the
> new PR should target main, and #516 should target the new PR). Ensure that
> the act tests are actually run.

"These" are the pre-existing failures in `make test-act`. "Actually run" means
`.github/workflows/ci.yml` must invoke the lane, because it never has.

## Commit gateways

Every commit must pass the full gateway set, run by `scrutineer` (the exclusive
gate-runner): `make check-fmt`, `make typecheck`, `make lint`, `make test`,
plus `make markdownlint` and `make nixie` for docs. CodeRabbit review via
`coderabbit review --agent` after each major milestone; on rate limit, sleep
`$(shuf -i 45-90 -n 1)` minutes with `vsleep` and retry.

## Findings so far (all reproduced, none inferred)

### F1 — act exports `ACT=true` unconditionally; it cannot be overridden

`nektos/act` v0.2.89 `pkg/runner/run_context.go:78-90`: `GetEnv()` builds
`rc.Env` from `mergeMaps(workflow.Env, job.Environment(), Config.Env)` and then
**unconditionally** writes `rc.Env["ACT"] = "true"`, mutating the shared map.
It is the only `"ACT"` assignment in act's tree.

Verified empirically on 0.2.89 (`~/.local/bin/act`, which the Makefile
prefers) and 0.2.88 (`~/go/bin/act`): `--env ACT=false`, workflow-level `env:`,
job-level `env:` and step-level `env:` all fail to change it inside a plain
step. Confirmed also for composite actions: `evaluateCompositeInputAndEnv`
(`pkg/runner/action_composite.go:15-40`) builds a fresh env from
`step.getEnv()` minus `INPUT_*`, and the composite `RunContext` carries no
`Job`, so only `ACT=true` is re-stamped.

**Consequence:** the `Resolve (OIDC fail-fast …)` step's `env: ACT: "false"`
(line 43 of `test-resolve-workflow-source.yml`) is inert under act.

### F2 — the OIDC half of `test-resolve-workflow-source.yml` is dead everywhere

Not just under act. On a real `workflow_dispatch`:
`actions/checkout` needs a token (job has `permissions: contents: read`, so it
has one), then step 1 `Resolve (act short-circuit)` runs with `ACT` unset hits
the OIDC branch, has no `ACTIONS_ID_TOKEN_REQUEST_URL` (job permissions are
`contents: read`, no `id-token: write`), and **fails the job** — so step 2 and
the OIDC assertion are never reached.

Independently confirmed this session by driving the real manifest fragment
through `composite_fragments.run_step` with `ACT` unset: `rc=1`, stderr
`OpenID Connect (OIDC) env vars not available…`, no outputs. With `ACT=true`:
`rc=0`, short-circuit fired. So no environment in which the wrapper is
triggered can execute it. CI never runs the fixture either: it is
`workflow_dispatch`-only and no workflow calls it.

**Consequence:** the `oidc-branch` step is unreachable in every environment;
the act lane's failure on it is real but the fixture's design is the cause.

### F3 — `install-whitaker` needs a Rust toolchain in the image

`catthehacker/ubuntu:act-latest` (Ubuntu 24.04.4) has python3, curl, git, tar
and **no cargo/rustc/rustup anywhere** (verified with `find / -xdev`).
`catthehacker/ubuntu:rust-latest` (3.09 GB) has cargo 1.97.1, rustc 1.97.1,
rustup, python3, curl, git, tar, node.

The installer extracts the pinned `cargo-dylint` archive, then probes
`cargo dylint --version`; without cargo the probe fails and, because
`--no-source-fallback` is passed, the run aborts with *"the repository install
failed verification"*.

Decisive experiment (`/tmp/wi_probe.py`, exit 0, 114.9 s): monkey-patching
`conftest._ACT_IMAGE` to the rust image made `install-whitaker` **pass** —
`whitaker_test_installation=complete`, `Whitaker installer::status=complete
version=0.2.9 suite=default-branch-tip suite-source=prebuilt`, no
`::error title=Whitaker`.

### F4 — CI can obtain a container runtime where it matters

`rust-toy-app.yml` runs `validate-linux-packages` on the **Ubicloud** arm,
whose first step is `sudo apt update -y && sudo apt install -y podman
bubblewrap proot mmdebstrap` (`.github/actions/validate-linux-packages/action.yml:79`).
So the Ubicloud image is Ubuntu 24.04 with sudo and apt, and installing rootless
podman there is an established pattern in this repository.

## Decisions

- **D1 — the act lane becomes its own `ci.yml` job**, `act-workflows`:
  `runs-on: ${{ github.event.pull_request.head.repo.fork && 'ubuntu-latest' ||
  'ubicloud-standard-2' }}`, `timeout-minutes: 30`.
  - The fork-fallback shape is required by `TestLinuxPlacementRule` because
    `ci.yml` carries `on: pull_request`; the Ubicloud arm is the one that can
    install podman, and the hosted arm is the fallback a fork lands on.
  - A tier of its own is **not** in `TIMEOUT_TIERS` (`assertion` 10, `install`
    15, `build` 20, `suite` 20, `coverage` 30). The lane's measured wall time
    is ~13 min on an idle 6-core host, and the job also installs podman and
    pulls two mult-gigabyte images, so `coverage` (30) is the only honest tier.
    `JOB_TIERS[("ci.yml", "act-workflows")] = "coverage"` with a comment
    recording the measurement.
- **D2 — the job installs act v0.2.89 through the repository's own
  `install-tool` action**, from a new `act` entry in
  `.github/tool-manifest.toml` (digests independently re-downloaded and
  verified this session, and they agree with upstream `checksums.txt`).
  Pinning v0.2.89 also neutralises the Makefile's `ACT ?=` preference for
  `~/go/bin/act` (0.2.88) on a runner that has neither.
  - `install-tool` appends its bin directory to `GITHUB_PATH`; the Makefile's
    `ACT ?=` uses `wildcard`, which cannot see it. So the job passes
    `ACT="$(command -v act)"` explicitly to `make test-act`.
- **D3 — the lane's image is `catthehacker/ubuntu:rust-latest`**, replacing
  `act-latest` in `conftest._ACT_IMAGE`. Required by F3. The consequence is
  recorded: the harness now runs act's steps on a fedora-ish Ubuntu image with
  a Rust toolchain, not on a GitHub-image replica; `act-latest` is an
  approximation either way, and this is the one that lets the lane test what it
  claims to.
- **D4 — the `resolve` fixture's OIDC half is repaired, not worked around.**
  The standing constraint forbids an expected-failure workaround, and F2 shows
  the step is dead in *every* environment rather than only under act, so the
  fix belongs in the fixture:
  - the `act-branch` step gets `if: env.ACT == 'true'` (the job-level `env:
    ACT: "false"` reaches it on a real dispatch, so it is skipped there and the
    short-circuit assertion is not asked for);
  - the OIDC-fail-fast step is given a front-loaded guard that cannot see the
    token endpoint, via `env: ACTIONS_ID_TOKEN_REQUEST_URL: ''` — an explicit
    empty env value *does* override the ambient one, unlike act's `ACT` write
    (which is an unconditional assignment rather than a default). Then the step
    fails fast in every environment, exactly once, and its `outcome` is
    `failure` as asserted;
  - the assertion step is kept unconditional (it must observe the outcome), and
    the OIDC assertion is reworded from `== "failure"` to a check that the
    outcome is `failure` **and** the step is not skipped, so a fixture that
    stopped exercising it fails rather than passes.
  This restores the fixture's stated purpose ("exercises the two branches
  reachable outside real GitHub infrastructure") and makes the lane green for a
  genuine reason.
- **D5 — a new contract holds the fixture to D4**, so the structural reason the
  OIDC branch is reachable (the empty-token-endpoint guard) cannot be dropped
  silently, and so the act-skip guard on the short-circuit step cannot be
  removed.

## Open questions / next steps

1. Write the `act` tool-manifest entry, then run
   `.github/actions/install-tool/tests/`.
2. Apply D3 (image), D4 (fixture), D5 (contract).
3. Add the `ci.yml` job per D1/D2; register `JOB_TIERS` and any policy maps the
   contracts demand.
4. Run the full lane to green; then the full gateway set via `scrutineer`.
5. Update docs: `docs/developers-guide.md` (runner-placement and ceiling
   tables) and `docs/local-validation-of-github-actions-with-act-and-pytest.md`
   (the three `-P ubuntu-latest=…` snippets).
6. Rebase the top branch (`issue-515-…`) onto this one — a `conftest.py`
   collision is expected — then `gh stack` the two branches, targeting `main`
   under this one and re-targeting #516 onto it.

## Evidence kept

- `/tmp/act-image-lane.out` — full lane against the rust image (first run, 20
  failures: 4 act cases plus 16 caused by running pytest through `.venv`
  directly instead of `uv run --with …`, which mismatched `hypothesis`).
  The 16 are an artefact of the invocation, not the code.
- `/tmp/act-lane-plain.txt` — the baseline lane on `act-latest`
  (`2 failed, 1062 passed, 102 skipped in 822.03s`).
- `/tmp/act-src/act-0.2.89/` — extracted act source, authority for F1.
