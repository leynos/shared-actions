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
the Cargo registry and Cargo Git dependencies only. The `target` directory is
deliberately uncached: sccache is the sole owner of compiler output. `setup-uv`
retains its historical automatic policy: its GitHub cache is enabled on
GitHub-hosted runners and disabled on self-hosted runners. These archive caches
are restored during setup and saved after the job.

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
cache compiler output. The sccache action exports `SCCACHE_PATH`, naming the
binary it installed. It sets neither `RUSTC_WRAPPER` nor
`SCCACHE_GHA_ENABLED`, so this action sets both:

- `RUSTC_WRAPPER`, naming the installed binary, because Cargo routes
  compilation through sccache only when that variable is set. Without it
  sccache is installed and never used.
- `SCCACHE_GHA_ENABLED`, because sccache otherwise writes to local disk, which
  nothing persists between jobs. It is exported **before** the sccache steps.
  sccache binds its backend once, at server start, and `GITHUB_ENV` reaches
  only the next step, so an export written alongside the wrapper would come too
  late for the `--zero-stats` in that same step. Those sccache steps start no
  server themselves; what they do is force `ACTIONS_CACHE_SERVICE_V2=on`, which
  is issue `#441` and not this ordering. The selection order is: an explicit
  `SCCACHE_GHA_ENABLED` wins,
  `false` and empty included; failing that, a caller-set `SCCACHE_DIR` leaves
  sccache on their directory; otherwise the GitHub Actions backend is chosen.
  Each run reports `metric setup-rust.sccache.backend=<gha|local|caller>`.

A caller that has already set `RUSTC_WRAPPER` keeps its value, and the action
says so in a notice. Statistics are zeroed after the export, so a later
`sccache --show-stats` measures the caller's own build.

Where the compiled objects go follows from the backend. On the GitHub Actions
backend, the `ghac` arm, sccache stores them through the cache service; there is
no local directory and no cache key of this action's own. The local backend is
everything else: an explicit `SCCACHE_GHA_ENABLED` that is not true-like, which
includes `false` and an empty value, or a caller-selected `SCCACHE_DIR`. sccache
reads that variable as a boolean and treats empty as false, so a caller who
clears it gets local disk exactly as one who wrote `false` does. Objects then go
to that directory, defaulting to `~/.cache/sccache`. This action does not
archive that directory; a lane that wants it to survive between jobs owns the
cache step and its key, which must be
separate from the Rust dependency cache above, because the two hold unrelated
data.

On Ubicloud, run the
[`export-ubicloud-cache-credentials`](../export-ubicloud-cache-credentials)
action **before** `setup-rust`. Without it the GitHub Actions backend cannot
reach Ubicloud's store, and the compiler cache silently falls back to whatever
the runner advertises. The revised
Node.js-backed actions are pinned to specific commits for reproducibility:
`actions/cache@55cc8345863c7cc4c66a329aec7e433d2d1c52a9`,
`mozilla-actions/sccache-action@fc920bf0ec8de6ee65d409111f7ec508035751ba`
and `msys2/setup-msys2@66cd2cce69caa17b53920067426061ca1de3a884`.

### Sizing `SCCACHE_CACHE_SIZE`

Repositories across this estate build in two shapes: a debug or dev-fast tree
built with Cranelift and linked with mold for lint and test, and an
instrumented `target/llvm-cov-target` tree built with the LLVM backend for
coverage. Both shapes coexist in one sccache store because sccache keys entries
by compiler flags, so objects from the two shapes never collide. Measured runs
confirm this: Whitaker run 33744418209 (coverage under `-C instrument-coverage`)
and Cuprum run 33677926269 (Cranelift-built Whitaker lints) each report
`Non-cacheable compilations 0`.

sccache defaults to a 10 GiB store. Under this action's GitHub Actions backend
(`SCCACHE_GHA_ENABLED=true`) GitHub's own per-repository cache limit applies
instead, so no sizing input is exposed here. Callers who self-manage a local
sccache directory should raise `SCCACHE_CACHE_SIZE` above the default so one
store holds both build shapes rather than evicting one to make room for the
other.

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
