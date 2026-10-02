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
gate-runner): `make check-fmt`, `make typecheck`, `make lint`, `make test`, plus
`make markdownlint` and `make nixie` for docs. CodeRabbit review via
`coderabbit review --agent` after each major milestone; on rate limit, sleep
`$(shuf -i 45-90 -n 1)` minutes with `vsleep` and retry.

## Findings so far (all reproduced, none inferred)

### F1 — act exports `ACT=true` unconditionally into a *composite* action

**Corrected.** An earlier version of this finding claimed a step-level `env:`
cannot override `ACT` under act. That is false for a plain step, and the
correction matters.

What is true, measured with scratch workflows under act 0.2.89:

- A **plain** step's own `env:` **does** win over act's forced `ACT=true`, in
  both the container and that step's `if:` expression environment
  (`pkg/runner/step.go:setupEnv` merges step env last).
- Act feeds a step's own `env:` to **that step's own `if:`**. So
  `env: ACT: "no"` on a step guarded by `if: ${{ env.ACT != 'true' }}` makes
  the guard read `"no"`, the guard passes, and the step **runs**. This is a
  genuine trap, and it is why the repaired fixture sets no `env: ACT:` at all
  on the guarded steps.
- A **composite's** inner environment is rebuilt fresh and act re-stamps
  `ACT=true` unconditionally. Proven with a scratch composite: outer step
  `env: {ACT: "false", PROBE_TOKEN: "from-step"}` gave inner `INNER_ACT=[true]`
  but `INNER_TOK=[from-step]`. So an outer step's `env: ACT:` cannot reach the
  composite's script; other env values can.
- A plain step with **no** `env:` sees `env.ACT == 'true'` under act.

GitHub sets no `ACT` anywhere, so `env.ACT == 'true'` is true under act and
false on GitHub — which is what makes it usable as the branch selector.

### F5 — a missing container runtime made the whole lane pass

`pytest_runtest_setup` (tests/workflows/conftest.py) skipped every
`@skip_unless_act` case when the runtime probe failed, and pytest exits 0 for a
run that skipped everything. Reproduced: with
`DOCKER_HOST=unix:///nonexistent/podman.sock` and `ACT_WORKFLOW_TESTS=1`, the
lane reported `1 skipped` and **exit 0**.

**Consequence:** any CI job running `make test-act` on a runner whose runtime
the probe could not reach would go green having executed no fixture. This is
the same silent-skip failure the job exists to remove — reintroduced through
placement. Fixed: with the opt-in set, a failed probe now `pytest.fail`s;
without it, the plain suite still skips as before. Both arms are contracted in
`test_conftest_runtime.py`.

### F6 — the rust image's baked toolchain makes `setup-rust` fail under overlayfs

Found by the first full lane run under D3 (the image change). Two cases in
`test_rustflags_export_workflow.py` that passed on `act-latest` began failing
with `AssertionError: act failed:`.

`catthehacker/ubuntu:rust-latest` bakes its toolchain into
`/usr/share/rust/.rustup` (`RUSTUP_HOME` is set to it in the image's own
environment), and under act that path is a **lower** layer of the container's
overlay filesystem. The image's stable is rustc 1.97.1; upstream stable had
moved to 1.99.0 on 2026-10-01, so the nested
`actions-rust-lang/setup-rust-toolchain` took its *update* path and rustup
swapped the old toolchain's component directories by rename. Overlayfs refuses
a rename that crosses layers (this mount has no `redirect_dir`) and rustup
rolled back:

```text
error: could not rename 'component' file from
'/usr/share/rust/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/share/doc/clippy'
to '/usr/share/rust/.rustup/tmp/.../bk':
Invalid cross-device link (os error 18)
```

Reproduced directly in the image (583 ms, same error). **This is
time-dependent**: it appears whenever upstream stable is newer than the rolling
image's baked one, so it would have arrived on `act-latest` too, at the next
Rust release.

The failing job is `setup-rust-exports`; `setup-rust-toolchain-available`
passes because it uninstalls the baked toolchain first, so its install lands
wholly in the writable layer. That asymmetry is the evidence that the layer,
not the action, is at fault.

Fix: `RUSTUP_PERMIT_COPY_RENAME=1` in the lane's container environment
(`_LANE_CONTAINER_ENV` in `tests/workflows/conftest.py`, merged by
`_build_container_env` and overridable per case). It is rustup's own opt-in to
copy-and-delete instead of rename, present in the 1.29.0 binary. Measured:
without it the update fails in 583 ms; with it the same update completes
(1.97.1 → 1.99.0) and the job exits 0 with the expected
`setup_rust_rustflags=[-D warnings -C debuginfo=0]`.

