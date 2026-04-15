[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Get-VersionMajor {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]
        $VersionText,

        [Parameter(Mandatory = $true)]
        [string]
        $Description
    )

    $match = [regex]::Match($VersionText, '(\d+)(?:\.\d+){0,2}')
    if (-not $match.Success) {
        Write-Error "Unable to determine $Description major version from '$VersionText'."
        exit 1
    }

    return [int]$match.Groups[1].Value
}

$toolsDir = Join-Path $env:USERPROFILE '.dotnet\tools'
if (-not ($env:PATH -split ';' | Where-Object { $_ -eq $toolsDir })) {
    $env:PATH = "$toolsDir;$env:PATH"
}

$toolVersion = $env:WIX_TOOL_VERSION
$installArgs = @('--global', 'wix')
if (-not [string]::IsNullOrWhiteSpace($toolVersion)) {
    $installArgs += @('--version', $toolVersion)
}

dotnet tool update @installArgs 2>&1 | Tee-Object -Variable updateOutput | Out-Null
$updateExitCode = $LASTEXITCODE
if ($updateExitCode -ne 0) {
    if ($updateOutput) {
        Write-Warning "dotnet tool update failed with exit code $($updateExitCode):`n$($updateOutput)"
    }
    else {
        Write-Warning "dotnet tool update failed with exit code $($updateExitCode)."
    }

    dotnet tool install @installArgs 2>&1 | Tee-Object -Variable installOutput | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if ($installOutput) {
            Write-Error "Failed to install WiX CLI via dotnet tool (exit code $($LASTEXITCODE)):`n$($installOutput)"
        }
        else {
            Write-Error "Failed to install WiX CLI via dotnet tool (exit code $($LASTEXITCODE))."
        }
        exit $LASTEXITCODE
    }
}

$wixVersionOutput = (& wix --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to determine installed WiX CLI version:`n$wixVersionOutput"
    exit $LASTEXITCODE
}

$wixMajorVersion = Get-VersionMajor -VersionText $wixVersionOutput -Description 'WiX CLI'

if ($wixMajorVersion -ge 7) {
    $eulaAcceptOutput = (& wix eula accept wix7 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to persist WiX EULA acceptance:`n$eulaAcceptOutput"
        exit $LASTEXITCODE
    }
}

if (-not [string]::IsNullOrWhiteSpace($env:WIX_EXTENSION)) {
    $extensionVersion = $env:WIX_EXTENSION_VERSION
    if ([string]::IsNullOrWhiteSpace($extensionVersion)) {
        $extensionVersion = $wixMajorVersion.ToString()
    }
    else {
        $extensionMajorVersion = Get-VersionMajor -VersionText $extensionVersion -Description 'WiX extension'
        if ($extensionMajorVersion -ne $wixMajorVersion) {
            Write-Error "WiX extension version '$extensionVersion' is incompatible with installed WiX CLI version '$wixVersionOutput'. Use a $wixMajorVersion.x extension or omit WIX_EXTENSION_VERSION to auto-match the installed WiX major."
            exit 1
        }
    }

    $extensionCoordinate = $env:WIX_EXTENSION
    if (-not [string]::IsNullOrWhiteSpace($extensionVersion)) {
        $extensionCoordinate = "$extensionCoordinate/$extensionVersion"
    }
    wix extension add -acceptEula wix7 -g $extensionCoordinate
}
