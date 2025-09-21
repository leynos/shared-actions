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

```
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
| `architecture` | no | `x64` | Architecture supplied to `wix build` (`x64` or `x86`). |
| `version` | no | _auto_ | Version embedded in the MSI. Defaults to `GITHUB_REF_NAME` with a leading `v` removed or `0.0.0`. |
| `dotnet-version` | no | `8.0.x` | .NET SDK version installed before running WiX. |
| `wix-tool-version` | no | _latest_ | Specific version of the `wix` .NET global tool to install. |
| `wix-extension` | no | `WixToolset.UI.wixext` | WiX extension coordinate loaded during the build. |
| `wix-extension-version` | no | `4` | Version suffix appended to the extension coordinate (e.g. `WixToolset.UI.wixext/4`). |
| `output-basename` | no | `MyApp` | Base name used when creating the MSI file. |
| `output-directory` | no | `out` | Directory where the MSI artefact is created. |
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

When `version` is omitted the action inspects `GITHUB_REF_NAME`. If the ref
starts with `v` (e.g. `v1.2.3`) the prefix is removed and the remainder is used
as the MSI version. All other cases fall back to `0.0.0`.

To show a licence in the installer UI you **must** supply an RTF document and
reference it from the WiX authoring, for example:

```xml
<WixVariable Id="WixUILicenseRtf" Value="assets\LICENSE.rtf" />
```

## Release history

See [CHANGELOG.md](./CHANGELOG.md).
