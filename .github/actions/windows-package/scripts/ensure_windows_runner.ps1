[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

if ($env:RUNNER_OS -ne 'Windows') {
    Write-Error "windows-package action must run on a Windows runner. Current OS: $($env:RUNNER_OS)"
    exit 1
}