### F7 — the resolve case's expectation outlived the fixture it describes

`test_resolve_workflow_source.py` still asserted `resolve_oidc_failfast=ok`,
which was the pre-D4 shape. Under D4 the OIDC half is *deliberately* skipped
under act, so the fixture prints `resolve_oidc_failfast=skipped` and the test
failed on a working fixture. Updated to assert the skip, and to assert the
fail-fast's diagnostic is *absent*: under act the branch is unreachable, so its
presence would mean the guards had stopped separating the halves.

A trap met while diagnosing: `grep -oE 'resolve_…'` over the pytest traceback
matches the *echoed assertion source*, not the logs, and appears to confirm
either marker. The logs in the traceback were also truncated mid-stream. The
direct `act` run was what settled it — and it also re-showed F5's shape from
the outside: invoked without `-P` for the job's label, act printed
`Skipping unsupported platform` and exited **0**.

### F2 — the OIDC half of `test-resolve-workflow-source.yml` is dead everywhere

Not just under act. On a real `workflow_dispatch`: `actions/checkout` needs a
token (job has `permissions: contents: read`, so it has one), then step 1
`Resolve (act short-circuit)` runs with `ACT` unset hits the OIDC branch, has no
`ACTIONS_ID_TOKEN_REQUEST_URL` (job permissions are `contents: read`, no
`id-token: write`), and **fails the job** — so step 2 and the OIDC assertion
are never reached.

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
`whitaker_test_installation=complete`,
`Whitaker installer::status=complete
version=0.2.9 suite=default-branch-tip suite-source=prebuilt`,
no `::error title=Whitaker`.

### F4 — CI can obtain a container runtime where it matters

`rust-toy-app.yml` runs `validate-linux-packages` on the **Ubicloud** arm,
whose first step is
`sudo apt update -y && sudo apt install -y podman bubblewrap proot mmdebstrap`
(`.github/actions/validate-linux-packages/action.yml:79`). So the Ubicloud
image is Ubuntu 24.04 with sudo and apt, and installing rootless podman there
is an established pattern in this repository.

## Decisions

- **D1 — the act lane becomes its own `ci.yml` job**, `act-workflows`:
  `runs-on: ${{ github.event.pull_request.head.repo.fork && 'ubuntu-latest' ||
  'ubicloud-standard-2' }}`,
  `timeout-minutes: 30`.
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
  verified this session, and they agree with upstream `checksums.txt`). Pinning
  v0.2.89 also neutralizes the Makefile's `ACT ?=` preference for
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
- **D4 — the `resolve` fixture is repaired by making its two halves
  mutually exclusive on `env.ACT`, not worked around.** The standing constraint
  forbids an expected-failure workaround, and F2 shows the OIDC branch was
  unreachable in *every* environment, so the fix belongs in the fixture.
  Implemented shape (differs from the earlier draft, which proposed an
  empty-token-endpoint `env:` guard — F1's correction explains why that was
  both unnecessary and dangerous):
  - the short-circuit half is guarded `if: ${{ env.ACT == 'true' }}`, so it
    runs under act and is skipped on a dispatch;
  - the OIDC fail-fast half is guarded `if: ${{ env.ACT != 'true' }}`, so it
    runs on a dispatch — where `ACT` is unset, the action's own `${ACT:-}`
    falls through, and it exits 1 on the missing token endpoint — and is
    skipped under act;
  - **neither guarded step declares `env: ACT:`**, because act feeds a step's
    own `env:` to that step's own `if:`, which would select the step the guard
    was written to skip (F1);
  - the final assertion step is unguarded and reports which half fired, using
    the literal `steps.<id>.outcome` strings act produces (`"success"` for a
    run step, `"skipped"` for a skipped one).
  Verified green under real act: exit 0, `resolve_act_branch=ok`,
  `resolve_oidc_failfast=skipped`.
- **D5 — a contract holds the fixture to D4**
  (`test_resolve_workflow_source_fixture.py`): it evaluates each guard against
  both runner environments and requires exactly one half to fire in each, that
  no guard is shadowed above its step, and that no step sets `ACT` for itself.
  Mutation-checked: restoring the old dead shape produced exactly 3 failures.
- **D6 — the lane cannot pass without a runtime** (F5). With the opt-in set, a
  failed runtime probe fails the run instead of skipping it. Without it, the
  plain suite skips as before, so a machine with no container runtime can still
  run the full suite.
