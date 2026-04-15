[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Set-ToolsPath {
    [CmdletBinding()]
    param()

    $toolsDir = Join-Path $env:USERPROFILE '.dotnet\tools'
    if (-not ($env:PATH -split ';' | Where-Object { $_ -eq $toolsDir })) {
        $env:PATH = "$toolsDir;$env:PATH"
    }
}

function Install-WixTool {
    [CmdletBinding()]
    param(
        [string]
        $ToolVersion
    )

    $installArgs = @('--global', 'wix')
    if (-not [string]::IsNullOrWhiteSpace($ToolVersion)) {
        $installArgs += @('--version', $ToolVersion)
    }

    dotnet tool update @installArgs 2>&1 | Tee-Object -Variable updateOutput | Out-Null
    $updateExitCode = $LASTEXITCODE
    if ($updateExitCode -eq 0) { return }

    $updateSuffix = if ($updateOutput) { ":`n$updateOutput" } else { '.' }
    Write-Warning "dotnet tool update failed with exit code $($updateExitCode)$updateSuffix"

    dotnet tool install @installArgs 2>&1 | Tee-Object -Variable installOutput | Out-Null
    if ($LASTEXITCODE -eq 0) { return }

    $installSuffix = if ($installOutput) { ":`n$installOutput" } else { '.' }
    Write-Error "Failed to install WiX CLI via dotnet tool (exit code $($LASTEXITCODE))$installSuffix"
    exit $LASTEXITCODE
}

function Get-InstalledWixVersion {
    [CmdletBinding()]
    param()

    $wixVersionOutput = (& wix --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to determine installed WiX CLI version:`n$wixVersionOutput"
        exit $LASTEXITCODE
    }

    return $wixVersionOutput
}

function Confirm-WixEula {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int]
        $MajorVersion
    )

    if ($MajorVersion -ge 7) {
        $eulaAcceptOutput = (& wix eula accept wix7 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to persist WiX EULA acceptance:`n$eulaAcceptOutput"
            exit $LASTEXITCODE
        }
    }
}

function Resolve-ExtensionVersion {
    [CmdletBinding()]
    param(
        [string]
        $ExtensionVersion,

        [Parameter(Mandatory = $true)]
        [int]
        $WixMajorVersion,

        [Parameter(Mandatory = $true)]
        [string]
        $WixVersionOutput
    )

    if ([string]::IsNullOrWhiteSpace($ExtensionVersion)) {
        return $WixMajorVersion.ToString()
    }

    $extensionMajorVersion = Get-VersionMajor -VersionText $ExtensionVersion -Description 'WiX extension'
    if ($extensionMajorVersion -ne $WixMajorVersion) {
        Write-Error "WiX extension version '$ExtensionVersion' is incompatible with installed WiX CLI version '$WixVersionOutput'. Use a $WixMajorVersion.x extension or omit WIX_EXTENSION_VERSION to auto-match the installed WiX major."
        exit 1
    }

    return $ExtensionVersion
}

function Install-WixExtension {
    [CmdletBinding()]
    param(
        [string]
        $Extension,

        [string]
        $ExtensionVersion,

        [Parameter(Mandatory = $true)]
        [int]
        $WixMajorVersion,

        [Parameter(Mandatory = $true)]
        [string]
        $WixVersionOutput
    )

    if ([string]::IsNullOrWhiteSpace($Extension)) {
        return
    }

    $resolvedExtensionVersion = Resolve-ExtensionVersion `
        -ExtensionVersion $ExtensionVersion `
        -WixMajorVersion $WixMajorVersion `
        -WixVersionOutput $WixVersionOutput

    $extensionCoordinate = $Extension
    if (-not [string]::IsNullOrWhiteSpace($resolvedExtensionVersion)) {
        $extensionCoordinate = "$extensionCoordinate/$resolvedExtensionVersion"
    }

    $arguments = @('extension', 'add')
    if ($WixMajorVersion -ge 7) {
        $arguments += @('-acceptEula', 'wix7')
    }
    $arguments += @('-g', $extensionCoordinate)

    & wix @arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function Invoke-Main {
    [CmdletBinding()]
    param()

    . (Join-Path -Path $PSScriptRoot -ChildPath 'common.ps1')

    Set-ToolsPath
    Install-WixTool -ToolVersion $env:WIX_TOOL_VERSION

    $wixVersionOutput = Get-InstalledWixVersion
    $wixMajorVersion = Get-VersionMajor -VersionText $wixVersionOutput -Description 'WiX CLI'
    Assert-SupportedWixMajorVersion -MajorVersion $wixMajorVersion -VersionText $wixVersionOutput

    Confirm-WixEula -MajorVersion $wixMajorVersion
    Install-WixExtension `
        -Extension $env:WIX_EXTENSION `
        -ExtensionVersion $env:WIX_EXTENSION_VERSION `
        -WixMajorVersion $wixMajorVersion `
        -WixVersionOutput $wixVersionOutput
}

Invoke-Main
