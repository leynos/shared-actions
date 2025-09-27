[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$toolsDir = Join-Path $env:USERPROFILE '.dotnet\tools'
if (-not ($env:PATH -split ';' | Where-Object { $_ -eq $toolsDir })) {
    $env:PATH = "$toolsDir;$env:PATH"
}

$toolVersion = $env:WIX_TOOL_VERSION
$installArgs = @('--global', 'wix')
if (-not [string]::IsNullOrWhiteSpace($toolVersion)) {
    $installArgs += @('--version', $toolVersion)
}

dotnet tool update @installArgs
if ($LASTEXITCODE -ne 0) {
    dotnet tool install @installArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install WiX CLI via dotnet tool (exit code $LASTEXITCODE)."
        exit $LASTEXITCODE
    }
}

if (-not [string]::IsNullOrWhiteSpace($env:WIX_EXTENSION)) {
    $extensionCoordinate = $env:WIX_EXTENSION
    if (-not [string]::IsNullOrWhiteSpace($env:WIX_EXTENSION_VERSION)) {
        $extensionCoordinate = "$extensionCoordinate/$($env:WIX_EXTENSION_VERSION)"
    }
    wix extension add -g $extensionCoordinate
}
