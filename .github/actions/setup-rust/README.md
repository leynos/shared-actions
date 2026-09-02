# Setup Rust

Install the Rust toolchain, cargo-binstall, and cache your build dependencies.
Optionally install PostgreSQL and SQLite system libraries for crates that
require them, and set up macOS or OpenBSD cross-compilers.

## Inputs

<!-- markdownlint-disable MD013 -->

| Name                  | Description                                                                                                                                                                  | Required | Default                               |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ------------------------------------- |
| toolchain             | Rust toolchain to install (e.g., `stable`, `nightly`, `1.70.0`). If omitted, uses `.rust-toolchain.toml` when present, otherwise `stable`.                                   | no       | _see description_                     |
| install-postgres-deps | Install PostgreSQL system dependencies                                                                                                                                       | no       | `false`                               |
| workspaces            | Cargo workspace to target mappings for `Swatinem/rust-cache`. Each non-empty line must use the format `workspace -> target`; leave empty to cache the default `. -> target`. | no       | _(empty)_ (defaults to `. -> target`) |
| install-sqlite-deps   | Install SQLite dev libraries (Windows)                                                                                                                                       | no       | `false`                               |
| use-sccache           | Enable sccache for non-release runs                                                                                                                                          | no       | `true`                                |
| cache-provider        | Use the built-in `github` Cargo and uv caches, or `external` when the caller mounts one cache owner                                                                          | no       | `github`                              |
| install-binstall      | Install cargo-binstall for faster binary crate installations                                                                                                                 | no       | `true`                                |
| with-darwin           | Install macOS cross build toolchain                                                                                                                                          | no       | `false`                               |
| darwin-sdk-version    | macOS SDK version for osxcross                                                                                                                                               | no       | `12.3`                                |
| with-openbsd          | Build OpenBSD std library for cross-compilation                                                                                                                              | no       | `false`                               |
| openbsd-nightly       | Pinned nightly Rust for OpenBSD                                                                                                                                              | no       | `nightly-2025-07-20`                  |
| rustflags             | `RUSTFLAGS` exported by the toolchain setup step. Set to the empty string to leave `RUSTFLAGS` unset, so an inherited value or the project's `build.rustflags` applies.      | no       | `-D warnings`                         |

<!-- markdownlint-enable MD013 -->

## Outputs

None

## Example

```yaml
- uses: ./.github/actions/setup-rust
  with:
    toolchain: 'nightly'
    install-postgres-deps: 'true'
    install-sqlite-deps: 'true'
    use-sccache: 'false'
    cache-provider: external
    with-darwin: true
    with-openbsd: true
```

The action installs `cargo-binstall` by default. Set
`install-binstall: 'false'` to skip this step. If you bump the pinned
`cargo-binstall` version, update the corresponding SHA-256 in the action
manifest at the same time. Keep `BINSTALL_VERSION` exported in the install
step: the downloaded installer runs in a child shell and reads
`BINSTALL_VERSION` from the environment. A plain shell variable is not
inherited, which makes the installer fall back to `releases/latest`. You can
obtain the new checksum by replacing `VERSION` with the desired tag (for
example, `v1.19.1`) and running:

```bash
VERSION="v1.19.1"
BASE_URL="https://raw.githubusercontent.com/cargo-bins/cargo-binstall"
curl -fsSL "${BASE_URL}/${VERSION}/install-from-binstall-release.sh" \
  | shasum -a 256 | awk '{print $1}'
```

When `install-postgres-deps` is enabled, the action installs PostgreSQL client
libraries via the package manager for the runner OS. On Linux, it uses `apt`
(`libpq-dev`). On Windows, Chocolatey installs `postgresql17` and exposes its
headers and import libraries through `PG_INCLUDE` and `PG_LIB` environment
variables.

When `install-sqlite-deps` is enabled, the action installs SQLite development
files using MSYS2 on Windows.

SQLite support on Windows is enabled by setting up an MSYS2 environment with
the MinGW toolchain and the `mingw-w64-x86_64-sqlite3` package, so the static
library and headers are available when compiling crates that depend on SQLite.

