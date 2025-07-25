
# Changelog

## v1.0.8 - 2025-07-24

- Build OpenBSD standard library using the updated `library/std` path.
- New `openbsd-nightly` input allows specifying the pinned nightly toolchain.

## v1.0.7 - 2025-07-24

- Quote boolean defaults in `action.yml` to avoid type mismatches.

## v1.0.6 - 2025-07-24

- Cache OpenBSD standard library build and only rebuild on cache miss.
- Make macOS SDK version configurable via `darwin-sdk-version` input.
- Scope OpenBSD target installation to Linux runners.
- Fix README example indentation.

## v1.0.5 - 2025-07-24

- Install macOS cross build toolchain via `with-darwin`.
- Build OpenBSD standard library and add target via `with-openbsd`.

## v1.0.5 – 2025-07-22

- Integrate `sccache` on non-release runs to speed up compilation.
- New `use-sccache` input controls this behaviour and caches `~/.cache/sccache`.
- Pin sccache setup via `sccache-action-version` input (default `v0.0.10`).

## v1.0.4 - 2025-06-21

- Optionally install SQLite development libraries on Windows via MSYS2 using the
  `install-sqlite-deps` input.

## v1.0.3 - 2025-06-20

- Install PostgreSQL client libraries on Windows via Chocolatey.

## v1.0.2 - 2025-06-20

- Document `BUILD_PROFILE` environment variable and fix README example

## v1.0.1 - 2025-06-20

- Document caching requirements, limitations and clarify when caches are saved.

## v1.0.0 - 2025-06-20
- Initial version.
