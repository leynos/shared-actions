# Building for `x86_64-pc-windows-gnullvm`

This document describes adapting the `rust-build-release` action to build for the `x86_64-pc-windows-gnullvm` target. The configuration produces MinGW-style artefacts on a Windows runner using an MSVC host toolchain while linking against LLVM's MinGW CRT provided by `llvm-mingw`, thereby removing any dependency on `libgcc`.

The process is orchestrated through Python scripts within the action, which replace the PowerShell examples from the original recommendation.

## Strategy Overview

1. **Host vs. Target**: The build runs on a `windows-latest` GitHub Actions runner, which uses the `stable-x86_64-pc-windows-msvc` toolchain as the _host_. This ensures any build scripts (`build.rs`) compile with MSVC and do not introduce GCC runtime dependencies. The _target_ is `x86_64-pc-windows-gnullvm`, which instructs `rustc` to produce a MinGW-style binary.
2. **`cross` Local Backend**: Instead of using Docker (which is unavailable on Windows runners for Linux containers), `cross` is instructed to use its "local backend" via the `CROSS_NO_DOCKER=1` environment variable. This uses the host's toolchains directly.
3. **LLVM Linker**: The key is to tell `rustc` how to link the `gnullvm` target. `clang` and `lld` from the `llvm-mingw` project are used, configured via a dynamically generated `.cargo/config.toml` file.
4. **Python Orchestration**: All setup steps—downloading `llvm-mingw`, creating the Cargo config, and setting environment variables—are handled by a dedicated Python script within the action, ensuring cross-platform consistency and maintainability.

## Implementation within `rust-build-release`

The following changes adapt the recommendation for the existing action.

### 1. GitHub Actions Workflow (`action.yml`)

A conditional step is added to `rust-build-release/action.yml` to trigger the setup only when building the `gnullvm` target on a Windows runner.

```yaml
# .github/actions/rust-build-release/action.yml
# ...
    - name: Configure gnullvm target on Windows
      if: runner.os == 'Windows' && inputs.target == 'x86_64-pc-windows-gnullvm'
      shell: bash
      run: |
        set -euo pipefail
        uv run --script "$GITHUB_ACTION_PATH/src/setup_gnullvm.py"
    - name: Build release
# ...
```

### 2. Setup Script (`setup_gnullvm.py`)

This new Python script automates the entire setup process previously handled by PowerShell. It performs the following actions:

- **Downloads `llvm-mingw`**: Fetches the specified release archive from GitHub, extracts it to a temporary runner directory, and adds its `bin` directory to the `GITHUB_PATH`.
- **Creates `.cargo/config.toml`**: Generates the necessary configuration to instruct Cargo how to link the `gnullvm` target.
- **Sets Environment Variables**: Writes the `CROSS_NO_DOCKER=1` flag and the target-scoped `CC_*`, `CXX_*`, etc., variables to `GITHUB_ENV` for the subsequent build step to use.
- **Supports Overrides**: Optional `RBR_LLVM_MINGW_VERSION` and `RBR_LLVM_MINGW_SHA256` environment variables allow selecting alternative releases while keeping checksum verification enabled.

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

To build for `x86_64-pc-windows-gnullvm`, set the `target` input in a workflow that uses the `rust-build-release` action on a `windows-latest` runner. The action performs the setup automatically.

```yaml
# .github/workflows/example-workflow.yml
# ...
jobs:
  build-windows-gnullvm:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/rust-build-release
        with:
          target: x86_64-pc-windows-gnullvm
          # ... other inputs like project-dir, version, etc.
```
