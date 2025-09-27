# Windows package action

Build a WiX v4 MSI from a prebuilt Windows application, licence text (RTF) and
supporting documentation. The action codifies the workflow documented in the
repository guidance so that any job can produce a signed installer artefact with
one composite call.

## Features

- Installs the WiX v4 CLI and UI extension on the runner.
- Resolves the MSI version from an explicit input or a tagged Git reference.
- Builds a single-file MSI (`EmbedCab="yes"`) for the supplied WiX authoring.
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
│  └─ Package.wxs              # WiX v4 authoring
└─ assets/
   ├─ MyApp.exe                # application binary
   ├─ LICENSE.rtf              # licence text shown in the installer UI
   └─ README.pdf               # documentation shipped with the installer
```

## Inputs

| Name | Required | Default | Description |
| ---- | -------- | ------- | ----------- |
| `wxs-path` | no | `installer/Package.wxs` | Path to the WiX authoring file used to build the MSI. |
| `architecture` | no | `x64` | Architecture supplied to `wix build` (`x86`, `x64`, or `arm64`). |
| `version` | no | _auto_ | Version embedded in the MSI. Defaults to a numeric tag-derived value or `0.0.0`. |
| `dotnet-version` | no | `8.0.x` | .NET SDK version installed before running WiX. |
| `wix-tool-version` | no | _latest_ | Specific version of the `wix` .NET global tool to install. |
| `wix-extension` | no | `WixToolset.UI.wixext` | WiX extension coordinate loaded during the build. |
| `wix-extension-version` | no | `4` | Version suffix appended to the extension coordinate (e.g. `WixToolset.UI.wixext/4`). |
| `output-basename` | no | `MyApp` | Base name used when creating the MSI file. |
| `output-directory` | no | `out` | Directory where the MSI artefact is created. |
| `license-plaintext-path` | no | _unset_ | Optional path to a UTF-8 (with or without BOM) plain text licence that will be converted to RTF using the default Calibri 11 pt template. |
| `license-rtf-path` | no | _unset_ | Output path for the generated licence RTF when converting from plain text. Defaults to replacing the input suffix with `.rtf`. |
| `upload-artifact` | no | `true` | When `true`, publishes the MSI using `actions/upload-artifact`. |
| `artifact-name` | no | `msi` | Name of the uploaded artifact. |

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
          output-basename: MyApp
          architecture: x64
          # version: "1.2.3"               # optional override
          # upload-artifact: 'false'       # disable artifact upload if not needed
```

In WiX authoring, reference the preprocessor variable supplied via
`-dVersion=...` using the `$(var.Name)` form expected by the WiX
preprocessor:

```xml
<Package Version="$(var.Version)">
```

The `$(var.Version)` preprocessor expression resolves to the version string the
action passes via `-dVersion=...`, ensuring the MSI `Package` uses the same
value that appears in the generated filename.

When `version` is omitted the action inspects `GITHUB_REF_TYPE` and
`GITHUB_REF_NAME`. Only tag refs that resemble `v<major>.<minor>.<build>` (the
minor and build segments are optional) are used to derive the MSI version. All
other refs—including branches and tags with non-numeric suffixes—fall back to
`0.0.0`.

MSI ProductVersion components must be integers where the major and minor
segments are `0–255` and the build segment is `0–65535`. Values outside those
ranges cause the action to fail fast so that WiX receives a valid version.

To display a licence in the installer UI, either provide an RTF file directly
or add a UTF-8 plain text document and set `license-plaintext-path` so the
action converts it to RTF prior to invoking WiX. UTF-8 input with or without a
byte-order mark is accepted—the converter strips any BOM and renders the text
using Calibri 11 pt by default. When no explicit `license-rtf-path` is set the
generated file replaces the source suffix with `.rtf`, making it easy to refer
to a stable path from WiX authoring. For example:

```xml
<WixVariable Id="WixUILicenseRtf" Value="assets\LICENSE.rtf" />
```

## Release history

See [CHANGELOG.md](./CHANGELOG.md).
