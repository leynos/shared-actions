# ADR 0005: setup-rust selects the sccache backend by runner

**Status:** Accepted **Date:** 2026-09-24

## Context

[ADR 0003](0003-sccache-owns-rust-compiler-output.md) made sccache the sole
owner of compiler output, and its 2026-09-04 addendum made `setup-rust` export
`SCCACHE_GHA_ENABLED=true` so that sccache writes to a cache that outlives the
job. That export chose one backend for every runner. Which service it reached
was decided somewhere else.

On Ubicloud the service is a cache proxy on the runner's private network.
sccache reaches it only once `export-ubicloud-cache-credentials` has republished
`ACTIONS_CACHE_URL` and `ACTIONS_RUNTIME_TOKEN` through `GITHUB_ENV` and
cleared `ACTIONS_CACHE_SERVICE_V2`, because the runner hands those variables to
JavaScript action steps only. That action fails the job by design when the URL
is missing or public, which is right for a job that must run on Ubicloud. It is
wrong for a job with a fork fallback: a fork's pull request cannot obtain an
Ubicloud runner and falls back to a GitHub-hosted one, where the credentials
step would turn a required check red. cuprum #467 carried a handwritten guard
and a matching backend switch for exactly this, and every consumer with a fork
fallback would have had to derive the same logic.

`runner.environment` cannot make the distinction. On a fork-fallback job its
value varies with the event, so a condition written on it describes the event
rather than the runner.

## Decision

`setup-rust` selects the sccache backend itself, in a JavaScript step placed
before the sccache-action steps, from what the runner actually offers:

- **Ubicloud.** `ACTIONS_CACHE_URL` names a private address literal (a full
  IPv4 or IPv6 unique-local literal, so a name that merely resolves privately
  is refused) and a runtime token is present. The step masks and exports both
  credentials, clears `ACTIONS_CACHE_SERVICE_V2` because the proxy serves v1,
  and sets `SCCACHE_GHA_ENABLED=true`. Credentials already exported by
  `export-ubicloud-cache-credentials`, recognized by the cleared
  `ACTIONS_CACHE_SERVICE_V2` it leaves behind, are not exported again.
- **GitHub-hosted.** The URL is missing or public and a runtime token is
  present. The credentials are left alone, and sccache writes to a local
  directory under the runner's temporary directory, which `setup-rust` restores
  and saves itself with `actions/cache` (see "The hosted arm" below). The
  output reports `local`.
- **Local.** Under nektos/act, or with no runtime token, there is no service
  sccache can use. Local disk is selected, never a failed build.

The caller still decides whether sccache uses a service at all. An explicit
`SCCACHE_GHA_ENABLED` or `SCCACHE_GHA_VERSION` wins, `false` and empty
included, and is read the way sccache 0.17 reads it: on a GitHub-hosted runner
an enabled switch is the one way to get GitHub's service, reported as `github`.
Failing that, a caller's `SCCACHE_DIR` selects local disk, and the action
caches nothing of it. The runner decides only which service.

### The hosted arm

On a GitHub-hosted runner left to the action, sccache uses local disk, not
GitHub's cache service. The service measured 0.28 s per hit against 0.42 s per
compile on chutoro, spending most of what a hit saves, and whitaker's Windows
lane had all 643 of its writes rejected. The same Windows lane against a local
directory under `actions/cache` recorded no read or write errors and a 78 %
warm hit rate. Consumers had been hand-rolling that local arm; `setup-rust` now
owns it (user ruling, 2026-09-24).

- The directory is `${{ runner.temp }}/sccache`, exported as `SCCACHE_DIR`.
- The cache key is one lane's:
  `sccache-<OS>-<arch>-<compiler>-<discriminator>-<lockfile>-<run id>`. The
  compiler is a checksum of `rustc -vV`. The discriminator is the
  `sccache-cache-discriminator` input, or the job id by default, so matrix
  entries on one runner can keep separate lanes. The restore keys drop the run
  id, then the lockfile, so a rerun takes the trunk's newest entry and a
  dependency bump still starts warm.
