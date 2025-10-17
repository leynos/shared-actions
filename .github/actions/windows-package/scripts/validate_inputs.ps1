[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$wxsPath = if ([string]::IsNullOrWhiteSpace($env:WXS_PATH)) { '' } else { $env:WXS_PATH.Trim() }
$applicationSpec = if ([string]::IsNullOrWhiteSpace($env:APPLICATION_SPEC)) { '' } else { $env:APPLICATION_SPEC.Trim() }

if ([string]::IsNullOrWhiteSpace($wxsPath) -and [string]::IsNullOrWhiteSpace($applicationSpec)) {
    $message = @"
windows-package input validation failed: provide 'application-path' when 'wxs-path' is omitted so the action can generate WiX authoring for you.
Set 'application-path' to your packaged executable (for example 'dist\MyApp.exe') or supply 'wxs-path' if you already maintain custom WiX authoring.
"@
    Write-Error $message
    exit 1
}
