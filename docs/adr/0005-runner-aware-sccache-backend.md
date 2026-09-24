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
  present. The credentials are left alone and sccache's native GitHub Actions
  backend is selected.
- **Local.** Under nektos/act, or with no runtime token, there is no service
  sccache can use. Local disk is selected, never a failed build.

The caller still decides whether sccache uses a service at all. An explicit
`SCCACHE_GHA_ENABLED` or `SCCACHE_GHA_VERSION` wins, `false` and empty
included, and is read the way sccache 0.17 reads it. Failing that, a caller's
`SCCACHE_DIR` selects local disk. The runner decides only which service.

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
switch.

`export-ubicloud-cache-credentials` stays, with its current contract. It is
still correct for jobs that need the credentials for something other than
`setup-rust`, and a job that calls both is harmless. Consumers drop the
explicit call in their next pull request that touches CI; there is no sweep.

Caches do not cross between Ubicloud's proxy and GitHub's cache service. A
fork's run on a GitHub-hosted runner will not reuse the warm cache that runs on
Ubicloud wrote, and this decision does not try to bridge the two.

The metric's values change from `gha`, `local` and `caller` to `ubicloud`,
`github` and `local`. A reader scraping the old values must follow. Who chose
the backend now appears in the notice rather than in the metric value.

Whether sccache's native backend works on a fork's pull request against
GitHub's v2 cache service, with the pinned `mozilla-actions/sccache-action`, is
settled by measurement, not by this record. If it does not, the `github` arm
falls back to `local` on forks in a follow-up.

## Implementation notes

`.github/actions/setup-rust/tests/test_sccache_backend.py` runs the shipped
script under Node for every runner and caller case and for `expect-cache`.
`test_sccache_backend_properties.py` holds the closed set, output and metric
agreement, and token confinement over generated environments, and holds the
private-address check identical to the credentials action's. Each branch was
proved by mutation, including a prefix match that would accept the DNS name
`10.attacker.example`.

`.github/workflows/test-ubicloud-sccache-proxy.yml` proves the Ubicloud arm on
a real runner with `expect-cache: ubicloud` and no credentials step, and
`.github/workflows/test-setup-rust-sccache.yml` proves the GitHub-hosted arm.

## References

- Issue `#521`
- [ADR 0003](0003-sccache-owns-rust-compiler-output.md)
- `docs/developers-guide.md`, "`setup-rust` and the rustc wrapper", "Rust
  action cache ownership" and "`export-ubicloud-cache-credentials` action
  contract"
- `docs/users-guide.md`, "`setup-rust` and sccache"