- Only a push to the default branch saves, through the full `actions/cache`
  action, whose post-job step saves after the caller's build (a composite
  action has no post step of its own). Every other event restores with
  `actions/cache/restore` and never writes. The trunk is the single writer,
  which is the estate's cache-ownership rule and the lesson of the ref-scoped
  proxy: a pull request can read only its own scope and the default branch's,
  so a pull request's save would warm nothing but itself.
- `cache-provider: external` hands every cache path to the caller, so the
  action then selects local disk without owning or caching the directory.

The private-address check is the one in `export-ubicloud-cache-credentials`,
copied, because a composite action cannot share a JavaScript module without
reaching outside its own directory. A contract holds the two copies identical.

A new input, `expect-cache` (`ubicloud`, `github` or `any`, default `any`),
lets a job require a backend. A job pinned to Ubicloud with no fork fallback
sets `expect-cache: ubicloud`, so a missing proxy still fails the job loudly,
as the credentials action does today. Silent fallback to local disk on such a
job would be worse than a red build.

The choice is observable three ways, none of which carries a URL, host path or
token: the action output `cache-backend` (`ubicloud`, `github` or `local`), the
fixed-name line `metric setup-rust.sccache.backend=<value>` over the same
closed set, and a notice naming the backend and who chose it.

## Consequences

A consumer calls `setup-rust` alone and gets a working compiler cache on either
kind of runner, with no guard of its own. Fork-fallback jobs need no backend
switch, and hosted lanes no longer hand-roll a local sccache cache.

Each trunk push saves a new directory entry, and GitHub's per-repository limit
evicts the oldest. A pull request's first run is warm from the default branch
and never cold for want of its own save.

`export-ubicloud-cache-credentials` stays, with its current contract. It is
still correct for jobs that need the credentials for something other than
`setup-rust`, and a job that calls both is harmless. Consumers drop the
explicit call in their next pull request that touches CI; there is no sweep.

Caches do not cross between Ubicloud's proxy and GitHub's cache service. A
fork's run on a GitHub-hosted runner will not reuse the warm cache that runs on
Ubicloud wrote, and this decision does not try to bridge the two.

A job that relied on the old default of GitHub's service on a hosted runner now
gets local disk. One that wants the service sets `SCCACHE_GHA_ENABLED`.

The metric's values change from `gha`, `local` and `caller` to `ubicloud`,
`github` and `local`. A reader scraping the old values must follow. Who chose
the backend now appears in the notice rather than in the metric value.

A fork's pull request lands on the hosted arm. A fork-fallback job runs on
Ubicloud on the default branch and so saves nothing there, which leaves the
fork arm cold; a hosted-only job's fork restores the default branch's directory
like any other pull request. One real fork run is still owed as live proof of
that arm.

## Implementation notes

`.github/actions/setup-rust/tests/test_sccache_backend.py` runs the shipped
script under Node for every runner and caller case and for `expect-cache`.
`test_sccache_backend_properties.py` holds the closed set, output and metric
agreement, and token confinement over generated environments, and holds the
private-address check identical to the credentials action's. Each branch was
proved by mutation, including a prefix match that would accept the DNS name
`10.attacker.example`.

`test_sccache_directory_cache.py` holds the hosted arm's key composition, the
trunk-only save predicate and its complement, and that both arms read one
cache, each proved by mutation.

`.github/workflows/test-ubicloud-sccache-proxy.yml` proves the Ubicloud arm on
a real runner with `expect-cache: ubicloud` and no credentials step.
`.github/workflows/test-setup-rust-sccache.yml` proves the hosted arm: it
asserts `local` and a `Local disk` location, runs on pushes to main so the
trunk saves, and, whenever a directory was restored, requires the toy app's
build to record a cache hit.

## References

- Issue `#521`
- [ADR 0003](0003-sccache-owns-rust-compiler-output.md)
- [ADR 0004](0004-main-owns-codescene-coverage.md), main-owned CodeScene
  coverage, the record numbered before this one
- `docs/developers-guide.md`, "`setup-rust` and the rustc wrapper", "Rust
  action cache ownership" and "`export-ubicloud-cache-credentials` action
  contract"
- `docs/users-guide.md`, "`setup-rust` and sccache"
