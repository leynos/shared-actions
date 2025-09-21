# Build macOS package

Build a distributable macOS installer package (`.pkg`) from a compiled CLI binary
along with optional man pages and license documentation. The action mirrors the
manual `pkgbuild`/`productbuild` flow while keeping the logic in repeatable
Python scripts.

The action must run on a **macOS** runner. It validates the platform before
executing any packaging commands.

## Inputs

| Name | Description | Required | Default |
| ---- | ----------- | -------- | ------- |
| `name` | Display name of the packaged CLI (also used for output filenames). | yes | – |
| `identifier` | Reverse-DNS package identifier (for example `com.example.tool`). | yes | – |
| `install-prefix` | Installation prefix within the target filesystem. | no | `/usr/local` |
| `binary` | Path to the compiled binary to install. | yes | – |
| `manpage` | Optional path to a man page (`.1`, `.1.gz`, etc.) to embed. | no | empty |
| `license-file` | Path to the license text copied into the package and optional UI. | no | `LICENSE` |
| `include-license-panel` | Show the license text inside the installer UI (requires `license-file`). | no | `false` |
| `version` | Override the package version. Defaults to the Git tag (`v*`) or falls back to the commit SHA. | no | derived |
| `developer-id-installer` | Developer ID Installer identity used with `productsign` for notarized packages. | no | empty |

## Outputs

| Name | Description |
| ---- | ----------- |
| `version` | Resolved version used for the package metadata. |
| `version-build-metadata` | Short commit SHA recorded when falling back to a default version. |
| `pkg-path` | Path to the generated installer archive (`dist/<name>-<version>.pkg`). |
| `signed-pkg-path` | Path to the signed installer archive when signing succeeds; empty otherwise. |

## Usage

```yaml
jobs:
  package:
    runs-on: macos-13
    steps:
      - uses: actions/checkout@v4

      - name: Build binary
        run: make build

      - name: Package CLI for macOS
        id: package
        uses: ./.github/actions/macos-package@v1
        with:
          name: mytool
          identifier: com.example.mytool
          binary: dist/mytool
          manpage: docs/mytool.1
          license-file: LICENSE
          include-license-panel: true

      - name: Upload installer
        uses: actions/upload-artifact@v4
        with:
          name: macos-pkg-${{ steps.package.outputs.version }}
          path: dist/*.pkg
```

## Behaviour

- Versions are automatically inferred from the triggering Git ref. Tags of the
  form `v1.2.3` resolve to `1.2.3`; other refs default to `0.0.0` while the
  short commit SHA is exposed separately via the
  `version-build-metadata` output.
- Payload contents are staged under `pkgroot/`, mimicking `/` on the target
  machine. The binary is installed under `<install-prefix>/bin/<name>`, an
  optional man page is compressed to
  `<install-prefix>/share/man/man<section>/<name>.<section>.gz`, and the license
  is copied to `<install-prefix>/share/doc/<name>/LICENSE` when present.
- When `include-license-panel` is `true`, the action renders a Distribution XML
  and copies the license text into `Resources/` so the installer shows a license
  acceptance step.
- Providing `developer-id-installer` triggers `productsign`, producing
  `<name>-<version>-signed.pkg` alongside the unsigned archive.
- Set the optional `TAG_VERSION_PREFIX` environment variable to adjust the tag
  prefix stripped from `refs/tags/<tag>` when resolving versions. The default
  assumes tags formatted as `v1.2.3`.

Release history for the action is tracked in [CHANGELOG](CHANGELOG.md).