- **D7 — the lane's containers run with `RUSTUP_PERMIT_COPY_RENAME=1`** (F6).
  The variable is set for every fixture through `_LANE_CONTAINER_ENV`, and a
  case may override it through `ActConfig.container_env` as before. It is
  rustup's own fallback for a filesystem that cannot rename across devices,
  which is what the image's baked toolchain sits on under act. The alternative
  — moving `RUSTUP_HOME` to a writable path — would change what the fixtures
  test by giving the container a rustup the action under test never sees.
- **D8 — the resolve case asserts the skip, not the fail-fast** (F7). The
  fail-fast is unreachable under act by construction (act sets `ACT=true`,
  which is the condition it is guarded against), so the case asserts the skip
  marker and the *absence* of the fail-fast's diagnostic. Both are evidence
  that exactly one half fired, which is what D4 and its contract require.

## Open questions / next steps

1. ~~Write the `act` tool-manifest entry, then run
   `.github/actions/install-tool/tests/`.~~ Done (`c82c629e`).
2. ~~Apply D3 (image), D4 (fixture), D5 (contract).~~ Done (`c82c629e`,
   `f25d458e`).
3. ~~Add the `ci.yml` job per D1/D2; register `JOB_TIERS` and any policy maps
   the contracts demand.~~ Done (`321534bc`).
4. ~~Run the full lane to green; then the full gateway set via `scrutineer`.~~
   **Lane green: `1079 passed, 104 skipped`, no failures** (823.69 s, log
   `/tmp/test-act-fix-act-lane-runs-in-ci.out`). Gateway set delegated to
   `scrutineer` at commit `4a904bc2`.
5. ~~Update docs: `docs/developers-guide.md` (runner-placement and ceiling
   tables) and `docs/local-validation-of-github-actions-with-act-and-pytest.md`
   (the three `-P ubuntu-latest=…` snippets).~~ Done (`3ed15d02`).
6. **Rebase the top branch (`issue-515-…`) onto this one, then `gh stack` the
   two branches.** Rebased: `7bc53417` → `ef0a3dbc`, 21 commits, both
   conflicts resolved as surveyed and the duplicate replayed empty. The
   `gh stack` half is next; the outcome is recorded below.

### The top-branch rebase, surveyed

The two branches were written against different `main` tips (bottom from
`ff1dd759`, top from `abf0dcf2`) and **independently contain the same Makefile
work**: bottom's `9c829353` and top's `21444c3e` share the title *"Gate the
plain suite off the act lane, and hold the recipes to it"*, and
`tests/workflows/test_makefile_act_lane_runs_once.py` is **byte-identical** on
both (235 lines, no diff). `git cherry` finds no patch-equivalent, so all 22
top commits will replay; the duplicates are textual, not detected.

Collisions by file, with the top's reason for touching each:

