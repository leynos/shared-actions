# ADR 0003: sccache owns Rust compiler output

**Status:** Accepted **Date:** 2026-09-03

## Context

The `setup-rust` and `generate-coverage` composite actions each archived a
Cargo `target` tree with `actions/cache` whenever the caller used the default
`github` cache provider. `setup-rust` archived `target/${{ env.BUILD_PROFILE }}`
and keyed the archive by the same placeholder. `BUILD_PROFILE` is never set
anywhere in this repository, so that path resolved to a bare `target/` and the
key never varied by profile. `generate-coverage` archived the whole `target`
tree.

Repositories across this estate build in at least two shapes. Lint and test
builds produce a debug or dev-fast tree built with the Cranelift backend and
linked with mold. Coverage builds produce an instrumented
`target/llvm-cov-target` tree, which must use the LLVM backend because
Cranelift has no `-C instrument-coverage` equivalent.

A single `target` archive can only capture one of those shapes. It is
invalidated on almost every change, and it costs multi-gigabyte restore and
save transfers: the rstest-bdd pilot measured 3.65 GB moved in about 121 s. It
also duplicates work that sccache already performs, because both actions can
enable sccache through `use-sccache`.

Measurements show sccache alone is sufficient. Whitaker run 33744418209
(coverage under `-C instrument-coverage`) and Cuprum run 33677926269
(Cranelift-built Whitaker lints) each report `Non-cacheable compilations 0`, so
LLVM-debug, Cranelift, and instrumented objects coexist in one sccache store
keyed by compiler flags.

## Decision

Neither Rust action archives a `target` tree. sccache is the sole owner of
compiler output.

`setup-rust` caches the Cargo registry and Git index under the
profile-agnostic key
`${{ runner.os }}-cargo-${{ hashFiles('rust-toolchain.toml', '**/Cargo.lock') }}`,
with a matching `${{ runner.os }}-cargo-` restore prefix. `generate-coverage`
caches the `cargo-binstall`, `cargo-llvm-cov`, and `cargo-nextest` binaries
alongside the registry and Git index, under its existing key.

The `cache-provider` boundary is unchanged. `external` still disables the
actions' own archive caches so a caller such as an Ubicloud or Namespace cache
volume owns those paths, and the coverage action still leaves ratchet-baseline
paths under their separate GitHub cache.

No sccache wiring changed. `SCCACHE_CACHE_SIZE` remains documentation rather
than a manifest input, because the actions use the GitHub Actions sccache
backend (`SCCACHE_GHA_ENABLED=true`), which GitHub bounds by its own
per-repository limit rather than by the local-disk value.

## Consequences

Cache restore and save transfers shrink to the dependency archives. Compiler
output is served from one sccache store that holds every build shape, so a
coverage run no longer evicts the lint tree or vice versa.

A cold sccache store costs a full rebuild, where a matching `target` archive
would previously have avoided one. The measured non-cacheable counts show that
case is rare in practice, and the archive rarely matched the current shape
anyway.

Coverage keeps the LLVM codegen backend. mold remains usable as the linker for
instrumented builds.

Callers that self-manage a local sccache directory should raise
`SCCACHE_CACHE_SIZE` above the 10 GiB default so one store holds both build
shapes.

## Implementation Notes

`.github/actions/tests/test_no_target_cache.py` enforces this decision. It
loads both manifests, selects the steps that invoke `actions/cache` or its
`restore` and `save` sub-actions, and fails if any `path` entry contains
`target` as a complete path segment. A companion assertion requires the Cargo
registry to remain cached, so the contract cannot pass by the cache steps
disappearing.

## Addendum, 2026-09-04: what sccache needs to own anything

This decision made sccache the owner of compiler output and removed the
`target` archive that had been standing in for it. It assumed sccache was
working. It was not, for two reasons that surfaced only once the archive was
gone and the cost became visible.

`mozilla-actions/sccache-action` installs sccache and exports `SCCACHE_PATH`.
It exports neither `RUSTC_WRAPPER`, without which Cargo never routes
compilation through sccache, nor `SCCACHE_GHA_ENABLED`, without which sccache
writes to a local disk directory that nothing persists between jobs. A consumer
relying on `setup-rust` therefore had an installed binary, no wrapper, and no
durable cache, while every log line reported sccache as enabled. Chutoro
measured zero compile requests, then 3,836 requests at a 0.18 % hit rate once
the wrapper landed.

`setup-rust` now exports both. The positions are load-bearing:
`SCCACHE_GHA_ENABLED` before the sccache-action steps and `RUSTC_WRAPPER`
after them, because it needs `SCCACHE_PATH`, which they produce. Neither can
move. A caller's own values win in both cases.

The backend export sits first because `GITHUB_ENV` reaches only the next step,
and the first thing in this action to start a server is the `--zero-stats` in
the wrapper step immediately after the sccache steps. sccache binds its backend
once, at server start, so an export written in that same step would be read by
nobody. The sccache-action steps themselves start no server; measurement on
Ubicloud (runs 33854048777 and 33854213968) showed that what they do instead is
write `ACTIONS_CACHE_SERVICE_V2=on` to `GITHUB_ENV`, which is a separate
problem, recorded in `#441`.

The decision itself stands: sccache owns compiler output, and no `target`
archive returns. This records that owning it requires the two exports, which
the original decision took for granted.

## References

- Issue `#424`, PR `#425`
- Issues `#437` and `#439`, PRs `#438` and `#440`
- `docs/developers-guide.md`, "Rust action cache ownership"
- `docs/users-guide.md`, "Rust cache ownership"
