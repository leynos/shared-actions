# Setup Windows GNU Toolchain

Installs the dependencies required to build GNU-targeted Rust binaries on
Windows runners. The action provisions MinGW toolchains via MSYS2 and downloads
an `llvm-mingw` release to supply the AArch64 cross linker.

## Inputs

| Name | Required | Default | Description |
| --- | --- | --- | --- |
| `llvm-mingw-version` | No | `20250910` | Release identifier for the `llvm-mingw` archive to install. |
| `llvm-mingw-sha256` | No | `bd88084d7a3b95906fa295453399015a1fdd7b90a38baa8f78244bd234303737` | SHA-256 checksum for the archive identified by `llvm-mingw-version`. |

## Usage

```yaml
- name: Configure Windows GNU toolchains
  if: runner.os == 'Windows'
  uses: ./.github/actions/setup-windows-gnu
  with:
    llvm-mingw-version: 20250910
    llvm-mingw-sha256: bd88084d7a3b95906fa295453399015a1fdd7b90a38baa8f78244bd234303737
```

The action validates that both `x86_64-w64-mingw32-gcc` and
`aarch64-w64-mingw32-gcc` are present on `PATH`, failing the job if either
compiler is missing.

## Checksums and updates

`llvm-mingw-sha256` defaults to the digest of the bundled release so the
downloaded archive is verified before extraction. When upgrading to a newer
`llvm-mingw-version`, compute its SHA-256 (for example with `Get-FileHash` or
`shasum -a 256`) and update both the version and checksum inputs together to
keep the integrity check accurate.
