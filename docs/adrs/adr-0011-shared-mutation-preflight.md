# ADR 0011: One shared mutation environment preflight

**Status:** Accepted **Date:** 2026-11-19

## Context

Both mutation reusable workflows grew their setup one failure at a time. The
failures recorded in issue #591 all happened before a mutant was exercised: a
file missing from the copied sandbox, an interpreter outside the project's
range, an external command never installed, and a native dependency built for
an unsupported interpreter. Each was repaired in the repository that hit it,
with one `also_copy` entry, one `apt-get` line or one version pin, so the same
class of failure kept returning.

The environment those failures belong to is specific. The sandbox is mutmut's
copy, not the checkout; the commands are the ones the lane's own install steps
provide; and the native extension is built under the project's interpreter, not
the workflow's helper. A check placed anywhere else measures a different
environment from the one the mutants will run in.

## Options considered

### Option A: Per-repository installer scripts

Each caller writes a script that installs what its mutation run needs and
checks what it can. Rejected: the script duplicates knowledge of the lane's
steps and of mutmut's copy contract, and the check still records a different
environment from the one that runs. Fixing a class of failure still means
editing every consumer.

### Option B: Inline steps in each reusable workflow

Each reusable workflow carries its own checks as steps. Rejected: the same
checks are written twice, once per language, and a fix to one lane leaves the
other measuring a different set. The steps have no shared vocabulary for a
declaration, so each caller's requirements would be hard-coded in the workflow.

### Option C: A per-language preflight in each reusable workflow

The mutmut and cargo lanes each ship a preflight of their own. Rejected: the
two lanes validate the same things — the interpreter range against the project,
the commands, and the native dependency — and splitting the rule lets the same
declaration mean two different things. What differs per lane is the copy and
check boundary, not the requirements.

### Option D: One shared preflight contract in a single helper

The caller declares its requirements once, and one helper checks them in the
sandbox the selected lane will build. The mutmut and cargo lanes differ in what
they check — `sandbox_paths` applies only to mutmut — not in how a requirement
is expressed or judged.

| Topic                       | Per-repository scripts  | Inline workflow steps           | Shared helper |
| --------------------------- | ----------------------- | ------------------------------- | ------------- |
| Measures the real sandbox   | No                      | No                              | Yes           |
| One schema for both lanes   | No                      | No                              | Yes           |
| Fixed once for every caller | No                      | No                              | Yes           |
| Caller work to adopt        | A script and its upkeep | Nothing until a check is needed | A declaration |

_Table 1: How the options compare on the criteria that motivated the decision._

## Decision

One shared preflight contract, declared by the caller and checked by one helper:

- The declaration lives in the caller's `pyproject.toml` under
  `[tool.mutation-preflight]` or in `.github/mutation-preflight.toml`, never
  both. The helper owns the schema, refuses unknown keys, and refuses a
  requirement the lane cannot check.
- `workflow_scripts/mutation_preflight.py`, with its phases in
  `workflow_scripts/mutationpreflight/`, is the check. Both workflows expose an
  opt-in `preflight` input, default `false`, so no existing caller starts
  checking requirements it has not declared.
- The mutmut lane runs the preflight after change detection and before the
  mutation run. The cargo lane runs it per shard, after the steps that install
  the tools the declaration names, because that is where those commands exist
  and nowhere else in the job graph.
- The sandbox phase compares the declaration with the copy contract of the
  selected mutmut release, and the checks phase runs inside a sandbox built
  from it. A release the helper does not know is reported `unknown` rather than
  assumed to copy what a known release copies.
- Every phase publishes its evidence in one streaming envelope as it
  finishes, so a crash in a later phase still leaves the earlier verdicts in
  the log, and the JSON report exists whether or not mutation results do.
- The phases touch the checkout only through purpose-shaped ports —
  `DocumentReader`, `LockReader`, `ConfigurationReader`, `PathLocator`,
  `SandboxProvider` and `EvidenceWriter` — carried on `Run` as `documents`,
  `lock`, `configuration`, `paths`, `sandbox` and `evidence`, and implemented by
  `DeclarationDocuments`, `LockRecords`, `MutmutConfiguration`,
  `CheckoutPaths`, `SandboxMaterializer` and `EvidenceFiles` in
  `repository.py`. A judgement receives parsed documents, lock records, the
  configuration, located paths and a materialized sandbox, and asks for its
  logs, summary and report to be written; a phase that reaches past the ports
  is refused by the boundary test rather than trusted, and a query phase's
  signature does not admit the runner at all.
- The native judgement runs after the declared checks. On the cargo lane those
  checks run in the checkout and can rewrite `uv.lock`, so the lock's identity
  is read before the checks and read again by the judgement; an identity that
  moved fails the native phase and withholds its probe, rather than certifying
  a pin the mutation run will not use.
- The interpreter phase reuses `requirement_violations` from
  `packages/cv005-contracts` — the rule closed in issue #565 — rather than
  restating it, so a bare `X.Y` is decided in one place.
- The helper's runtime and the project's runtime are judged and reported
  separately. The project's interpreter is validated before any phase that
  would sync under it, and a rejected interpreter gates those phases.
- Where support cannot be established, the phase reports `unknown` and names
  what it could not decide, rather than guessing. A run fails only on `fail`.

## Consequences

- The helper owns the declaration schema and its versioning. Adding a key,
  removing one or changing what a key means is a change every caller that
  declares it sees. Unknown keys are refused, so a stale declaration fails
  loudly instead of silently checking less.
- Callers declare requirements rather than install them in repository-specific
  scripts. A requirement a lane does not install remains the caller's job;
  naming it in the declaration makes a missing one a named phase rather than a
  baseline failure attributed to a mutant.
- The contract a declared check runs under is explicit: `cwd`, `env` and the
  relative `PYTHONPATH` import root. On the mutmut lane a helper module or
  fixture directory a check needs must be named in `also_copy` or
  `source_paths`, because the check runs in the sandbox and not in the checkout.
- Unknown is preferred to a guess. A caller may see `unknown` for an
  interpreter range, a native support boundary or a mutmut release the helper
  does not know; it is surfaced in the report and does not block the run.
- A repository that declares nothing is unaffected apart from the interpreter
  phase; the declaration-backed phases report `skipped`.
- `sandbox_paths` is refused on the cargo lane, because cargo-mutants copies
  the whole working tree and mutmut's copy set is not its contract.
- The diagnostics are an artefact the caller must read. Both workflows upload
  the output directory under `always()`, and a missing directory is an error
  rather than nothing to publish.

## References

- [Mutation environment preflight](../mutation-preflight.md)
- [Developer guide](../developers-guide.md#mutation-environment-preflight)
- [Issue #591](https://github.com/leynos/shared-actions/issues/591)
- [PR #698](https://github.com/leynos/shared-actions/pull/698)
