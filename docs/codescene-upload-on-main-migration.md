# Agent prompt: adopt the "upload on main" CodeScene topology

Copy the block below into an agent working in a single estate repository that
uses `leynos/shared-actions`. It is written to be self-contained: the agent
needs no prior context from the repository that pioneered the pattern.

Derived from `leynos/syrupy-mdast` PR #32, which is the reference
implementation. Where this document and that PR disagree, the PR is right and
this document is a bug.

Two notes on reading this file.

**The blockquote is not a diff.** The whole prompt is quoted, including its
headings and its own non-goals list. Indentation inside the quoted region
carries no meaning; it reproduces the prompt verbatim so it can be copied in
one piece.

**The quotes of the published action are verbatim.** Where this document shows
a shell test, a `description:` string, or a named step from
`leynos/shared-actions`, it is reproduced from the action at the revision named
alongside it. Anything that looks like a quotation but paraphrases is labelled
as a paraphrase.

______________________________________________________________________

## Prompt

> ## Mission
>
> Migrate this repository to the estate's "upload on main" CodeScene coverage
> topology, exactly as implemented in `leynos/syrupy-mdast` PR #32. `main`
> becomes the only CodeScene consumer. Pull requests stop contacting CodeScene
> entirely and enforce coverage locally instead.
>
> Work on a branch. Commit after each coherent change and gate each commit.
>
> ## Why this is being done
>
> The previous topology ran the CodeScene changed-line gate on trusted internal
> pull requests. That has two problems. A pull request runs arbitrary
> head-repository code, so a CodeScene step it can reach either hands
> `CS_ACCESS_TOKEN` to a fork or leaves a secret-less fork waiting on a check
> that can never be produced. And it made pull-request mergeability depend on a
> remote service.
>
> Separately, this repository may be carrying a pin that is already broken. The
> shared action revision `395f8e8630d431abb4a136847f1c14c4ad5a0ccc` nests
> `actions/cache@6849a6489940f00c2f30c0fb92c6274307ccb58a`, which is
> `actions/cache` v4.1.2. GitHub retired that release, so **any job reaching it
> fails during action preparation, before the first step runs**:
>
> ```text
> This request has been automatically failed because it uses a deprecated
> version of `actions/cache: 6849a648...`.
> ```
>
> That failure is invisible to every local gate, because nothing local resolves
> a composite action's transitive pins. It presents as a workflow that passed
> yesterday and failed today with no commit in between.
>
> ## The target topology
>
> 1. **Upload on default-branch push only.** One step, `mode: upload`, guarded
>    by `github.event_name == 'push' && github.ref == 'refs/heads/main'`.
> 2. **Pull requests never contact CodeScene.** No `check` step, no
>    `project-url`, no fork skip notice.
> 3. **Pull-request coverage is enforced locally** by adding
>    `with-ratchet: 'true'` to the shared `generate-coverage` action, which
>    fails the run when total line coverage falls below the baseline the default
>    branch last saved.
> 4. **No checksum input is passed.** See "The checksum trap" below — this is
>    the single most likely way to get this migration wrong.
>
> ## Required changes
>
> ### 1. Locate every CodeScene site
>
> Find every reference in this repository, in both `.github/workflows/` and
> `.github/actions/`:
>
> ```bash
> grep -rn "upload-codescene-coverage\|codescene\|CodeScene\|CS_ACCESS_TOKEN\|CODESCENE_CLI_SHA256\|cs-coverage" \
>   .github/ --include=*.yml --include=*.yaml
> ```
>
> Sites are not always in one file. A repository may keep the upload in a
> separate `coverage-main.yml` while the pull-request gate lives in `ci.yml`,
> and may run the same gate from more than one job. Report every site you find
> before changing any of them.
>
> Also check for a workflow whose only purpose was refreshing a checksum
> variable — commonly `.github/workflows/get-codescene-sha.yml`. If present,
> delete it; its reader is going away.
>
> ### 2. Pin the upload step to the approved revision
>
> The upload step must reference exactly:
>
> ```text
> leynos/shared-actions/.github/actions/upload-codescene-coverage@a5765019912a8ab6882b12db049c7cde635f3a85
> ```
>
> Two revisions are forbidden and must not appear anywhere:
>
> - `395f8e8630d431abb4a136847f1c14c4ad5a0ccc` — the obsolete revision that
>   nests the retired `actions/cache@6849a648`.
> - `4d696e72fff6db49f34302ccf119ba978f1032c9` — it does **not** contain the
>   required CodeScene cache migration.
>
> Do not advance the pin beyond `a5765019` in this change unless you have a
> specific reason. Do not modify any other action pin.
>
> ### 3. Replace the CodeScene steps with the target shape
>
> Delete the `mode: check` step, its `project-url:` input, and any direct
> `cs-coverage` installation. Add or replace to leave exactly:
>
> ```yaml
>       - name: Upload coverage data to CodeScene
>         env:
>           CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}
>         if: github.event_name == 'push' && github.ref == 'refs/heads/main' && env.CS_ACCESS_TOKEN != ''
>         uses: leynos/shared-actions/.github/actions/upload-codescene-coverage@a5765019912a8ab6882b12db049c7cde635f3a85
>         with:
>           format: cobertura
>           mode: upload
>           path: coverage.xml
>           access-token: ${{ env.CS_ACCESS_TOKEN }}
> ```
>
> Match the `format` and `path` inputs to the coverage format this repository
> actually produces — `cobertura`/`coverage.xml` or `lcov`/`lcov.info`. `lcov`
> files must end in `.info`; the action rejects anything else in `check` mode
> and it is the estate convention regardless.
>
> Confirm the default branch name before writing the guard. If it is not `main`,
> use that name.
> `gh repo view --json defaultBranchRef --jq .defaultBranchRef.name`
>
> ### 4. Add the ratchet to `generate-coverage`
>
> Add `with-ratchet: 'true'` to the shared `generate-coverage` step. That step
> is now the pull-request coverage decision.
>
> The input exists from `296dc4aa07acf3a7f60a54fb6bccb5672a597479` (2026-07-07)
> onwards, and is fully wired there. If this repository pins something older,
> advance the pin to `a5765019912a8ab6882b12db049c7cde635f3a85` in the same
> commit and say so in the commit message.
>
> Two things to verify rather than assume, because an input that is declared but
> not consumed accepts `with-ratchet: 'true'` and ratchets nothing — the
> workflow looks correct and fails open:
>
> ```bash
> PIN=<CURRENT_PIN>
> gh api "repos/leynos/shared-actions/contents/.github/actions/generate-coverage/action.yml?ref=$PIN" \
>   --jq '.content' | base64 -d > /tmp/gc.yml
> grep -n 'with-ratchet' /tmp/gc.yml    # expect many hits, not one
> grep -n 'baseline-python-file\|baseline-rust-file' /tmp/gc.yml
> ```
>
> ### 4b. Know what advances the baseline
>
> The ratchet compares against a baseline stored in the Actions cache and saved
> by a later step. `publish-baseline` defaults to `auto`, which saves **only on
> a push to `refs/heads/main`**. So:
>
> - A trunk that is **not** `main` never advances the baseline under `auto`. The
>   comparison still runs, against whatever was last saved — which may be
>   nothing, in which case the ratchet has no floor and quietly passes.
> - A repository whose merges fire no `push` event on the trunk is in the same
>   position.
>
> If either applies, set `publish-baseline: 'always'` and confirm the calling
> workflow restricts that job to the trunk — the action's own comment says the
> calling workflow is responsible for that restriction once `always` is set, so
> `always` without the restriction lets a feature branch publish the baseline
> every pull request measures against.
>
> For a trunk named `main` with ordinary merges, the default is correct and
> needs no change. Verify which case this repository is in; do not set `always`
> reflexively.
>
> Note this is a **new external dependency**: the pull-request coverage decision
> now depends on a cache entry that only a trunk push populates. Before it
> exists the ratchet has no baseline to enforce. Expect the first run after
> migration to be weaker than the steady state, and do not treat that as
> evidence the ratchet is broken.
>
> ### 5. Drop the checksum plumbing
>
> Remove the `CODESCENE_CLI_SHA256` environment variable and any repository
> variable or workflow that exists only to populate it. If this repository has
> a documented setup step telling operators to set `CODESCENE_CLI_SHA256`,
> update that documentation.
>
> ### 6. Consider the checkout depth
>
> A `fetch-depth: 0` checkout often exists only to feed the removed `check`
> mode. If this repository has one:
>
> - Remove it **only** if nothing else needs full history.
> - Verify nothing else does. Do not blanket-remove every `fetch-depth: 0` in
>   the repository.
>
> ### 7. Pin the job token
>
> Add `permissions: contents: read` to the job if it is not already scoped, and
> if the job genuinely needs no repository write.
>
> ## The checksum trap
>
> **This is the change most likely to be got wrong, and getting it wrong is
> silent.**
>
> The shared action changed what its checksum input means across revisions. The
> two eras are **not interchangeable**, and in **both** eras the input is
> optional with an empty default — so "pass nothing" is correct at every
> revision, and the only thing that can go wrong is carrying an input forward:
>
> | Pin | If you supply a digest | Meaning |
> | --- | --- | --- |
> | At or before `c5a54701c8603a0fa756a6b34c49bc2af75a6c11` | `installer-checksum` | Digest of the downloaded **installer script**, checked with `sha256sum -c`, and only when the value is non-empty |
> | At `a5765019912a8ab6882b12db049c7cde635f3a85` | `archive-checksum` | Must equal `archive_sha256` in the action's own `cli-manifest.json` — the digest of the CLI **zip archive** |
>
> At `a5765019`, a non-empty `installer-checksum` is a **hard error**. The
> action's first step is:
>
> ```bash
> if [ -n "$INPUT_INSTALLER_CHECKSUM" ]; then
>   echo "installer-checksum is deprecated; remove it or use archive-checksum" >&2
>   exit 1
> fi
> ```
>
> A repository still passing
> `installer-checksum: ${{ vars.CODESCENE_CLI_SHA256 }}`
> at this pin is green only because that variable is empty. Populating it — for
> any reason, at any time — fails the step. This is live in
> `leynos/netsuke` and filed as
> [netsuke#751](https://github.com/leynos/netsuke/issues/751).
>
> **The instruction: pass no checksum input at all.** Delete the line. Do not
> rename it to `archive-checksum`, and do not add `archive-checksum` at all.
> Both inputs are `required: false` with an empty default, and the action
> verifies the archive it downloads against its own `cli-manifest.json`
> unconditionally. A caller-supplied digest can only agree with the manifest or
> go stale and fail the run, so omitting it loses nothing — and it means bumping
> the action revision needs no accompanying variable update, because the
> manifest travels with the revision.
>
> Note the asymmetry: if this repository is currently at or below `c5a54701`
> and passing `installer-checksum`, that is **correct for that pin**. The
> mistake is carrying the input forward to `a5765019`, not having it now.
>
> ## An absent token skips, it does not fail
>
> The action's own upload step is conditioned on `inputs.access-token != ''`,
> so an empty token makes it a no-op. The caller-side guard
> `env.CS_ACCESS_TOKEN != ''` makes that explicit and stops the run from even
> claiming a step it will not perform.
>
> Do **not** add a step that fails the run when the token is missing. It reads
> as the stricter, safer choice and it is not: the token is absent by
> construction in forks and in any repository that has not provisioned the
> secret, so a fail-on-absent guard turns every such push to the default branch
> red for a reason the pusher cannot fix and that no code change will clear.
> Skipping is the intended behaviour. The purpose of the guard is to make the
> skip visible and deliberate rather than incidental.
>
> ## Contract tests
>
> Add a workflow contract test that pins the new topology, adapting
> `tests/test_codescene_workflow_contract.py` from `leynos/syrupy-mdast` PR #32.
> Two properties matter more than the rest:
>
> - **The sweep that asserts absence must match on behaviour, not on name.**
>   The point of that sweep is to prove nothing reaches CodeScene from a pull
>   request. A name-based sweep can be defeated by relabelling the step, so it
>   proves nothing. The reference test collects a step when its `uses:` names
>   the shared action **or** its `run:` invokes `cs-coverage`, across every job
>   in the workflow — so delegating the call under a new name, or moving it to
>   another job, cannot hide it.
>
>   A narrower, name-based lookup is still fine for a specific named step, and
>   the reference test uses one to assert ordering (the job generates the report
>   before it uploads it). Keep the two roles distinct: behaviour-based for "no
>   step may contact CodeScene", name-based for "this particular step behaves".
>   Note that a step's `name:` is the *caller's* label — the action's own
>   internal step is "Upload coverage to CodeScene" while a caller may call its
>   step anything — so a name-based assertion is a statement about this
>   repository's workflow, not about the action.
> - **Assert the retired revisions cannot reappear.** The failure this guards
>   against is a future pin bump that reintroduces `395f8e86`, or a new pin that
>   resolves through `actions/cache@6849a648`.
>
> The tests must run **without network access**. The estate's convention is a
> checked-in fixture, `tests/support/approved_action_revisions.json`, recording
> each approved revision and the third-party actions nested inside it, with a
> `retired` section listing condemned references. Refresh it whenever a pin
> moves, so a reviewer sees the dependency list change alongside the pin.
>
> Record honestly. `actions/upload-artifact@v4` and `actions/cache@v4` appear as
> floating tags inside some approved composites; record them as floating tags
> rather than pretending they are SHA-pinned, and note that the retired-SHA rule
> does not reach them. Do not try to fix them from the consumer side — they live
> inside the composite and a consumer cannot repin them.
>
> ## Documentation
>
> Add a developer-facing note explaining why an immutable full-SHA pin still
> needs scheduled maintenance: pinning to a SHA stops a *tag* from moving, but
> it does not freeze the pins inside the pinned thing, and a third-party action
> nested in a composite can be retired without the composite's own SHA changing.
> Point readers at the fixture and the contract test as the mechanism that
> catches it.
>
> ## Verification
>
> Run the repository's own gates — formatting, lint, type-check, tests — using
> its own targets, and run the workflow contract test specifically. Confirm:
>
> - [ ] Every `upload-codescene-coverage` reference is
>       `a5765019912a8ab6882b12db049c7cde635f3a85`.
> - [ ] `395f8e8630d431abb4a136847f1c14c4ad5a0ccc` appears nowhere in the
>       repository, including in fixtures and docs, other than as a condemned
>       entry that the contract test asserts is rejected.
> - [ ] No `installer-checksum` and no `archive-checksum` reaches the action.
> - [ ] No `mode: check` step and no `project-url` input remain.
> - [ ] `with-ratchet: 'true'` is present on `generate-coverage`, **and** the
>       pinned revision both declares that input and *wires* it — grep the
>       pinned `action.yml` for more than one hit. A revision that declares an
>       input without consuming it accepts `with-ratchet: 'true'` and ratchets
>       nothing, which looks correct in the caller and fails open.
> - [ ] No direct `cs-coverage` installation remains.
> - [ ] `CODESCENE_CLI_SHA256` has no remaining reader anywhere.
> - [ ] The upload step's guard names this repository's real default branch.
> - [ ] No step was added that fails the run when the token is absent.
> - [ ] The workflow YAML parses, and any action validator the repository runs
>       passes.
>
> Validate the workflow file itself rather than trusting the diff — a YAML
> syntax error in a workflow does not fail most local test suites.
>
> ## Report back
>
> State, with evidence:
>
> 1. Every CodeScene site found, and what happened to each.
> 2. The exact revision the upload step now pins.
> 3. That no checksum input is passed, and that you verified the action does not
>    require one.
> 4. Which `generate-coverage` revision is pinned, that it wires `with-ratchet`,
>    and what `publish-baseline` resolves to for this repository's trunk name.
> 5. Whether `fetch-depth: 0` was removed, and what you verified to conclude
>    nothing else needed it.
> 6. Anything you found that this prompt did not anticipate.
>
> ## Non-goals — do not do these
>
> - Do not change unrelated action pins. This change touches the CodeScene
>   topology and nothing else.
> - Do not reintroduce direct CodeScene CLI installation.
> - Do not restore a pull-request CodeScene check, in any mode, on any
>   condition. The whole point is that pull requests never reach CodeScene.
> - Do not populate, rename, or preserve `CODESCENE_CLI_SHA256`.
> - Do not "fix" floating tags nested inside a composite action. Record them.
