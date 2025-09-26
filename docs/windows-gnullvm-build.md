# Building for Windows `*-pc-windows-gnullvm`

This document describes adapting the `rust-build-release` action to build for Windows `gnullvm` targets. The configuration produces MinGW-style artefacts on a Windows runner using an MSVC host toolchain while linking against LLVM's MinGW CRT provided by `llvm-mingw`, thereby removing any dependency on `libgcc`. Both `x86_64-pc-windows-gnullvm` and `aarch64-pc-windows-gnullvm` are supported.

The process is orchestrated through Python scripts within the action, which replace the PowerShell examples from the original recommendation.

## Strategy Overview

1. **Host vs. Target**: The build runs on a `windows-latest` GitHub Actions runner, which uses the `stable-x86_64-pc-windows-msvc` toolchain as the _host_. This ensures any build scripts (`build.rs`) compile with MSVC and do not introduce GCC runtime dependencies. The _target_ is one of the `*-pc-windows-gnullvm` triples (currently `x86_64` or `aarch64`), which instruct `rustc` to produce a MinGW-style binary.
2. **`cross` Local Backend**: Instead of using Docker (which is unavailable on Windows runners for Linux containers), `cross` is instructed to use its "local backend" via the `CROSS_NO_DOCKER=1` environment variable. This uses the host's toolchains directly.
3. **LLVM Linker**: The key is to tell `rustc` how to link the `gnullvm` target. `clang` and `lld` from the `llvm-mingw` project are used, configured via a dynamically generated `.cargo/config.toml` file.
4. **Python Orchestration**: All setup steps—downloading `llvm-mingw`, creating the Cargo config, and setting environment variables—are handled by a dedicated Python script within the action, ensuring cross-platform consistency and maintainability.

## Implementation within `rust-build-release`

The following changes adapt the recommendation for the existing action.

### 1. GitHub Actions Workflow (`action.yml`)

A conditional step is added to `rust-build-release/action.yml` to trigger the setup whenever a Windows `gnullvm` target is requested. The target triple is passed through to the setup script.

```yaml
# .github/actions/rust-build-release/action.yml
# ...
    - name: Configure gnullvm target on Windows
      if: runner.os == 'Windows' && endsWith(inputs.target, '-pc-windows-gnullvm')
      shell: bash
      working-directory: ${{ inputs.project-dir }}
      run: |
        set -euo pipefail
        uv run --script "$GITHUB_ACTION_PATH/src/setup_gnullvm.py" --target "${{ inputs.target }}"
    - name: Build release
# ...
```

The composite action installs `uv` earlier via the "Setup uv" step, so the snippet
does not need to install it explicitly.

### 2. Setup Script (`setup_gnullvm.py`)

The Python helper automates the setup process previously handled by PowerShell. It performs the following actions:

- **Downloads `llvm-mingw`**: Fetches the specified release archive from GitHub, extracts it to a temporary runner directory, and adds its `bin` directory to the `GITHUB_PATH`. On GitHub’s Windows runners (`x86_64` hosts), the setup defaults to the `ucrt-x86_64` archive, which bundles cross-compilers for both the `x86_64` and `aarch64` Windows targets. The `RBR_LLVM_MINGW_VARIANT` environment variable can override this default.
- **Creates `.cargo/config.toml`**: Generates the necessary configuration to instruct Cargo how to link the requested `gnullvm` target using the matching `*-w64-mingw32-clang` frontend.
- **Sets Environment Variables**: Writes the `CROSS_NO_DOCKER=1` flag and the target-scoped `CC_*`, `CXX_*`, `AR_*`, and `RANLIB_*` variables to `GITHUB_ENV` for the subsequent build step to use.
- **Supports Overrides**: Optional `RBR_LLVM_MINGW_VERSION`, `RBR_LLVM_MINGW_VARIANT`, and `RBR_LLVM_MINGW_SHA256` environment variables allow selecting alternative releases while keeping checksum verification enabled.

The default archive variant is `ucrt-x86_64`, matching the upstream `llvm-mingw` distribution that links against the UCRT and provides cross-compilers for multiple targets. The setup script supports variants that share the upstream archive layout, including `ucrt-arm64`, `ucrt-i686`, `msvcrt-x86_64`, and `msvcrt-i686`. When overriding `RBR_LLVM_MINGW_VARIANT`, set `RBR_LLVM_MINGW_SHA256` to the checksum for the chosen archive to keep checksum validation enabled.

### 3. Build Script (`main.py`)

The main build script, `src/main.py`, is modified to recognise when the `cross` local backend should be used, even when no container runtime (Docker/Podman) is detected.

```python
# src/main.py (excerpt)
# ...
    use_cross_local_backend = (
        os.environ.get("CROSS_NO_DOCKER") == "1" and sys.platform == "win32"
    )
    use_cross = cross_path is not None and (has_container or use_cross_local_backend)
# ...
```

This ensures that `cross` is invoked correctly when the setup script has prepared the environment for a local `gnullvm` build.

## How to Use

To build for a Windows `gnullvm` target, set the `target` input in a workflow that uses the `rust-build-release` action on a `windows-latest` runner. The action performs the setup automatically.

```yaml
# .github/workflows/example-workflow.yml
# ...
jobs:
  build-windows-gnullvm:
    runs-on: windows-latest
    strategy:
      matrix:
        target:
          - x86_64-pc-windows-gnullvm
          - aarch64-pc-windows-gnullvm
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/rust-build-release
        with:
          target: ${{ matrix.target }}
          # ... other inputs like project-dir, version, etc.

The matrix workflow above validates that both Windows `gnullvm` targets are configured correctly.
```