| File                                                             | Top commits                        | Expected shape                                                                                                                                             |
| ---------------------------------------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Makefile`                                                       | `21444c3e`, `3179704b`, `ba8cedc6` | `21444c3e` is the duplicate; the other two build on it. Bottom's version is the superset (it also carries main's `TYPOS_CONFIG_BUILDER_VERSION` `v0.1.3`). |
| `test_makefile_act_lane_runs_once.py`                            | `21444c3e`, `7bc53417`             | add/add, **identical blobs** — resolves to either side unchanged.                                                                                          |
| `test_job_ceilings.py`                                           | `d6ae259e`                         | disjoint: top adds the generate-coverage fixture's tier, bottom adds `act-workflows`.                                                                      |
| `docs/developers-guide.md`                                       | `d6ae259e`                         | disjoint regions (placement rule vs skip markers).                                                                                                         |
| `docs/local-validation-of-github-actions-with-act-and-pytest.md` | 4 commits                          | top edits the opt-in prose; bottom edits the `-P` image snippets. Both are wanted.                                                                         |

The duplicate commit is the one to watch: replaying it onto a tree that already
has the change should go empty, and an empty replay is a decision to record
rather than to force through (`--empty=stop` per the rebase skill).

### The top-branch rebase, done

Old head `7bc53417` → new head `ef0a3dbc`; exclusive boundary `abf0dcf2` →
target `5bc9e743`; 22 replayed, 21 committed, zero merges. Recovery refs
`refs/recovery/act-lane-top-{old-head,old-base}` and
`refs/recovery/act-lane-bottom-target` are retained. Logs:
`/tmp/rebase-shared-actions-fix-act-lane-runs-in-ci.out` and
`/tmp/range-diff-shared-actions-fix-act-lane-runs-in-ci.out`.

Two conflicts, both resolved as surveyed:

- **3/22, `ba8cedc6` (Makefile).** The incoming commit's inline pytest recipe
  collided with the bottom's `test: test-act` prerequisite. Took the bottom's
  superset; the commit's unique contribution
  (`tests/workflows/test_action_behaviours.py`) was untouched and replayed as
  `858c19e9` (1 file changed, 26 insertions).
- **21/22, `21444c3e` (add/add on
  `tests/workflows/test_makefile_act_lane_runs_once.py`).** Stage 2 was the
  bottom's `2bb808f8`, stage 3 the duplicate's earlier `4af239d4`. Resolved to
  `2bb808f8`; the Makefile auto-merged to the bottom's blob. The commit then
  replayed **empty** and was dropped at `--continue`, exactly as surveyed —
  the whole change it carried is already in the bottom, so an empty replay is
  the honest outcome and nothing was forced through. Its later top-side
  refinements (`7bc53417`) survive, since that commit's content is already the
  bottom's blob.

Range-diff audit: 18 of the 21 entries are patch-identical; the three that
differ — 3, 11, and the old tip's replay (`7bc53417` → `ef0a3dbc`) — differ
only by hunks the bottom already carries. No commit is missing other than the
dropped duplicate. Every bottom-only file (24 of them) is byte-identical at
the new head; the top's six new files are all present; no unexplained
deletions; `git diff --check` clean. Both sides' content is present in each
overlap file: `test_job_ceilings.py` carries `act-workflows` *and*
`test-generate-coverage-out-no-suffix`, `developers-guide.md` carries the
placement rule and the lane's ceiling, and the act guide carries both the
opt-in prose and the `-P ubuntu-latest=catthehacker/ubuntu:rust-latest`
snippets.

The new head is a linear descendant of the bottom (`git merge-base
--is-ancestor 5bc9e743 ef0a3dbc`), which is what the stack requires. The top
touches no `docs/execplans/` file, so this record can be amended on the bottom
without disturbing it.

## Evidence kept

- `/tmp/act-lane-plain.txt` — the baseline lane on `act-latest`
  (`2 failed, 1062 passed, 102 skipped in 822.03s`), failing
  `test_simple_workflow_validation[install-whitaker]` and
  `test_resolve_workflow_source_branches`.
- `/tmp/test-act-fix-act-lane-runs-in-ci.out` — the green lane
  (`1079 passed, 104 skipped in 823.69s`).
- `/tmp/act-image-lane.out` — first lane against the rust image (20 failures:
  4 act cases plus 16 caused by running pytest through `.venv` directly instead
  of `uv run --with …`, which mismatched `hypothesis`; the 16 are an artefact
  of the invocation, not the code).
- `/tmp/test-rustflags-after-fix.out` — the rustflags module green (6 passed).
- `/tmp/test-resolve-after-fix.out` — the resolve case green.
- `/tmp/act-src/act-0.2.89/` — extracted act source, authority for F1.
- `/tmp/rebase-shared-actions-fix-act-lane-runs-in-ci.out` — the top-branch
  rebase: both conflicts and their resolutions, and the drop of the empty
  duplicate.
- `/tmp/range-diff-shared-actions-fix-act-lane-runs-in-ci.out` — the
  old-series/new-series audit (21 entries; 3, 11 and 21 differ only by hunks
  the bottom already carries).

## Findings and decisions index

| #   | Finding                                                                                | Decision |
| --- | -------------------------------------------------------------------------------------- | -------- |
| F1  | act exports `ACT=true` into a composite, and feeds a step's own `env` to its own `if:` | D4, D5   |
| F2  | the resolve fixture's OIDC half is dead in every environment                           | D4, D5   |
| F3  | `act-latest` ships no Rust toolchain                                                   | D3       |
| F4  | Ubicloud runners can install a container runtime                                       | D1       |
| F5  | a failed runtime probe made the whole lane exit 0 having run nothing                   | D6       |
| F6  | the rust image's baked toolchain cannot be renamed across overlay layers               | D7       |
| F7  | the resolve case's expectation outlived the fixture it describes                       | D8       |

## The stack, once the rebase lands

`gh stack link` pushes its branch arguments **non-force** and keeps no local
tracking, so on its own it cannot publish the rewritten top layer — that push
would be rejected. `gh stack submit` pushes every branch with
`--force-with-lease`, which is what a rewritten layer needs. The path is:

1. `gh stack init fix/act-lane-runs-in-ci issue-515-…` — adopt both branches
   into local tracking, bottom first. No push.
2. `gh stack submit --auto --open` — push both layers (the top with
   `--force-with-lease`), create the bottom PR against `main`, correct #516's
   base onto the bottom branch, and create the stack object.
3. `gh stack view --json` — verify the chaining.

Never a hand-rolled `git push --force`: `submit` and `push` bind each lease to
the head the remote actually has. The rebased top is ungated so far; run the
full gateway set on the final head before `submit` publishes it.
