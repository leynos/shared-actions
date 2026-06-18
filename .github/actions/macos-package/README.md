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
| `name` | Display name | yes | – |
| `identifier` | Reverse-DNS identifier | yes | – |
| `install-prefix` | Install prefix | no | `/usr/local` |
| `binary` | Binary path | yes | – |
| `manpage` | Man page path | no | empty |
| `license-file` | License file path | no | `LICENSE` |
| `include-license-panel` | Show license in UI | no | `false` |
| `version` | Version override | no | derived |
| `developer-id-installer` | Developer ID identity | no | empty |

## Outputs

| Name | Description |
| ---- | ----------- |
| `version` | Resolved package version |
| `version-build-metadata` | Commit SHA (fallback) |
| `pkg-path` | Installer package path |
| `signed-pkg-path` | Signed package path |

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
