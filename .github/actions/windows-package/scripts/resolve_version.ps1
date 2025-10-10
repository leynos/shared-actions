[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Get-MsiVersion([string] $candidate) {
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        return $null
    }

    $trimmed = $candidate.Trim()
    $trimmed = $trimmed.TrimStart('v', 'V')
    try {
        $version = [System.Version]$trimmed
    }
    catch {
        return $null
    }

    if ($version.Major -lt 0 -or $version.Major -gt 255) {
        return $null
    }
    if ($version.Minor -lt 0 -or $version.Minor -gt 255) {
        return $null
    }
    if ($version.Revision -ge 0) {
        return $null
    }

    $build = if ($version.Build -lt 0) { 0 } else { $version.Build }
    if ($build -gt 65535) {
        return $null
    }

    return ([System.Version]::new($version.Major, $version.Minor, $build)).ToString()
}

$versionSource = 'default'
$explicitVersion = $env:INPUT_VERSION
$resolved = $null
if (-not [string]::IsNullOrWhiteSpace($explicitVersion)) {
    $resolved = Get-MsiVersion $explicitVersion
    if ($null -eq $resolved) {
        Write-Error "Invalid MSI version '$explicitVersion'. Provide numeric major.minor.build where major/minor are 0–255 and build is 0–65535."
        exit 1
    }
    $versionSource = 'input'
}

if ($null -eq $resolved) {
    $refType = $env:GITHUB_REF_TYPE
    $refName = $env:GITHUB_REF_NAME
    if ($refType -eq 'tag' -and -not [string]::IsNullOrWhiteSpace($refName)) {
        $resolved = Get-MsiVersion $refName
        if ($null -eq $resolved) {
            Write-Warning "Tag '$refName' does not match v#.#.# semantics required for MSI ProductVersion. Falling back to 0.0.0."
        }
        else {
            $versionSource = "tag '$refName'"
        }
    }
    elseif (-not [string]::IsNullOrWhiteSpace($refName)) {
        Write-Host "Ignoring ref '$refName' of type '$refType' when resolving MSI version."
    }
}

if ($null -eq $resolved) {
    $resolved = '0.0.0'
}

Write-Host "Resolved version ($versionSource): $resolved"
"version=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
