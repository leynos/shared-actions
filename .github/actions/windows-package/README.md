# Windows package action

Build a WiX MSI from a prebuilt Windows application, license text (RTF) and
supporting documentation. The action codifies the workflow documented in the
repository guidance so that any job can produce a signed installer artefact with
one composite call.

## Features

- Installs the WiX CLI and UI extension on the runner.
- Resolves the MSI version from an explicit input or a tagged Git reference.
- Builds a single-file MSI (`EmbedCab="yes"`) from supplied WiX authoring or a
  generated template that installs the provided application and supporting
  files.
- Optionally uploads the generated MSI via `actions/upload-artifact`.

> [!IMPORTANT]
> This action **must** run on a Windows runner. The first step fails fast when
> `RUNNER_OS` is not `Windows`.

## Repository layout

The action expects the following project structure (paths may be overridden via
inputs):

```text
.
├─ installer/
│  └─ Package.wxs              # WiX authoring
└─ assets/
   ├─ MyApp.exe                # application binary
   ├─ LICENSE.rtf              # license text shown in the installer UI
   └─ README.pdf               # documentation shipped with the installer
```

The `installer/Package.wxs` authoring is optional—omit it when using the
default template and provide the executable (and optional additional files) via
the `application-path` and `additional-files` inputs.

## Inputs

| Name | Required | Default | Description |
| ---- | -------- | ------- | ----------- |
| `wxs-path` | no | `''` | WiX authoring file path |
| `application-path` | no | `''` | Main executable path |
| `additional-files` | no | `''` | Extra files to include |
| `product-name` | no | `''` | Product name |
| `manufacturer` | no | `''` | Manufacturer name |
| `install-dir-name` | no | `''` | Install directory (defaults to sanitised product name) |
| `description` | no | `''` | Installer description for MSI summary |
| `upgrade-code` | no | `''` | UpgradeCode GUID (auto-generated if omitted) |
| `architecture` | no | `x64` | Target architecture (`x86`, `x64`, or `arm64`) |
| `version` | no | _auto_ | MSI version (defaults to tag-derived or `0.0.0`) |
| `dotnet-version` | no | `8.0.x` | .NET SDK version to install before WiX |
| `wix-tool-version` | no | _latest_ | Specific `wix` tool version to install |
| `wix-extension` | no | `WixToolset.UI.wixext` | WiX extension to load |
| `wix-extension-version` | no | `''` | Extension version suffix (auto-matches WiX major if omitted) |
| `output-basename` | no | `MyApp` | Base name for generated MSI file |
| `output-directory` | no | `out` | Directory for MSI output |
| `license-plaintext-path` | no | `''` | UTF-8 license to convert to RTF |
| `license-rtf-path` | no | `''` | Output path for RTF license |
| `upload-artefact` | no | `true` | Upload MSI via `actions/upload-artifact` |
| `artefact-name` | no | `msi` | Name of uploaded artefact |

## Outputs

| Name | Description |
| ---- | ----------- |
| `version` | Version value resolved for the build (after tag stripping). |
| `msi-path` | Absolute path of the generated MSI file. |

## Usage

```yaml
jobs:
  package:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build WiX MSI
        uses: ./.github/actions/windows-package
        with:
          application-path: dist/win/MyApp.exe
          additional-files: |
            docs/manual.pdf|docs/manual.pdf
          product-name: MyApp
          manufacturer: Example Org
          output-basename: MyApp
          architecture: x64
          # version: "1.2.3"               # optional override
          # upload-artefact: 'false'       # disable artefact upload if not needed
```

In WiX authoring, reference the preprocessor variable supplied via
`-dVersion=...` using the `$(var.Name)` form expected by the WiX
preprocessor:

```xml
<Package Version="$(var.Version)">
```

The `$(var.Version)` preprocessor expression resolves to the version
string the action passes via `-dVersion=...`, ensuring the MSI `Package`
uses the same value that appears in the generated filename.

When `version` is omitted the action inspects `GITHUB_REF_TYPE` and
`GITHUB_REF_NAME`. Only tag refs that resemble `v<major>.<minor>.<build>`
(the minor and build segments are optional) are used to derive the MSI
version. All other refs—including branches and tags with non-numeric
suffixes—fall back to `0.0.0`.

MSI ProductVersion components must be integers where the major and minor
segments are `0–255` and the build segment is `0–65535`. Values outside
those ranges cause the action to fail fast so that WiX receives a valid
version.

To display a license in the installer UI, either provide an RTF file
directly or add a UTF-8 plain text document and set
`license-plaintext-path` so the action converts it to RTF prior to invoking
WiX. UTF-8 input with or without a byte-order mark is accepted—the
converter strips any BOM and renders the text using Calibri 11 pt by
default. When no explicit `license-rtf-path` is set the generated file
replaces the source suffix with `.rtf`, making it easy to refer to a stable
path from WiX authoring. For example:

```xml
<WixVariable Id="WixUILicenseRtf" Value="assets\LICENSE.rtf" />
```

## Release history

See [CHANGELOG.md](./CHANGELOG.md).
