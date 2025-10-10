[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# We centralise all MSI ProductVersion validation so every call path returns
# either a normalised three-part version or `$null`; consumers simply branch on
# success and do not need to remember individual guard-rails.
function Get-MsiVersion([string] $candidate) {
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        return $null
    }

    # MSI ProductVersion disallows surrounding whitespace, so drop it up front
    # rather than hoping downstream tooling copes with it.
    $trimmed = $candidate.Trim()
    $numeric = $trimmed.TrimStart('v', 'V')

    if ([string]::IsNullOrWhiteSpace($numeric)) {
        return $null
    }

    $rawParts = $numeric.Split('.')
    # Reject four-part versions early; WiX only accepts major.minor.build and
    # `[System.Version]` would silently treat the fourth segment as a revision.
    if ($rawParts.Count -gt 3) {
        return $null
    }

    $parts = @()
    foreach ($rawPart in $rawParts) {
        $part = $rawPart.Trim()
        if ($part.Length -eq 0) {
            return $null
        }
        $parts += $part
    }

    # Default any missing components to zero so tags like `v1` still produce a
    # valid MSI version while keeping the major/minor values explicit.
    while ($parts.Count -lt 3) {
        $parts += '0'
    }

    $normalized = $parts -join '.'

    try {
        # `[System.Version]` performs the heavy lifting: it enforces numeric
        # input and guards against negative values, which keeps the manual
        # checks below focused on MSI-specific ranges.
        $version = [System.Version]$normalized
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
    # MSI cannot represent a revision segment; any non-negative value here
    # indicates the original string had too many components.
    if ($version.Revision -ge 0) {
        return $null
    }

    # `[System.Version]` sets Build to -1 when omitted; MSI needs an explicit
    # zero in that case, yet values above 65535 overflow ProductVersion.
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
        # Abort early instead of silently falling back—callers supplied an
        # explicit value and deserve an actionable error.
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
            # Keep legacy release flows alive by emitting a warning rather than
            # hard-failing when a tag does not parse; we still fall back to
            # 0.0.0 so downstream steps behave deterministically.
            Write-Warning "Tag '$refName' does not match v#.#.# semantics required for MSI ProductVersion. Falling back to 0.0.0."
        }
        else {
            $versionSource = "tag '$refName'"
        }
    }
    elseif (-not [string]::IsNullOrWhiteSpace($refName)) {
        # Non-tag refs (branches, PRs) rarely encode usable version numbers, so
        # log the decision to ignore them for easier debugging.
        Write-Host "Ignoring ref '$refName' of type '$refType' when resolving MSI version."
    }
}

if ($null -eq $resolved) {
    # A zeroed version keeps the MSI packaging step moving without silently
    # producing an inconsistent ProductVersion.
    $resolved = '0.0.0'
}

Write-Host "Resolved version ($versionSource): $resolved"
"version=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
