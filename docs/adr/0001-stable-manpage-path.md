# ADR 0001: Stable Man-Page Path for Release Staging

**Status:** Accepted  
**Date:** 2026-05-01

## Context

The `rust-build-release` staging step previously located man pages by scanning
the hash-dependent Cargo build-output directory
`target/<triple>/release/build/<crate>-<hash>/out/`. This path is
non-deterministic: it changes when Cargo's content-addressed build graph
changes, and it can produce multiple matches when incremental build artefacts
accumulate. Staging failures caused by empty or ambiguous glob results were a
recurring source of CI breakage.

## Decision

Introduce a deterministic man-page output location:

```text
target/generated-man/<TARGET>/<PROFILE>/<bin>.1
```

The consuming project's `build.rs` is responsible for writing the man page to
this location. It derives `target/` from `CARGO_TARGET_DIR` (when set) or by
walking `OUT_DIR`'s ancestor structure. The staging action reads the stable
path directly and falls back to the legacy glob only when the stable file is
absent, emitting a `::warning::` annotation on fallback.

## Consequences

* **Positive:** Staging is decoupled from Cargo's content-addressed build
  directories. The path is predictable in CI logs and scripts.
* **Positive:** `cargo:rerun-if-env-changed=CARGO_TARGET_DIR` ensures the
  build script reruns when `cross` changes the container-mounted target
  directory, preventing stale-cache failures.
* **Negative:** Consuming projects must update their `build.rs` to write to the
  stable location. Build scripts that have not been updated will trigger the
  fallback warning until they adopt the new convention.
* **Neutral:** The legacy glob fallback is retained indefinitely to avoid
  breaking existing consumers on day one of adoption.
