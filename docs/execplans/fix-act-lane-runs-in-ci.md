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

### F4 — CI has a container runtime on both arms

**Corrected.** An earlier version of this finding only said the Ubicloud arm
*could install* a runtime, citing the podman line in `validate-linux-packages`
(`.github/actions/validate-linux-packages/action.yml:79`). The truth is
stronger, and it is why the job installs nothing.

- Ubicloud's x64 runner images are generated from GitHub's own packer
  templates and are, in Ubicloud's words, "fully compatible with default
  runners"
  ([runner types](https://www.ubicloud.com/docs/github-actions-integration/runner-types));
  `ubicloud-standard-2` is Ubuntu 24.04, the same image family GitHub's
  `ubuntu-latest` resolves to. GitHub's ubuntu-24.04 image ships `docker-ce`
  with the daemon running, so both arms of the fork-fallback `runs-on` have
  Docker.
- The probe prefers Docker: `_docker_cli_available()` requires `docker info`
  and `docker ps -a` to succeed, and podman is only consulted when the Docker
  CLI is absent (`tests/workflows/conftest.py:246-278`). So the podman install
  pattern exists but is the fallback, not the path.
- Installing podman in the job would therefore be dead weight on the common
  arm and, worse, would suggest the lane needs provisioning it does not. D6
  already makes a runtime the probe cannot reach a loud failure, so a runner
  without Docker fails with the probe's reason rather than skipping into a
  green job. The first CI run died at `Install act` (F8) before the lane step,
  so the probe has not yet run on either arm; the re-run the fixes trigger is
  its first real exercise.

## Decisions

- **D1 — the act lane becomes its own `ci.yml` job**, `act-workflows`:
  `runs-on: ${{ github.event.pull_request.head.repo.fork && 'ubuntu-latest' ||
  'ubicloud-standard-2' }}`,
  `timeout-minutes: 30`.
  - The fork-fallback shape is required by `TestLinuxPlacementRule` because
    `ci.yml` carries `on: pull_request`; both arms carry a Docker daemon
    (F4), so the lane runs on either, and the hosted arm is the fallback a
    fork lands on.
  - A tier of its own is **not** in `TIMEOUT_TIERS` (`assertion` 10, `install`
    15, `build` 20, `suite` 20, `coverage` 30). The lane's measured wall time
    is ~13 min on an idle 6-core host, and the job pulls two multi-gigabyte
    images, so `coverage` (30) is the only honest tier.
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
  recorded: the harness now runs act's steps on the same Ubuntu 24.04 base as
  `act-latest` with a Rust toolchain added, not on a GitHub-image replica;
  `act-latest` is an approximation either way, and this is the one that lets
  the lane test what it claims to.
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
   two branches.** Rebased, then restacked onto this record's own commit; 21
   commits replay patch-identically each time. The `gh stack` half is next; the
   outcome is recorded below.

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
  replayed **empty** and was dropped at `--continue`, exactly as surveyed — the
  whole change it carried is already in the bottom, so an empty replay is the
  honest outcome and nothing was forced through. Its later top-side refinements
  (`7bc53417`) survive, since that commit's content is already the bottom's
  blob.

Range-diff audit: 18 of the 21 entries are patch-identical; the three that
differ — 3, 11, and the old tip's replay (`7bc53417` → `ef0a3dbc`) — differ
only by hunks the bottom already carries. No commit is missing other than the
dropped duplicate. Every bottom-only file (24 of them) is byte-identical at the
new head; the top's six new files are all present; no unexplained deletions;
`git diff --check` clean. Both sides' content is present in each overlap file:
`test_job_ceilings.py` carries `act-workflows` *and*
`test-generate-coverage-out-no-suffix`, `developers-guide.md` carries the
placement rule and the lane's ceiling, and the act guide carries both the
opt-in prose and the `-P ubuntu-latest=catthehacker/ubuntu:rust-latest`
snippets.

The new head is a linear descendant of the bottom
(`git merge-base --is-ancestor 5bc9e743 ef0a3dbc`), which is what the stack
requires. The top touches no `docs/execplans/` file, so this record can be
amended on the bottom without disturbing it. Amending it did move the bottom,
and the top was replayed once more onto the new tip; all 21 entries came out
patch-identical, and the only tree difference was this record. Expect one more
replay after this file's final edit: the top head is whatever the last such
replay produces, and `gh stack submit` pushes it with `--force-with-lease`.

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
| F4  | both CI arms carry a Docker daemon; podman is the probe's fallback, not the path       | D1       |
| F5  | a failed runtime probe made the whole lane exit 0 having run nothing                   | D6       |
| F6  | the rust image's baked toolchain cannot be renamed across overlay layers               | D7       |
| F7  | the resolve case's expectation outlived the fixture it describes                       | D8       |
| F8  | install-tool cannot verify act: act prints `act version 0.2.89`, not `act 0.2.89`      | see F8   |
| F9  | the worktree test asserted about the live checkout, not one it builds                  | see F9   |
| F10 | `Install nfpm` `curl: (22) … 500` on one PR's linux leg, transient upstream            | none     |
| F11 | github-script validates `github-token` before the script, and act leaves it empty      | D9       |
| F12 | the heaviest fixture exceeds the harness's 300 s act budget on a cold CI runner        | see F12  |
| F13 | CodeRabbit: dispatch skip read as act, silent version abort, D3 wording, pinned error  | see F13  |
| F14 | bash 3.2 ignores errexit for `[[ ]]`, so the assertion refused nothing on macOS        | see F14  |
| F15 | the act version assertion matched as a substring, accepting 0.2.890 / 10.2.89 / -rc.1  | see F15  |

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

### The stack, established

Done. `gh stack init` adopted both branches; `submit` created **PR #583** for
the bottom branch (base `main`), corrected **#516**'s base to
`fix/act-lane-runs-in-ci`, and created **stack #584** on GitHub.
`gh stack view --json` confirms the chaining.

One obstacle, worth recording because it is a property of this host, not the
stack. The first `submit` was refused:

```text
! [remote rejected] fix/act-lane-runs-in-ci -> fix/act-lane-runs-in-ci
  (refusing to allow an OAuth App to create or update workflow
  `.github/workflows/ci.yml` without `workflow` scope)
```

The Lody git helper's credential is an OAuth token
(`admin:public_key, gist, read:org, repo`) and **has no `workflow` scope**, so
GitHub rejects any ref update that creates or modifies `.github/workflows/*` —
which is exactly this branch's payload. Nothing had been pushed and no PR was
touched.

Resolution, within the host's documented bypass: the workflow-carrying refs
were pushed over direct SSH (`git@github.com`, which authenticates as `leynos`;
this repo's earlier workflow-file pushes went the same way), with the injected
`GIT_CONFIG_*` routing neutralized for that command only — not by editing the
Makefile, and not by restarting the daemon. The leases were bound to the heads
recorded before the operation (`7bc53417` for the top; the empty "must not
exist" lease for the never-pushed bottom), the same leases `gh stack push`
would compute. The tracking refs were then updated to the pushed heads so
`submit` took its no-op push path and did the PR and stack work over the API,
which is not affected by the scope gap.

A recurrence of this failure on any workflow-touching branch is expected until
the OAuth App is granted `workflow` scope, or pushes for this repo move to SSH.

### What the first CI run found

Both PRs ran the full matrix. Three failure classes surfaced; two are defects
of this stack's own making and one is environmental.

**F8 — install-tool cannot verify act, so the job that is the point of this
branch dies before the lane starts.** Both PRs, `act-workflows`, in ~15s:

```text
##[error]expected act 0.2.89, got act version 0.2.89
metric install-tool.verify=mismatch
```

`Install act -> failure`, `Run the act workflow lane -> skipped`. The root
cause is `resolve_tool.py`'s `describe()`: it derives
`expected_version = f"{binary} {entry['version']}"` — `act 0.2.89` — while act
prints `act version 0.2.89`. The verify step's substring test can therefore
never match, and the probe step's copy of it is equally dead. Every other
manifest tool was checked and does print `"<binary> <version>"`, and the
`installs-and-caches` matrix passes for sccache, merman-cli, cargo-nextest and
cargo-audit on both PRs, which isolates the defect to act's entry. The consumer
that keeps the derived default honest is generate-coverage and
ratchet-coverage's `install_cargo_llvm_cov.py`, which compares
`probe.version == expected_version` **exactly** — so any fix must leave every
other tool's composed string byte-identical.

**F9 — `test_an_ordinary_checkout_is_not_given_one` asserts about the live
checkout, and CI checks out ordinarily.** Coverage on both PRs, python-tests on
macOS and on Windows, all four:

```text
AssertionError: this repository is meant to be lived in as a linked worktree;
if it is not, the mount cannot be mounted and must be absent rather than
shadowing the checkout's own .git: got
/home/runner/work/shared-actions/shared-actions/.git
```

The test reads `_REPOSITORY_ROOT` and asserts the common dir is *not* inside it
— true only when the repository is lived in as a linked worktree, which is this
host's arrangement, not the runner's. The behaviour it should pin —
`_git_common_dir_mount` returning `None` for an ordinary checkout — is already
correct; the test simply never constructs the ordinary checkout it names.
GitHub runners check out with a real `.git` directory inside the checkout.

**F10 — `Install nfpm` failed with `curl: (22) … 500` on PR #583's linux leg
only.** The same job passed on #516 (3m58s) and on `main`; the step is a plain
curl download of a pinned release. Transient upstream; no code change planned,
and the re-run that the fixes trigger is the test of that judgement.

None of the three is a reason to touch the Makefile, the manifest's existing
entries, or the lane's design. F8 and F9 both belong on this branch: the act
manifest entry and the worktree test arrived with `c82c629e`/`6fc575f4`, and
this file lives only here. The fixes are recorded below as they land.

### The F8 and F9 fixes, landed

**F8 — `version-lead` in the manifest, composed by `describe()`.** The fix
keeps the derived default byte-identical for every tool that prints
`<binary> <version>`, because generate-coverage and ratchet-coverage compare
`probe.version == expected_version` with `==` (`install_cargo_llvm_cov.py`).
Shape:

- `.github/tool-manifest.toml` gains an optional `version-lead` key, and act's
  entry records `version-lead = "act version"`. The header documents the key
  and that a tool needing one and lacking it fails verification on a runner and
  nowhere earlier.
- `resolve_tool.py`'s `describe()` composes
  `f"{entry.get('version-lead', binary)} {entry['version']}"`.
- Guards, each pinning a distinct claim:
  `test_every_tool_has_the_required_fields` accepts the optional key;
  `test_the_tools_whose_output_is_not_their_name_are_the_expected_ones` pins
  act as the sole exception;
  `test_a_version_lead_carries_the_words_it_composes` pins the lead's shape;
  `test_a_tool_without_a_lead_keeps_the_derived_expectation` pins the
  byte-identical default; `test_accepts_a_tool_that_prints_more_than_its_name`
  runs the shipped verify fragment against a lead-shaped stub, so the failure
  cannot return unnoticed.
- `test-install-tool.yml`'s `installs-and-caches` matrix already asserts
  `expected: <tool> <version>` for four other tools, which is a second guard
  that the default did not move.

Verified locally: the install-tool suite is **210 passed**, and the resolver
against the real manifest reports `status=ok`, `version-check=true`,
`expected-version=act version 0.2.89`.

**F9 — the worktree test builds the ordinary checkout it names.** The test now
runs `git init` into `tmp_path/ordinary` and asserts `_git_common_dir_mount`
returns `None` for it, instead of reading this host's linked worktree. The
resolved-path comparisons are safe: `git rev-parse --path-format=absolute`
returns resolved paths even through a symlinked invocation, and pytest's tmpdir
temproot/basetemp are resolved.

**F10 — no change.** The `curl: (22) … 500` on PR #583's linux leg was a
transient upstream failure; the re-run the fixes trigger is the test of that
judgement.

### The second restack, done

The top sat on `78d5c2b8`, the remote bottom head, while the local bottom had
moved twice: `c2c3b4a7` (this record) and `61c75fd5` (the F8/F9 fixes). The
exclusive boundary is therefore `78d5c2b8` — the last inherited commit — and
the target `61c75fd5`. Explicit invocation, no autostash, no fork-point:

```text
git -c merge.conflictStyle=zdiff3 rebase --merge --no-fork-point \
  --no-update-refs --no-autostash --reapply-cherry-picks --keep-empty \
  --empty=stop --onto 61c75fd5 78d5c2b8 issue-515-…
```

All 21 commits replayed with **zero conflicts**; new head `9a08aaa7`. Recovery
refs `refs/recovery/act-lane-top-old-head-2` (`546bfa9d`) and
`refs/recovery/act-lane-bottom-new-61c75fd5` are retained. Log:
`/tmp/restack-shared-actions-fix-act-lane-runs-in-ci.out`.

Audit: `range-diff 78d5c2b8..546bfa9d 61c75fd5..9a08aaa7` reports **all 21
entries patch-identical**. The new head is a linear descendant of the new
bottom; 21 commits, no merges, `git diff --check` clean. The top-vs-bottom
patch is byte-identical except for two hunk *line numbers* in
`docs/developers-guide.md`, which the bottom's new `version-lead` paragraph
shifted; the top's own hunk content is unchanged. No bottom-owned file is
missing or altered at the new head: the 22-file overlap set is identical on
both tops, and every bottom-only file is byte-identical.

**Push shape.** The bottom's two new commits touch no `.github/workflows/*`
file, so its push is a plain fast-forward. The top's range contains
`.github/workflows/test-generate-coverage.yml`, so its push will hit the OAuth
`workflow`-scope refusal recorded under "The stack, established" and needs the
same SSH-with-explicit-leases bypass.

One more replay follows this record's own edit, as before: the top head is
whatever that replay produces, and `gh stack submit`/`push` publishes it with
`--force-with-lease`.

### What the second CI run found

The bottom (`ac4c992d`) went fully green, `act-workflows` included: the lane is
now *actually run*, which is the request's own acceptance test. The top
(`a2ccc33c`) failed `act-workflows` on exactly one fixture, the top-only
`test-generate-coverage-out-no-suffix` (1 failed / 1088 passed / 108 skipped,
780.38s).

**F11 — `actions/github-script` validates `github-token` before it runs the
script, and act leaves `github.token` empty.** The step dies at input
validation, before the act-aware script can select local disk:

```text
::error::Unhandled error: Error: Input required and not supplied: github-token
    at main (/run/act/actions/actions-github-script@d746ffe…/dist/index.js:65071:24)
Error: Job 'test-generate-coverage-out-no-suffix' failed
assert 1 == 0
```

Root cause, read from the pinned `dist/index.js`: `main()` runs
`core.getInput('github-token', { required: true })` before `callAsyncFunction`
executes the user script, and the manifest's declared default is
`${{ github.token }}`, which act leaves empty. This contradicts ADR 0005's
explicit promise — "**Local.** Under nektos/act, or with no runtime token,
there is no service sccache can use. Local disk is selected, never a failed
build."

This fixture is the only act-driven workflow leaving `use-sccache` at its
default `true`: `test-rustflags-export.yml` and `test-setup-rust-mold.yml` pass
`use-sccache: "false"`, and the sccache-specific fixtures are not driven by the
pytest act lane. So the failure is the fixture finding a real defect, which is
what the lane exists for.

**D9 — the selection step gets a `github-token` fallback, and a manifest test
holds it.** `github-token: ${{ github.token || 'unused-by-this-step' }}` in the
step's `with:` block. On a real runner the expression yields the runner's own
token, unchanged — byte-identical to the value github-script's own default
would have supplied. Under act it yields a placeholder that satisfies input
validation; nothing authenticates with it, and the script never touches the
client the action builds. Not an expected-failure workaround: the failure is
removed at its cause, the input validation, and the step then selects the
backend ADR 0005 promises. A manifest test
(`test_the_token_input_never_blocks_the_selection`) pins the fallback, because
the Node harness stubs `@actions/core` and structurally cannot observe
github-script's own validation.

Verified locally, end to end: the harness fixture passes (269.81s), and a
direct act run reports `metric setup-rust.sccache.backend=local`,
`::notice title=setup-rust sccache::selected local disk (action)`,
`sccache: Starting the server...`, `Post Run sccache` ✅, `jobResult: success`,
and the coverage outputs the fixture asserts (`file=coverage.xml`,
`format=cobertura`,
`artefact-name=cobertura-test-generate-coverage-out-no-suffix-0-linux-x86_64`).

Placement: the fix belongs on the bottom, with F8 and F9, for the same reasons
— the fixture lives only on the top, but the defect it found is setup-rust's,
and the bottom is the PR whose charter is "run the lane and fix what it finds".
The file is byte-identical on both branches, so the edit moved down with a
branch switch. The top's next replay is a no-op against this change; only the
new bottom commit needs publishing.

Logs: `/tmp/act-fixture-repro-…out` (before), `/tmp/act-fixture-after-fix-…out`
(harness pass), `/tmp/act-direct-…out` (direct act run).

### The third restack, and publication

The F11 fix and its record landed on the bottom as `23dfa2e5` and `cc486d14`,
gated locally (all six gates green; `make test` 3626 passed, 141 skipped) and
pushed as a fast-forward — neither commit touches `.github/workflows/*`.

The top was replayed with the same explicit invocation, boundary `ac4c992d`
onto target `cc486d14`:

```text
git -c merge.conflictStyle=zdiff3 rebase --merge --no-fork-point \
  --no-update-refs --no-autostash --reapply-cherry-picks --keep-empty \
  --empty=stop --onto cc486d14 ac4c992d issue-515-…
```

All 21 commits replayed with zero conflicts; new head `1b1c3f17`. Recovery refs
`refs/recovery/act-lane-top-old-head-3` (`a2ccc33c`) and
`refs/recovery/act-lane-bottom-old-head-3` (`ac4c992d`) are retained. Audit:
`range-diff ac4c992d..a2ccc33c cc486d14..1b1c3f17` reports all 21 entries
patch-identical; 21 commits, no merges, `git diff --check` clean; every
bottom-owned file (setup-rust's action, changelog, test, and this file) is
byte-identical at the new top. The top-vs-bottom diffstat is unchanged.

The top's push again needed the SSH-with-explicit-leases bypass: its range
carries `.github/workflows/test-generate-coverage.yml`, and the OAuth token has
no `workflow` scope. The lease was bound to the head recorded before the
operation (`a2ccc33c`), the tracking refs were updated to the pushed heads, and
`gh stack submit --auto` then took its no-op push path and synced the stack
object (#584) over the API.

CI: the bottom re-ran green on the previous head; this cycle's runs are watched
below. One transient upstream failure surfaced on #516's `install-whitaker` — a
`http status: 500` downloading `dylint-link` from `leynos/whitaker`'s rolling
release, the same class as F10 and unrelated to setup-rust; the job passes on
the bottom, and the failed job was re-run.

### What the third CI run found, and the review

The third run's bottom (`7b02bf83`) `act-workflows` passed — 11m01s, so the
lane is run in CI and the bottom is green. The top (`c9d7ce6c`) failed the same
single fixture, but no longer at the token: the F11 fix works, the step gets
past validation and selects local disk. The failure is now

```text
act timed out after 300s
```

**F12 — the lane's heaviest fixture exceeds the harness's default act budget on
a cold runner.** `ActConfig.timeout` defaults to 300 s; the fixture took 269.81
s on this idle host, and the CI runner is slower and cold (it pulls the 3.09 GB
`rust-latest` image and installs a toolchain before the build). The timing is
the whole story: the job's `Run the act workflow lane` step ran 16:59:04 →
17:04:14, and the fixture is the last in the file, so the kill at ~310 s is the
fixture consuming the budget, not a hang. `test_rustflags_export_workflow.py`
already carries `timeout=600` for exactly this reason — its fixture is slower
than the default too. The fix gives this fixture the same override, threaded
through `EnvOverrideTestCase.timeout` so the default stays 300 for every case
that does not need it.

**F13 — four findings from the CodeRabbit review of both scopes, all minor, all
fixed.** The reviews completed with no rate limiting (4 of 10 included reviews
remaining), zero high or medium concerns, and full file coverage (24/24 and
22/22). The four:

- **A real functional defect on the bottom: the resolve fixture's outcome
  assertion reads a dispatch's skip as act.** The step branched on
  `-n "${ACT_OUTCOME}"`, but a skipped step reports the outcome `"skipped"`,
  which is non-empty. On a genuine `workflow_dispatch` the act-branch step is
  skipped, so the guard took the act arm, demanded `"success"` of a step that
  reported `"skipped"`, and failed — the one run the OIDC half exists for. Act
  cannot catch it because act selects the opposite branch. The fix asks whether
  the act branch *succeeded*: `if [[ "${ACT_OUTCOME}" == "success" ]]` then
  require the OIDC half skipped, else require the act half skipped and the OIDC
  half failed. Two new executed cases in
  `test_resolve_workflow_source_fixture.py` run the fixture's own script (read
  from the step, not retyped) under each runner's real pair and refuse every
  other pair; mutation-checked: the old shape produces exactly 2 failures.
- **A silent-failure finding on the bottom: the ci.yml version assertion
  aborted with no message.** A bare `[[ ... ]]` under `set -e` exits non-zero
  without saying what it found, so the one failure the assertion exists to
  explain was the one that said nothing. Now an explicit `if` prints the
  expected version, the resolved binary and the version it printed, then exits
  1. A new contract (`test_act_job_version_assertion.py`) executes the step
  against stub `act`/`make` binaries and proves a mismatch stops before `make`
  while a match proceeds; mutation-checked: the old shape produces 1 failure.
- **A documentation finding on the bottom:** D3 called `rust-latest`
  "fedora-ish"; it is the same Ubuntu 24.04 base as `act-latest` with the
  toolchain added, as F3 and the conftest comment already say. Wording
  corrected.
- **A flake on the top: the IPC disconnect assertion pinned one of two error
  names.** A write to a socket whose peer has gone raises `BrokenPipeError` or
  `ConnectionResetError` depending on kernel timing, and the guard treats both
  as a departed client — the sibling unit test already covers both. The
  unguarded-path assertion accepted only `BrokenPipeError`, so a reset would
  fail the test spuriously. It now accepts either and carries the captured
  stderr in the message.

Placement: F13's fixture and ci.yml fixes, and the D3 wording, belong on the
bottom — the resolve fixture and `ci.yml` are bottom-owned, and the review's
scope-1 findings are exactly that layer. F12 and F13's IPC finding belong on
the top: `test_action_behaviours.py` and `test_cmd_mox_ipc_disconnect.py` are
changed only there, and the timeout is the top's fixture (the generate-coverage
case exists only on the top; the bottom's copy of `test_action_behaviours.py`
has no such case at all).

### The fourth restack, and publication

The F13 fixes landed in two commits, one per layer. On the top, `04570bfb`
(F12's timeout) and `60ce6f38` (the IPC error pair) — both committed first, so
the top could be replayed without carrying uncommitted work across a branch
switch. On the bottom, `c8066cd5`, "Read a dispatch's skip as a skip, and say
what the version check found", carrying the resolve fixture, `ci.yml`, both new
contracts, and this file.

All six gates were green on the bottom candidate before the commit (check-fmt,
typecheck, lint, test 3636 passed / 141 skipped, markdownlint, nixie), and the
two new contracts executed rather than being skipped: three cases in
`test_act_job_version_assertion.py` and eleven in
`test_resolve_workflow_source_fixture.py`. `make test-act` was deliberately
excluded from the gate pass and run separately afterwards.

The bottom's push needed the SSH-with-explicit-leases bypass for the first time:
`c8066cd5` is the first bottom commit whose range touches
`.github/workflows/*` (ci.yml and the resolve fixture), and the OAuth token has
no `workflow` scope. The push was a fast-forward from `7b02bf83` over
`git@github.com:`, with every injected `GIT_CONFIG_*` variable unset.

The top was replayed with the same explicit invocation, boundary `7b02bf83`
onto target `c8066cd5`:

```text
git -c merge.conflictStyle=zdiff3 rebase --merge --no-fork-point \
  --no-update-refs --no-autostash --reapply-cherry-picks --keep-empty \
  --empty=stop --onto c8066cd5 7b02bf83 issue-515-…
```

All 23 commits replayed with zero conflicts; new head `6ba0c160`. The bottom
commit touches five files and the top's 23 commits touch twenty-two; the two
sets are disjoint, so no replay could conflict. Recovery refs
`refs/recovery/issue515-r4-old-head-20261002-202039` (`60ce6f38`),
`…-old-base-…` (`7b02bf83`), `…-target-…` (`c8066cd5`) and `…-remote-head-…`
(`c9d7ce6c`) are retained. Audit:
`range-diff 7b02bf83..60ce6f38 c8066cd5..6ba0c160` reports all 23 entries
patch-identical; 23 commits, no merges, `git diff --check` clean.

The top's push also took the bypass, its lease bound to the head published
before the operation (`c9d7ce6c`). The tracking ref was force-refreshed to the
pushed head, and `gh stack submit --auto` then reported both PRs up to date and
synced the stack object (#584). Verified topology: PR #583
`fix/act-lane-runs-in-ci → main`, PR #516
`issue-515-… → fix/act-lane-runs-in-ci`, heads `c8066cd5` and `6ba0c160`,
`needsRebase: false` on both.

### The act lane, green on the restacked top

`make test-act` ran to completion on the restacked top: **1099 passed, 108
skipped in 1386 s (23:06)**, zero failures and zero errors. Log:
`/tmp/test-act-shared-actions-fix-act-lane-runs-in-ci.out`.

The two fixtures this cycle fixed both passed: the F12 case
(`test_env_overrides_normalize_inputs[generate-coverage-out-no-suffix]`) under
its 600 s budget, and all twelve resolve-fixture cases, including the two new
executed pairs. The whole lane also passed on the pre-restack bottom
(`7b02bf83`) in CI in 11m01s, so the local run agrees with CI on the lane's
outcome for this change surface.

### What the fourth CI run found: F14, and the lane's blind spot

Both heads (`c8066cd5`, `6ba0c160`) went green everywhere except
`python-tests (macos)`, which failed the same four cases on each: the new
`test_the_assertion_refuses_any_other_pair` rows, each reporting the assertion
had *accepted* a pair it must refuse. `act-workflows` passed on both heads.

**F14 — bash 3.2 does not treat a failing `[[ ]]` as fatal under errexit, so
the fixture's refusal depended on the host shell.** macOS still ships bash 3.2
as `/bin/bash`, which is the `bash` the macOS job's tests resolve; there,
`set -e` aborts on a failing `[ ]` or `test` but not on a failing `[[ ]]`. The
assertion step's refusals were bare `[[ ]]` statements, so on macOS they were
no-ops: the step fell through to its diagnostic `echo`s and exited 0 for every
pair. The Linux job and act (Ubuntu, bash 5) never saw it, which is why the
lane passed while macOS failed — the lane is Linux-only, so this is the first
defect in this cycle the lane could not have caught and the plain macOS suite
could. Reproduced in `docker.io/library/bash:3.2` under podman: the old script
exits 0 for all four refusal pairs, the new one exits 1 for all ten non-real
pairs and 0 for the two real ones, in both 3.2 and 5.2.

The fix spells the refusal as nested `if` / `exit 1`, which both shells honour,
and the contract's module docstring now records why the explicit form is
required. The assertion is the one this cycle already rewrote once (F13's first
finding) — that rewrite fixed the *logic* for a dispatch and this one fixes its
*portability*, and the executed cases caught each.

Worth recording for the next cycle: the act lane cannot see this class of
defect. It runs on Linux with bash 5, so any assertion whose behaviour differs
by shell is invisible to it; the macOS leg of `python-tests` is the only place
such a defect shows, and it only shows when a contract executes the script
rather than matching its text.

### The fifth restack, and both PRs green

F14's fix and its record landed on the bottom as `4e475c52` and `503200f5`,
gated locally (all six gates green; `make test` 3636 passed / 141 skipped) and
pushed over the SSH bypass — the fix touches
`.github/workflows/test-resolve-workflow-source.yml`, so the OAuth token's
missing `workflow` scope again refused a plain push.

The top was replayed with the same explicit invocation, boundary `c8066cd5`
onto target `503200f5`:

```text
git -c merge.conflictStyle=zdiff3 rebase --merge --no-fork-point \
  --no-update-refs --no-autostash --reapply-cherry-picks --keep-empty \
  --empty=stop --onto 503200f5 c8066cd5 issue-515-…
```

All 23 commits replayed with zero conflicts; new head `ef7c4748`. Recovery refs
`refs/recovery/issue515-r5-old-head-20261002-211952` (`6ba0c160`),
`…-old-base-…` (`c8066cd5`) and `…-target-…` (`503200f5`) are retained. Audit:
`range-diff c8066cd5..6ba0c160 503200f5..ef7c4748` reports all 23 entries
patch-identical; 23 commits, no merges, `git diff --check` clean. The top's
push took the bypass with its lease bound to `6ba0c160`, the tracking refs were
force-refreshed, and `gh stack submit --auto` synced the stack object.

**Both PRs are now fully green in CI, macOS included.** Run 37053390324 (bottom
`503200f5`): all five jobs success, `act-workflows` 11m26s. Run 37053451127 (top
`ef7c4748`): all five jobs success, `act-workflows` 13m36s — the F12 fixture
completing inside its 600 s budget on a cold runner, which is what that
override was for. Both PRs report `mergeable=MERGEABLE`,
`mergeStateStatus=CLEAN`, no longer drafts. The macOS leg passing on both is
F14's proof: the same four cases that failed the fourth run pass now, and the
act lane's own run cannot show the difference because it never runs bash 3.2.

PR bodies were updated for this cycle: #583 gains the dispatch-skip fix, the
loud version assertion, and the bash 3.2 portability fix, with the lane result
refreshed to 1099 passed / 108 skipped; #516 gains the F12 timeout and the IPC
error pair.

### The sixth CI run, the review, and F15

Run 37055384862 (bottom `f48b87f7`) and run 37055411936 (top `33157c0d`) are
both fully green: all five jobs on each, `act-workflows` included, macOS
included. The record commit's own replay did not disturb anything — the
`range-diff` audit had already shown the series patch-identical.

CodeRabbit was then run over the delta the previous review had not seen: the
bottom's F13/F14 work (base `main`, head `f48b87f7`) and the top's F12/IPC work
(base `fix/act-lane-runs-in-ci`, head `33157c0d`). The top returned zero
findings. The bottom returned two, both on the same line of `ci.yml`:

**F15 — the act version assertion matched the pinned version as a substring, so
it failed open on exactly the case it exists for.** The step asked whether
`act --version`'s output *contained* `0.2.89`, which accepts `0.2.890`,
`10.2.89` and `0.2.89-rc.1` — each a version nobody pinned, and each a runner
the gate is meant to stop. Reproduced directly in bash: the substring form
accepts all three; only `0.2.88` is refused. The fix splices spaces around the
output and matches `*[[:space:]]"${ACT_VERSION}"[[:space:]]*`, which gives the
version a left and right boundary, so no leading or trailing character can
extend it. Verified in `docker.io/library/bash:3.2` and the host's bash 5.2:
both accept `act version 0.2.89` and a trailing-detail form, and both refuse
all four near misses. The near misses are now cases in
`test_act_job_version_assertion.py` — mutation-checked against the old form,
which fails exactly those three and passes the rest.

This is the same class as F14 but caught by review rather than by a runner: the
substring check is *portable* and *deterministic*, so no shell and no leg would
have failed. A gate that only ever sees the version it expects is a gate whose
discrimination is never exercised; the contract's near-miss cases are what make
the assertion's refusal checkable at all.

### The seventh restack, and publication

F15's fix landed on the bottom as `36911dbc`, "Match the pinned act version as
a token, not a substring", carrying `ci.yml`, the contract, and this file.
Gated locally before the commit: `check-fmt` needed one mechanical fix (ruff
collapsed a wrapped assertion), after which all six gates were green on the
same tree — `test` 3640 passed / 141 skipped, the extra four over the previous
count being the new parametrized cases.

The bottom's push took the SSH bypass again (`ci.yml` is in the range), a
fast-forward from `f48b87f7`. The top was replayed with the same explicit
invocation, boundary `f48b87f7` onto target `36911dbc`:

```text
git -c merge.conflictStyle=zdiff3 rebase --merge --no-fork-point \
  --no-update-refs --no-autostash --reapply-cherry-picks --keep-empty \
  --empty=stop --onto 36911dbc f48b87f7 issue-515-…
```

All 23 commits replayed with zero conflicts; new head `2850c782`. Recovery refs
`refs/recovery/issue515-r7-old-head-20261002-222419` (`33157c0d`),
`…-old-base-…` (`f48b87f7`) and `…-target-…` (`36911dbc`) are retained. Audit:
`range-diff f48b87f7..33157c0d 36911dbc..2850c782` reports all 23 entries
patch-identical, no non-matching entries at all; `git diff --check` clean.

The top's six gates were green on `2850c782` (`test` 3665 passed / 146
skipped), and its push took the bypass with its lease bound to `33157c0d`.
Tracking refs were force-refreshed and `gh stack submit --auto` reported the
stack up to date; `gh stack view --json` confirms `36911dbc` ← `2850c782`, both
`needsRebase=False`. #583's body gained the F15 bullet; #516's already named
its base correctly and needed no edit.

**Both reviews are now clear.** The bottom's re-review at `36911dbc` returned
zero findings, and the top's at `33157c0d` had already returned zero.

**Both PRs are green at the F15 heads.** Run 37060360264 (bottom `36911dbc`)
and run 37060954668 (top `2850c782`) both completed with all five jobs success —
`act-workflows`, `coverage`, both `python-tests` legs, and Windows included.
The macOS leg passing is what keeps F14 closed, and the `act-workflows` leg is
the lane this stack exists to make run.

### The eighth restack, onto a main that grew a doctest tier

The F15 head went `CONFLICTING`/`DIRTY` when `main` advanced to `d0c2585d`. One
file was in conflict, and the check that established that was non-mutating:
`git merge-tree --write-tree` names the conflicted paths without touching the
index, so the blast radius was known before any history moved.

**The conflict was `Makefile`, and it was real.** `main`'s #493 added a
`doctest` tier — a `DOCTEST_PATHS` list, a `doctest:` target, and
`test: .venv doctest` — while this branch's `9c829353` had reworked the `test`
recipe for the act lane. Both sides edited the same two regions: the `.PHONY`
list, and the `test` recipe and its prerequisites. Neither side's version is a
superset, so neither `--ours` nor `--theirs` was correct; the resolution is the
union.

The union, region by region:

- `.PHONY` carries both new names, `test-act` and `doctest`.
- `test: .venv doctest` keeps main's prerequisite and gains the lane's
  `ACT_WORKFLOW_TESTS=0` export on the recipe line, so docstring examples still
  run first and the plain suite still cannot inherit the opt-in.
- Everything main added — `DOCTEST_PATHS` with its comment block, the `doctest`
  target, and the `ci.yml` step that runs it — is untouched.

The constraint that made this worth doing carefully rather than quickly: main's
`tests/workflows/test_doctest_target.py` executes the real `Makefile` with a
stub `uv` and requires `test` to run doctest **first** and to stop before the
suite if it fails, and `test_doctest_coverage.py` requires every file carrying a
`>>>` to be named in `DOCTEST_PATHS`. A union that dropped either half fails
both. No branch-added test file contains `>>>`, so `DOCTEST_PATHS` needed no
addition — checked, not assumed.

The replay, with the explicit invocation and the boundary this branch's own
work starts at (`OLD_BASE` = `ff1dd759`, the last commit it inherits, not the
merge base):

```text
git -c merge.conflictStyle=zdiff3 rebase --merge --no-fork-point \
  --no-update-refs --no-autostash --reapply-cherry-picks --keep-empty \
  --empty=stop --onto d0c2585d ff1dd759 fix/act-lane-runs-in-ci
```

`e69c4e80` → **`961279ab`**, 24 commits, linear, zero merges. Recovery refs
`refs/recovery/act-lane-r8-old-head-e69c4e80`,
`refs/recovery/act-lane-r8-old-base-ff1dd759` and
`refs/recovery/act-lane-r8-target-d0c2585d` are retained.

Audit: `range-diff ff1dd759..e69c4e80 d0c2585d..961279ab` reports **23 of 24
entries patch-identical**. The one differing entry is #2, `9c829353` →
`ea428571` ("Gate the plain suite off the act lane, and hold the recipes to
it"), and it differs by exactly main's `doctest` additions — which is the
conflict's resolution showing up where it should. `git diff --check` against
`origin/main` is clean.

The targeted contracts were run before the full gate set, so a bad resolution
would be named rather than merely failing: 194 passed across the doctest target
and coverage contracts, the act-lane recipe contracts, the version-assertion
contract, the job ceilings and the platform-step contracts.

This restack also split the work by layer, because two sessions were converging
on the same stack. This session owns the bottom branch; a peer session owns the
top branch and the merge, and neither touches the other's layer. The top is
untouched at `55b385b3` and will be replayed by its owner with boundary
`e69c4e80` once the bottom's push is verified.

### F16 — an inline `VAR=value` recipe prefix hands the line to a shell

The F15 head turned `python-tests-windows` red in 3m59s while every other job
stayed green, including `python-tests (macos)` and the lane itself. The failing
assertion was main's `test_doctest_target.py`:

```text
E  AssertionError: /usr/bin/sh: line 1:
   C:UsersrunneradminAppDataLocalTemppytest-of-runneradminpytest-0test_...uv-stub:
   command not found
E    make: *** [Makefile:87: test] Error 127
```

Note the path: `C:Usersrunneradmin...`. The backslashes are gone. The test
passes `UV=C:\Users\runneradmin\AppData\Local\Temp\...\uv-stub` as a
command-line override, exactly as it does on `main`, where this same job is
green at `69f9c29d`.

**The mechanism.** GNU Make execs a recipe's command directly when the line is
a bare command, and passes it to a shell when the line is anything else —
including a leading inline assignment. `$(UV) run ...` takes the direct path,
so the `UV` path arrives intact. `ACT_WORKFLOW_TESTS=0 $(UV) run ...` cannot be
exec'd directly, so make hands the line to `/usr/bin/sh`, and that shell eats
the backslashes. Make has no Windows-specific special case here: the
`SHELL`/`sh.exe` handling applies to the shell it *does* invoke. The union at
`ea428571` introduced the prefix into a line that `main` had kept bare, which
is why a job that was green on `main` went red on the branch.

The reproduction that isolates it, on a Linux make, is the shell-mangling
itself: a recipe line beginning `ACT_WORKFLOW_TESTS=0 python3 … C:\Users\ru\x`
reaches the child as `C:Usersrux`. On Linux this is invisible, because the
direct-exec and shell paths both mangle identically and both succeed; only a
Windows `UV` path exposes the difference. **No local Linux gate can confirm the
fix. The decisive evidence is the Windows CI leg after the push.**

**The fix, and why it is not the obvious one.** The obvious form is the one the
documentation offers — `test: export override ACT_WORKFLOW_TESTS := 0` — and it
is what a peer session proposed and validated on GNU Make 4.4. It is wrong
here. Make **3.81**, which is what macOS ships as `/usr/bin/make`, rejects a
target-specific assignment carrying both keywords:

```text
Makefile:5: *** multiple target patterns.  Stop.
```

Verified against a locally built 3.81, and against the real Makefile: the file
fails to parse at all, so *every* target on macOS dies — including the
`test_doctest_target.py` suite that made this job red on Windows in the first
place. That trades one platform's failure for another's. Make 3.81 accepts
`export` **or** `override` on a target line, never both.

The portable shape splits them: `export` as a global directive, `override` on
the target, and a bare recipe line.

```make
export ACT_WORKFLOW_TESTS
test: override ACT_WORKFLOW_TESTS := 0
test: .venv doctest ## Run tests, docstring examples first
	$(UV) run --with typer … pytest -n auto --dist worksteal -v
```

`test-act` gets the same treatment (`export ACT := $(ACT)` and
`override ACT_WORKFLOW_TESTS := 1` on the target, bare `$(UV)` line), so the
lane's line stops being a shell line too. Behaviour was checked on **both**
makes (3.81 and 4.4.1) across all four opt-in paths — plain, command-line
`ACT_WORKFLOW_TESTS=1`, command-line `WITH_ACT=1`, and an inherited environment
`ACT_WORKFLOW_TESTS=1`: the plain child always sees `0`, the lane child always
sees `1` with the resolved `ACT`, and the lane still runs for every opt-in. The
plain recipe line is now byte-identical to the one `main` runs green on Windows.

`tests/workflows/test_makefile_act_lane_runs_once.py` was rewritten to hold the
new mechanism, since it had been reading the old one off the recipe line — a
contract on the shape that caused the bug would have passed while the bug
shipped. It now asserts the plain target forces the gate off with `override`
and can export it, the lane forces it on, the two disagree, **every recipe line
starts with a non-assignment word** (the Windows property, stated directly),
and no target line combines `export` with `override` (the 3.81 property). Eight
mutations were checked against it, including the peer's combined-keyword form
and a reinstated inline prefix; all eight fail at least one contract.

**A second finding, unrelated to the lane: `tests/*.py` is collected by
nothing, and wiring it in is not a one-line fix.** `pytest.ini`'s `testpaths`
names `.github/actions`, `workflow_scripts/tests` and `tests/workflows`; the
four modules directly in `tests/` are in none of them, so they run nowhere —
not in `make test`, not in the CI coverage job, not on either platform leg.
Three of the four fail when run directly: two in `test_cmd_utils.py` (a
`bytearray` payload that decodes to `"bytearray(b'chunk')"` rather than
`"chunk"`, and a non-UTF-8 stderr case) and
`test_makefile_typecheck.py::test_typecheck_target_passes_project_venv_to_both_ty_invocations`
(the `typecheck` recipe grew `install-mdtablefix`/`install-makeutil` search
paths and targets after the contract's expected list was written). All three
are pre-existing on `origin/main`; none is caused by this branch, and
`test_makefile_typecheck.py` behaves identically against the unmodified
`origin/main` Makefile.

The obvious repair — add `tests` to `testpaths` — does not work yet, for two
independent reasons, each confirmed by running it:

- `tests/test_cmd_utils.py` and
  `.github/actions/generate-coverage/tests/test_cmd_utils.py` are both
  collected, share a basename, and neither directory is a package (no
  `__init__.py`), so pytest aborts the whole run at import:

  ```text
  import file mismatch: imported module 'test_cmd_utils' has this
  __file__ attribute: …tests/test_cmd_utils.py, which is not the same
  as the test file we want to collect: …generate-coverage/tests/…
  ```

  That is a collection error on `make test` and on every CI leg, not a mere
  test failure. Either basename has to change or one of the directories has to
  become a package, and the fix has to land *with* the `testpaths` change or it
  is worse than the status quo.
- `tests/test_makefile_typecheck.py` and
  `tests/workflows/test_doctest_target.py` both run `make` for real. The latter
  is already in `testpaths`, so it already runs on the Windows leg, and if it
  is green there the same holds for the former; that part would probably be
  fine, but it is exactly the surface F16 shows is risky, so it wants its own
  record rather than being folded in.

So this is a genuine follow-up rather than something to repair inside the
Windows fix: it needs a basename or package decision, two decode repairs, a
regenerated `typecheck` expectation, and a Windows run to confirm the
make-dependent modules behave there — a footprint that would bury an 11-line
Makefile fix. Recorded here so it is not lost; not attempted here.

### The ninth pass: the review's pre-merge checks

CodeRabbit's pre-merge checks on #583 were holding the PR at
`CHANGES_REQUESTED` with one error and three warnings. Each was verified
against the code before being acted on; all four were genuine, and all four
were inside this stack's own ranges rather than pre-existing.

**The error — `github_repository` had no test.** `composite_fragments.py`
gained the field and the `github.repository` arm of `ActionContext.resolve` in
this range (it is absent on `origin/main`), but nothing exercised either. The
contract module grew a `github.repository` row in the parametrized resolution
table and a case for the undeclared default, which is the one a manifest reading
`${{ github.repository }}` without declaring the `env` key gets: an empty
string, as a runner substitutes. `resolve-workflow-source` declares it, so the
empty default is the behaviour worth pinning.

**The warnings.** The `install-tool` and lane documentation existed but not
where the checker reads: `docs/developers-guide.md` gained a maintainer-facing
section on the lane's Makefile interface (`test-act`, `WITH_ACT`,
`ACT_WORKFLOW_TESTS`, the `ACT` override, and why the gate rides on the target
rather than the recipe), and `docs/users-guide.md` gained the `version-lead`
behaviour plus act in the manifest's list. The property warning was the
substantive one: the `runs-on` classifier gained a Hypothesis test asserting
the independent rule directly — accepted exactly when the labels named are
non-empty and all Linux — with a separate case for the empty-label boundary,
which is the one the property test cannot generate its way to.

**What the mutation check caught in my own work.** Six mutations were run
against the new tests. Five failed a contract immediately; the fourth — the
forwarded `UV_PROJECT_ENVIRONMENT` clobbering a case's own `container_env` —
passed, because the case I had written set `RUSTUP_PERMIT_COPY_RENAME` in the
host environment, and the forwarding only ever reads `UV_PROJECT_ENVIRONMENT`.
The test was vacuous: it could not have failed for any implementation. It was
rewritten to exercise the variable the forwarding actually touches, and the
same mutation then failed it. A test that pins an ordering has to use the
operands that ordering is about.

**Two gate failures, both mine, both in the contract file.** `make check-fmt`
wanted an `assert` collapsed onto one line, and `make lint` wanted `r"""` on
the two docstrings carrying a literal `C:\...`. Both were introduced by the
rewrite on this branch; the `HEAD` version of the file passed both gates. A
first gate run was discarded because I edited the tree underneath it — the
`make test` it produced never saw five of the seven changed files — so the
whole set was re-run against a frozen tree. The lesson is cheap and worth
keeping: freeze the tree, then gate, not the other way round.

### Why the reviewer's own form of the fix is the one that breaks macOS

The combined form `test: export override ACT_WORKFLOW_TESTS := 0` is not a
stylistic alternative to the shipped `export`/`override` split. On the make
macOS actually ships as `/usr/bin/make` it is a hard parse error, and it was
reported as a validated option by a peer session running GNU Make 4.4. That
divergence is worth recording, because it is invisible on this host and the
tempting conclusion — "both forms work, pick either" — is false.

Reproduced on a locally built GNU Make 3.81, the same binary used for the F16
verification:

```text
$ /tmp/make381/make-3.81 -f /tmp/m381a.mk test
/tmp/m381a.mk:2: *** multiple target patterns.  Stop.
exit=2

$ make -f /tmp/m381a.mk test      # GNU Make 4.4.1
exit=0
```

The combined keyword form parses on 4.4.1 and fails on 3.81, which is why local
validation said "validated" and the Makefile would still have been unparsable
on macOS — every target, not just the lane, since a parse error takes the whole
file. The `test_doctest_target.py` suite that this work exists to green on
Windows would die on macOS instead; the fix would have traded one platform's
red for another's.

The shipped shape was re-verified on both makes after the fact, across all four
opt-in paths (plain, inherited `ACT_WORKFLOW_TESTS=1`, command-line
`ACT_WORKFLOW_TESTS=1`, and command-line `WITH_ACT=1`): the plain child always
sees `0`, the lane child always sees `1`. `override` beats an inherited
environment value and a command-line value on both 3.81 and 4.4.1, which is
what makes the split form safe to use rather than merely parseable.

**The rule for this repository, stated once:** a target-specific variable line
may carry `export` or `override`, never both. `export` belongs in a global
directive; the gate belongs on the target as `override`. Anything that
validates a Makefile change on this host alone has not validated it, because
`/usr/bin/make` here is 4.4.1 and the host CI cares about is 3.81. There is now
a contract test on this property
(`test_no_target_line_combines_export_and_override`), so a future edit that
reintroduces the combined form fails locally rather than on macOS.

### Which checks can actually see the F16 fix

Worth pinning down, because F16 is the kind of defect that can ship green.

The branch ruleset is `main-required-checks` (ruleset 18427916, enforcement
`active`, `~DEFAULT_BRANCH`): it requires eleven `build-release` legs, the three
`python-tests` legs (linux, macos, **windows**), and ten action contract tests.
`strict_required_status_checks_policy` is `false`, so a required check is
matched by name against the head regardless of whether the branch is behind.

`python-tests-windows` is therefore a required check, and it is the **only**
one that can observe the difference. The reasoning, restated compactly: GNU
make execs a recipe line directly when it begins with a command, but hands any
line beginning with an inline `VAR=value` prefix to a POSIX shell
(`/usr/bin/sh`). On Linux that shell is dash or bash and both paths mangle
identically and succeed, so no Linux gate — including the
`python-tests (linux)` leg and every local gate on this host — can distinguish
the two forms. On Windows that shell is MSYS `sh`, which rewrites
`C:\Users\runneradmin\...` into `C:Usersrunneradmin...`, and the command
vanishes. This is why the decision log records a Windows leg rather than a
local reproduction as the acceptance evidence for F16, and why a green local
`make test` says nothing about it.

**`act-workflows` is not a required check.** The new lane runs in CI and its
value is that it executes at all — the stack exists because the lane had never
run there — but it is not in the required list, so its failure would not block.
That is a deliberate distinction: the lane's job is to be *run*, and the gate
that protects the fix is the Windows leg.

### Review coverage after a restack is not review coverage

The subtlest thing this pass turned up is not a defect in the code but in how
review state was being read. CodeRabbit's last substantive word on #583 was a
confirmation that Codex's P2 finding was fixed, and it named the commit it
confirmed: `e69c4e80`. By the time that review was read, `e69c4e80` had ceased
to exist — the eighth restack (`b4963b38`, onto a main that had grown the
doctest tier) replayed it as `961279ab`, and
`git merge-base --is-ancestor e69c4e80 HEAD` is false. The same is true of the
`CHANGES_REQUESTED` review still attached to the PR: it anchors `ac4c992d`,
orphaned three restacks ago.

So the PR carried a stale blocking review against a commit line that is no
longer in the branch, and a "confirmed resolved" that speaks for a tree that
has since been rebuilt. Neither is a finding about the code, and neither is
evidence about the current candidate. The correct move is what was done: treat
both as expired, re-dispatch a full review against the published head
(`comenq put leynos/shared-actions 583 "@coderabbitai full review"`), and let
the new review's `commit_id` establish what it actually inspected. A reply that
says "resolved" is not a resolution; a review whose `commit_id` is unreachable
from the head is not coverage. Both statements have to be read against
`git merge-base --is-ancestor`, not against the comment's own confidence.

### The transport anomaly: an environment rewrite, not a push bug

One loose end from the push of `55e3b5a5` is worth recording because it looked,
briefly, like a security-relevant event. The push printed:

```text
To https://github.com/leynos/shared-actions.git
   18494589..55e3b5a5  HEAD -> fix/act-lane-runs-in-ci
```

despite the command naming `git@github.com:leynos/shared-actions.git`, with
`[Lody GitHub] {"source":"personal","stage":"acquire","code":"personal_auth_missing"}`
on stderr. The instruction in force is SSH-only for pushes, so a silent
fallback to HTTPS reads as a violation.

It is not. Lody injects a `GIT_CONFIG_*` block into the session environment
(`GIT_CONFIG_COUNT=10`) whose first four entries are
`url.https://github.com/.insteadOf`. Git applies `insteadOf` rewrites before
transport selection, so `git@github.com:…` is rewritten to
`https://github.com/…` before git ever reaches an SSH client. The proof is one
command:

```text
$ /usr/bin/git ls-remote --get-url git@github.com:leynos/shared-actions.git
https://github.com/leynos/shared-actions.git

$ env -u GIT_CONFIG_COUNT -u GIT_CONFIG_KEY_0 … \
    /usr/bin/git ls-remote --get-url git@github.com:leynos/shared-actions.git
git@github.com:leynos/shared-actions.git
```

and with `GIT_TRACE=1` on the stripped environment the transport is
`/usr/bin/ssh -o SendEnv=GIT_PROTOCOL git@github.com 'git-upload-pack …'`. The
push itself landed correctly — `git ls-remote` over genuine SSH returns
`55e3b5a52e6c156cab8d2802dbfc8b6fa18792f6` — so the only real finding is the
one the global instructions already state as a rule: strip `GIT_CONFIG_*` from
the environment for git operations in this session, or the transport is not the
one the command asked for. This is the same injected block the earlier memory
records as breaking credential routing for `gh` and `uv` fetches; here it is
milder — the rewrite succeeds over HTTPS — but it is the same mechanism, and
"strip `GIT_CONFIG_*`" is the remedy in both cases.

A related trap, easy to hit while auditing this: an injected `GIT_CONFIG_*`
entry and a modern configuration entry share a name, so
`sed 's/GIT_CONFIG_VALUE_[0-9]+=<redacted>/'` is fine, but dumping
`GIT_CONFIG_KEY_*` by value prints configuration, not secrets — while dumping
`GIT_CONFIG_VALUE_*` prints the credential helper path, which embeds a
per-session directory. Print key names, never values.

### F16 is closed: the Windows leg ran the contract and passed

The claim that no Linux gate can see the F16 defect carried an obligation —
that the Windows CI leg be read, not merely be green in aggregate. It has now
been read, on the candidate that matters.

```text
actions/jobs/114064503116   python-tests-windows
  conclusion : success
  head_sha   : 55e3b5a52e6c156cab8d2802dbfc8b6fa18792f6
  run_id     : 38002763313 (attempt 1)
  steps      : Set up job / Checkout / Setup uv / Run tests / post-steps,
               all success
```

`head_sha` is the pushed candidate, read back from the API rather than inferred
from a PR badge, and `python-tests-windows` is one of the ruleset's required
`required_status_checks`. The run step's summary:

```text
collected 2843 items / 13 skipped
================ 2589 passed, 267 skipped in 289.94s (0:04:49) ================
```

and the two modules that carry the contract are visible as passing lines in
that log, not merely as counts:

```text
tests\workflows\test_makefile_act_lane_runs_once.py ......               [ 78%]
tests\workflows\test_doctest_target.py ......                            [ 71%]
```

Six dots, six assertions, no failure markers anywhere in the log. The argument
for F16 was that only this leg could distinguish the direct-exec path from the
shell path; the leg ran the rewritten contract, and the contract passed.

**The controlled comparison.** The push before this one ran the same three jobs
on the previous head, so the before/after is a genuine experiment rather than a
comparison against `main`:

| Job                    | `18494589` (before) | `55e3b5a5` (after)  |
| ---------------------- | ------------------- | ------------------- |
| `python-tests-windows` | **failure**         | **success**         |
| `python-tests (macos)` | success             | **failure** (flake) |
| `python-tests (linux)` | success             | success             |

Job IDs confirm it is the same job in each column: windows `114047207030` →
`114064503116`, macOS `114047207211` → `114064503470`, linux `114047207256` →
`114064503358`. The before-run's windows failure carries the F16 signature
verbatim, which is the same text the mechanism section predicts:

```text
E  AssertionError: /usr/bin/sh: line 1:
   C:UsersrunneradminAppDataLocalTemppytest-of-runneradminpytest-0
   test_make_test_runs_the_exampl0uv-stub: command not found
E   +  where 2 = CompletedProcess(args=['make', '-f', 'Makefile', 'test',
      'UV=C:\\Users\\runneradmin\\AppData\\Local\\Temp\\pytest-of-r...'])
   returncode: make: *** [Makefile:87: test] Error 127
```

Everything before `make` is true to the diagnosis: `/usr/bin/sh` (not cmd.exe)
was handed the line, and it ate every backslash in the `UV` value, collapsing
the whole path — `uv-stub` included — into a single unsplittable token. The
after-run's windows leg has no such line. One job changed from red to green and
nothing else about the Makefile changed, which is as close to a controlled
experiment as a CI run gets.

**The macOS leg is a flake, and saying so needs evidence.**
`python-tests (macos)` failed on the new head, which is the leg whose
`/usr/bin/make` is 3.81 — exactly the leg a mistake in the `export`/`override`
split would have broken at parse time. So it is the one failure that could have
falsified the fix, and it does not:

- It failed with `AssertionError: IPC error: timed out` in
  `test_generate_coverage_allow_no_tests_subprocess.py`, raised against a
  `cmd_mox` stub, with a `BrokenPipeError` in `socketserver` on the server
  side. That is a subprocess IPC timeout, not a parse error: the runner would
  have reported `Makefile:NNN: *** multiple target patterns. Stop.` and no
  tests would have run at all.
- The macOS leg passed at `18494589` — the head that *failed* on Windows —
  which rules out "this leg never worked" and rules out a Make 3.81 parse fault
  on the new head: 3.81 parses the new file correctly, which is the property
  the split form was chosen for.
- The failing test file is unchanged by this branch
  (`git log origin/main..HEAD -- <path>` is empty), and the branch's only
  change under `generate-coverage/` is two context-manager return annotations,
  `cabc.Iterator[None]` → `cabc.Generator[None]` and `typ.Iterator[Path]` →
  `typ.Generator[Path]`. Those are annotations under
  `from __future__ import annotations` on generator functions already decorated
  with `@contextlib.contextmanager`; they alter no runtime value and cannot
  make an IPC socket time out.
- The codebase already has a history of this exact class of flake:
  `git log --all --grep='IPC disconnect'` returns eight commits titled "Surface
  the child's streams, and stop the IPC disconnect traceback (#515)", which is
  the same traceback the macOS log shows.

A rerun of the failed job is the appropriate next step when one is available;
the rule is not to record this as green but to record it as an unrelated
infrastructure flake with the evidence above, and to keep the Windows result —
which is the one that speaks to this change — separate.

### The act lane itself, running in CI

The task's headline objective was that the lane run in CI at all, and it is now
observably doing so. The `act-workflows` job on `55e3b5a5` (job `114064503364`)
is a straight run of success:

```text
name        : act-workflows
conclusion  : success
head_sha    : 55e3b5a52e6c156cab8d2802dbfc8b6fa18792f6
steps       : Set up job / Set up runner / Checkout repository / Setup uv /
              Install act / Run the act workflow lane / post-steps
              — every step success
```

The step that matters is "Run the act workflow lane", and it ran a real lane
rather than exiting early — which is the silent-skip failure this work was
chasing, so the counts are the evidence, not the tick:

```text
make test-act ACT="${ACT_BIN}"
… SKIPPED / PASSED lines for tests/workflows/* …
================ 1167 passed, 110 skipped in 580.95s (0:09:40) ================
```

Nine minutes forty of wall clock, 1167 tests, and individual `PASSED` lines
naming `tests/workflows/<module>::<class>::<test>[<workflow fixture>]`. A lane
that had skipped its runtime probe would exit 0 in seconds with no test IDs in
its log; this one cannot have — the fixture IDs in the parameterized names are
the ones that only appear when `act` actually executes a workflow.

The remaining red check on this head is the macOS `cmd_mox` flake documented
above; `act-workflows` is not among the ruleset's required checks, so its green
is evidence rather than a gate, and the two should not be conflated when
reading the PR.

**Two cautions about how this evidence is read**, both of which were live here:
the logs endpoint refuses to emit output containing terminal escape sequences
unless `--allow-escape-sequences` is passed, and `gh run view --log` answers
`logs will be available when it is complete` for a *partially* complete run —
the job was green while its parent run was still going, so the per-job endpoint
(`actions/jobs/<id>/logs`) is the one to use.

### The tenth pass: two gate failures, one of them a real local-only race

The r8 gate run came back red on two gates, and the two failures had nothing in
common except that both were mine to fix.

**`make markdownlint` — spelling, and a masked-span false positive.** The
`spelling` prerequisite failed on two words. `parameterised` was a plain typo:
the shared en-GB-oxendict dictionary prefers `-ize`, so it is `parameterized`
here. The other was subtler and worth recording, because the obvious reading
("I misspelled a word in a quotation") was wrong. The flagged string was
`exampl` inside `test_make_test_runs_the_exampl0\uv-stub`, and checking it
against the before-run's own job log showed it is **verbatim**: pytest
truncates its tmpdir component to 30 characters, so the real directory
genuinely ends `..._the_exampl0`. The apparent misspelling is a truncation
artefact of pytest's own naming, not an error in the quotation.

The reason it was flagged at all is the mask. `typos.local.toml` masks inline
code spans with this pattern:

```text
`[^`\n]+`
```

The negated class excludes the newline by design, so that an unclosed backtick
cannot swallow the rest of the file. The offending span was an inline `UV` path
wrapped across a line break, so the mask did not apply to its second half and
the truncated token fell through to the dictionary. The fix that preserves the
mechanism is to **not** put a wrapped path in an inline span: the sentence now
refers to the whole path with the tail named separately, and the
backslash-laden path stays in the fenced verbatim block above, where no mask is
needed. Widening the mask pattern to span newlines would have traded a one-line
rewording for a class of unclosed-backtick bugs, which is the wrong direction.

**`make test` — a deterministic xdist race, and a correction.** One test failed:
`test_action_install_step_resolves_from_external_checkout`, with
`shutil.Error: … [Errno 2] No such file or directory: '…/.venv-coverage'`. My
first reading was that I had caused it: three gate agents were running
concurrently in this session, two of them executing `make test` at once, and
that violates the sequential-gate rule outright. That reading was **wrong**,
and the experiment is what showed it.

`test_doctest_coverage.py` builds a throwaway venv at `<repo>/.venv-coverage`
and removes it in a `finally`; `test_action_workdir.py` copies the whole
repository root with `shutil.copytree` behind `ignore_patterns(...)`, and that
list does not name `.venv-coverage`. Under `-n auto --dist worksteal` the two
land on different workers and the removal can race the copy mid-walk.

Run the two files together under xdist, and nothing else:

```text
12 runs, unmodified tree:  1 failed, 38 passed   (12/12)
 8 runs, with the fix:        39 passed            (8/8)
```

Twelve out of twelve. It is not a flake, it is not a load artefact, and it is
not caused by my overlapping gates — it is a deterministic collision the moment
those two files share a session. The concurrency in this session made it *more
likely to be noticed*, which is the opposite of my first conclusion.

It is also why CI never sees it. `ci.yml` runs `uv run pytest` **serially** on
the macOS and Windows legs, and the Linux suite runs through the coverage
action with `pytest-workers: ''`. Only the local `make test` uses
`-n auto --dist worksteal`, so the local gate is the one that can fail this
way. A green CI run is no evidence against it, in either direction.

The fix adds `.venv-coverage` to that ignore list. The list is already a
deliberate **superset** of what the action's `rsync` excludes — `.cache` and
`.uv-cache` appear in the test and not in `action.yml` — so this follows the
list's existing intent rather than extending it, and the accompanying comment
says why the entry is there so the next reader does not "tidy" it away as
unused.

Two things worth carrying forward. First, the failure was invisible to CI and
visible only to the local gate, so "CI is green" and "the gate is green" are
not interchangeable claims about this repository. Second, when a gate fails
while several of my own jobs are running, the concurrency is a strong
*temptation* to blame and a weak *explanation* — the discriminator is whether
the failure reproduces in a single isolated run, and here it reproduced twelve
times out of twelve.

### The act lane runs in CI

Run `38006382760` on `49918168` was the first green run in which the lane this
PR adds did the thing it exists for. All five jobs passed (`act-workflows`,
`coverage`, `python-tests` on linux, macOS and Windows), and A17's
`act-workflows` job reported:

```text
running the lane on: act version 0.2.89 (/home/runner/.cargo/bin/act)
================ 1167 passed, 110 skipped in 618.39s (0:10:18) =================
```

The skips are workflow-parameter scoping in four test classes, not act guards.
That distinction is worth stating, because a skipped act case reports success
for a run that executed nothing: `skip_unless_act` fails rather than skips when
the lane was requested, so every module carrying the mark that reports `PASSED`
proves act executed. All eight did, with zero skips. This is the silent-skip
hazard the lane was built to close, and the guard held under the real CI
runtime rather than only locally.

### The merge will be by stack number, not by branch

The branch sits in a remote stack (`PRS_kwDOO9OxNc4AGbC5`, stack 584) with #516
above it. The local `gh stack` metadata in this worktree is **stale**: it
records heads `e69c4e80` and `55b385b3`, both orphaned, and bases that no
longer exist. A `gh stack merge` that trusted it would act on a wrong idea of
where the branch is. `gh stack merge <number>` is a pure remote operation and
is not exposed to that staleness, which is why the merge is named by stack
number rather than by branch.

The other review facts that gate the merge, checked against live state rather
than the recorded ledger:

- The ruleset `main-required-checks` (id 18427916) requires **only** the 21
  status-check contexts and `deletion`. There is no review-approval rule in it,
  and there is no classic branch protection on `main` — the API answers
  `Branch not protected`. So `reviewDecision: CHANGES_REQUESTED` is a visible
  signal, not a merge block.
- That `CHANGES_REQUESTED` (review 5392227901) is a CodeRabbit review anchored
  to `ac4c992d`, which is not an ancestor of this head. Its whole body is
  "Pre-merge checks failed. Please resolve the failing checks before merging.",
  and the PR has exactly one review thread, which is resolved and outdated.
- PR 516's head `e26fac8f` now descends from `49918168`, so this branch's
  landing will not orphan it; before the peer's restack its diff against this
  branch was a `-374/+1` reversion of this very change.

### The eleventh pass: the pre-merge rows, checked against the source

CodeRabbit's first issue comment was edited in place into a "Reviews paused"
notice, but its pre-merge block still names four rows for `ac4c992d`. Each was
re-checked against the current source rather than the stale anchor:

- **Testing (Overall)** — fixed. The `github.repository` arm and the
  `_build_container_env` defaults both have tests now.
- **User-Facing Documentation** — fixed. `docs/users-guide.md` carries
  `version-lead` and act's manifest entry.
- **Developer Documentation** — fixed. Two items in the row were genuinely
  undocumented: the overlayfs rename failure behind
  `RUSTUP_PERMIT_COPY_RENAME=1` and the read-only Git object-store mount a
  linked worktree needs. Both are now in a new "The fixture container: image,
  environment and mounts" subsection. The rest of the row was already covered —
  including the `catthehacker/ubuntu:rust-latest` image, which the row placed
  in the wrong file: it is documented in
  `docs/local-validation-of-github-actions-with-act-and-pytest.md`.
- **Testing (Property / Proof)** — fixed, and its premise is wrong. The
  Hypothesis test it asks for exists
  (`test_only_expressions_of_linux_labels_are_accepted`), and the claim that
  "no PR addition uses Hypothesis" is false for this diff.

A lesson here: a pre-merge row is a claim about a revision, and when the branch
has been replayed the claim is about a revision that no longer exists. The rows
had to be re-derived from the source, and one of the four turned out to be
describing a defect that the anchor revision had and the current one does not.
The reconciliation names the reviewed revision and the current one so the
discrepancy is visible rather than argued.

### The twelfth pass: CodeRabbit's reply, and four defects it found

The reconciliation went to `coderabbitai` naming head `4f98b2e4` and base
`d0c2585d`. It replied through
[issue comment 6091465241](https://github.com/leynos/shared-actions/pull/583#issuecomment-6091465241),
accepting Testing (Overall) as resolved and contesting the other three. Every
claim was re-checked against the current source, and all four were correct:

- **`docs/users-guide.md` was factually wrong about the default.** It said most
  tools print a bare version "which is what the manifest's `version` is
  compared against". `resolve_tool.py:194` composes
  `f"{entry.get('version-lead', binary)} {entry['version']}"`, and
  `test_resolve_tool.py:154` pins `brisk 2.0.0` for a leadless tool. The
  default is `<binary> <version>`, and the section now says so.
- **The developers-guide forward reference was dangling.** My own new prose
  said "the runner-label classifier below decides whether act is given an image
  at all", and the guide contained zero occurrences of
  `_resolves_to_one_platform`, `_LINUX_PLATFORMS` or `_require_an_image_for`.
  The original pre-merge row had asked for the label mapping and the
  fail-closed guard, and "partially resolved" was the right verdict. There is
  now a "The runner-label classifier, and the image guard" subsection and an
  "`ActionContext.github_repository`" subsection.
- **`docs/local-validation-…-act-and-pytest.md` contradicted the harness
  twice.** It said the harness "skips automatically" when the runtime is
  unavailable and that the target "first runs the regular test suite … then
  re-runs only the workflow harness". `pytest_runtest_setup` *fails* an
  opted-in lane whose runtime is missing, and `test: test-act` makes the lane a
  **prerequisite**, so it runs first and the plain suite then forces the gate
  off. Both statements are corrected, and `make test-act` is named as the
  standalone command.
- **The matrix property genuinely did not exist.** The existing matrix tests
  were fixed examples calling the implementation as the oracle, which is what
  the row forbade. There is now a property generating zero, one and several
  `include` legs and asserting the rule from the generated inputs.

A fifth defect was found while fixing the fourth, in my own expression property:
`compared` was filtered only against whole-string `_RUNNER_LABELS`, so a
generated value containing a single quote would splice a label into the
expression. Reproduced with `compared=["x' || 'macos-15"]`, which yields
`${{ 'ubuntu-latest' || 'x' || 'macos-15' }}` and fails a property that never
described that input. The alphabet now excludes `'`.

Both new properties were mutation-checked rather than assumed to be load-
bearing. The first attempt patched `tests.workflows.conftest`, but pytest
imports the module as `workflows.conftest`, so the mutant never reached the
call path and survived — a false negative from a broken harness, not a weak
test. Installed through a `pytest_collection_modifyitems` plugin on the module
pytest actually loads, a first-string-only `_labels_named_by` fails 3 tests,
and a matrix rule that accepts any non-empty leg set fails 3 more.

Also recorded: the CI run for the superseded head `4f98b2e4` (`38007997738`)
was **cancelled** by GitHub's concurrency group, not failed — "a higher
priority waiting request for CI-refs/pull/583/merge exists". The current head
`d60d2590` is green on all five runs, with `act-workflows` genuinely executing
`act version 0.2.89` (`1167 passed, 110 skipped`).

The same class of defect was then found twice more in the *new* matrix
property, both by asking what the reader normalizes that the expectation does
not. `_single_label` strips surrounding whitespace, so a leg spelled
`" ubicloud-standard-2"` is read as the one accepted label while an expectation
comparing raw strings says it is not. `_single_label` also refuses anything
starting with `${{`, so a leg spelled `"${{ foo }}"` contributes no label at
all while the expectation counts it as one. Each was reproduced before being
fixed, and the generated text now excludes the quote, the whitespace categories
and the expression prefix — the three spellings that make a generated value
mean something different to the test than to the classifier.

#### Two more defects, found by the gates and by re-reading

The gate run on this delta came back with `make spelling` and
`make markdownlint` **red** — the first real failure either had reported
against this branch, and both from the same word. The local dictionary is
en-GB-oxendict, which re-words `-ise` to `-ize`, and the two `normalises`
spellings added this pass failed it. The gate stops at the first error, so only
one was reported and genuine coverage required checking for the rest by
scanning every added line for `-ise`/`-our`/doubled-`l` variants: three
candidates, of which `cancelled` and `behaviour` are the oxendict forms and two
`normalises` were not. Both are fixed, and the gate is green with `typos.toml`
byte-identical — so the shared dictionary was never the problem, the prose was.

`make markdownlint` never ran at all: it depends on `spelling`, so the
prerequisite failure blocked the lint step and left markdown coverage unproven
rather than passing. Run on its own it reports `98 file(s), 0 error(s)`. A gate
that is blocked by a prerequisite must not be read as a gate that passed.

Re-reading the new matrix property against the source found one more
inaccuracy, in a comment rather than in code: `.get` answers `None` for an
absent key exactly as it does for a null value, so a property generating
`{"os": None}` exercises the null value and *not* the absent key its comment
claimed. The named example test pins the absent key; the comment now says so
instead of overclaiming. This is the fourth time on this branch that a claim
was found to be one step wider than the code supporting it, and each time it
was found by reading the source rather than by running the suite — the suite
was green throughout.

#### Publication of the twelfth pass

Pushed as `51dde73f` (`d60d2590..51dde73f -> fix/act-lane-runs-in-ci`), local
HEAD, `origin/fix/act-lane-runs-in-ci` and PR 583's `headRefOid` all reading
that SHA. The reconciliation reply went to
[issue comment 6091755126](https://github.com/leynos/shared-actions/pull/583#issuecomment-6091755126),
dispositioning all four contested findings against the published head and
base, and noting the fifth defect found while fixing the fourth.

The reply was posted through the authorized token pool at
`~/.local/share/github-tokens`, selecting with `shuf`. That file is
newline-separated token values rather than `key=value`, which an initial
redacting `sed` assumed; the values were printed to the terminal as a result.
No value is recorded here and none was written to any tracked file, but the
assumption is worth noting for the next reader: confirm the format before
piping a credential file through a filter intended to mask it.

Before the reply, each citation in it was re-checked against the source rather
than recalled: `resolve_tool.py:194` and `test_resolve_tool.py:154` for the
version default, `Makefile:112` and `Makefile:101` for the prerequisite and the
forced-off gate, `conftest.py:339-344` for fail-versus-skip, and the three
exclusions in `_COMPARED_VALUE` for the normalization claim.