When `with-darwin` is enabled, the action installs the osxcross toolchain on
Linux so that Rust crates can be cross-compiled for macOS. The SDK version can
be configured via the `darwin-sdk-version` input and defaults to `12.3`. The
`x86_64-apple-darwin` and `aarch64-apple-darwin` Rust targets are installed so
that Cargo can produce macOS binaries.

When `with-openbsd` is enabled, the action installs the nightly toolchain
specified by the `openbsd-nightly` input (default `nightly-2025-07-20`), builds
the OpenBSD standard library from the Rust source tree, installs it into
`rustup`, and caches the result so that the `x86_64-unknown-openbsd` target is
readily available on later runs.

```yaml
      # Bring in MSYS2 plus the MinGW build of SQLite
      - name: Install MSYS2 toolchain and SQLite
        uses: msys2/setup-msys2@66cd2cce69caa17b53920067426061ca1de3a884 # v2.32.0
        with:
          msystem: MINGW64
          update: true
          install: >-
            mingw-w64-x86_64-toolchain
            mingw-w64-x86_64-sqlite3       # ships libsqlite3.a + headers

      # Build inside the MSYS2 shell so the linker sees /mingw64/lib
      - name: Build
        shell: msys2 {0}
        run: cargo build --workspace --all-targets --verbose
```

## Caching

With the default `cache-provider: github`, this action uses `actions/cache` for
the Cargo registry, Cargo Git dependencies, and the configured build-profile
target directory. `setup-uv` retains its historical automatic policy: its
GitHub cache is enabled on GitHub-hosted runners and disabled on self-hosted
runners. These archive caches are restored during setup and saved after the
job.

Set `cache-provider: external` when the caller mounts those paths through one
other cache service, such as a Namespace cache volume. External mode disables
both nested GitHub archive caches; it does not mount a replacement itself. The
caller must establish the external cache before installing dependencies or
building so each path has exactly one cache owner.

Example using an external cache owner:

```yaml
  - name: Set up Rust
    uses: ./.github/actions/setup-rust
    with:
      cache-provider: external
      use-sccache: 'false'

  - name: Mount the caller-owned cache
    uses: namespacelabs/nscloud-cache-action@c5f8dab7560444c4bf8dbc64f1b203431873c547
    with:
      cache: rust
```

When the workflow is not triggered by a `release` event and `use-sccache` is
enabled, the action also runs [sccache](https://github.com/mozilla/sccache) to
cache compiler output. The sccache action sets `SCCACHE_GHA_ENABLED=true` and
`RUSTC_WRAPPER=sccache` so subsequent build steps benefit from the cache. The
compiled objects are stored in `~/.cache/sccache` and cached with a **separate
cache key** from the directories above. This directory holds the sccache cache
space and does not share data with the Rust dependency cache. The revised
Node.js-backed actions are pinned to specific commits for reproducibility:
`actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9`,
`mozilla-actions/sccache-action@fc920bf0ec8de6ee65d409111f7ec508035751ba`
and `msys2/setup-msys2@66cd2cce69caa17b53920067426061ca1de3a884`.

An external cache does not replace this compiler-cache backend automatically.
Callers that use a local cache volume for sccache must pass
`use-sccache: 'false'`, install a trusted prebuilt sccache binary into a cached
path, set `RUSTC_WRAPPER=sccache`, and mount its cache directory themselves.
The action writes a bounded `hit`, `miss`, `disabled`, or `error` observation
for each archive cache to the workflow log and job summary. External cache hit
telemetry remains the caller's responsibility because this action does not
mount that cache.

### Extent and limitations

- GitHub limits the total cache size to 5 GB per repository and OS, so old
  entries may be evicted.
- Caches are scoped to the runner OS; Linux, macOS, and Windows caches are
  independent.
- The cache is best-effort: if the key changes or the cache is evicted, the
  build will proceed without cached artefacts.

### Effective use

- Keep `rust-toolchain.toml` and `Cargo.lock` files checked in to ensure stable
  cache keys.

Release history is available in [CHANGELOG](CHANGELOG.md).
