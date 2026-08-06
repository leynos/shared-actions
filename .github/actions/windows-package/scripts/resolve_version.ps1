[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$InformationPreference = 'Continue'
Set-StrictMode -Version Latest

<#
.SYNOPSIS
Resolve a Windows Installer ProductVersion with consistent validation and logging.

.DESCRIPTION
The script accepts an explicit version input, falls back to tags when
available, and ultimately emits a deterministic three-part ProductVersion.

Get-MsiVersionInfo is the focal point: a pure query that normalizes raw
candidates, splits and validates semver metadata, rejects invalid
combinations, and pads missing components so that downstream callers do not
replicate defensive logic. Get-MsiVersion is its thin string-or-null wrapper.
Warnings about discarded metadata are emitted only at the command boundary
(Invoke-ResolveVersion and Resolve-TagVersion), keeping the query functions
side-effect free.
#>

function Write-Log {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidateSet('Info', 'Warning', 'Error')][string] $Level,
        [Parameter(Mandatory = $true)][string] $Message
    )

    switch ($Level) {
        'Info' { Write-Information -MessageData $Message }
        'Warning' { Write-Warning $Message }
        'Error' { Write-Error $Message }
    }
}

function Remove-SemverMetadata {
    param([string] $numeric)

    # MSI ProductVersion is strictly numeric major.minor.build, so semver
    # pre-release ('-beta1') and build metadata ('+abc123') cannot be carried
    # through. Split the release part from the suffix, validating the suffix
    # against the SemVer grammar (dot-separated, non-empty [0-9A-Za-z-]
    # identifiers): a malformed suffix such as '1.2.3-' or '1.2.3-beta..exp'
    # rejects the whole candidate instead of being silently discarded.
    #
    # Pure: no logging. Returns @{ Release; Metadata } or $null when the
    # suffix is malformed. Callers at the command boundary decide whether the
    # discarded metadata warrants a warning.
    $index = $numeric.IndexOfAny([char[]]@('-', '+'))
    if ($index -lt 0) {
        return @{ Release = $numeric; Metadata = $null }
    }

    $metadata = $numeric.Substring($index)
    # A pre-release identifier is either numeric without leading zeroes
    # ('0' or '[1-9]\d*') or alphanumeric containing at least one non-digit;
    # '1.2.3-01' and '1.2.3-alpha.01' are therefore rejected. Build metadata
    # identifiers permit leading zeroes, per the SemVer grammar.
    $prereleaseId = '(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)'
    $prereleasePattern = "^-$prereleaseId(\.$prereleaseId)*(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$"
    $buildPattern = '^\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*$'
    if ($metadata -notmatch $prereleasePattern -and $metadata -notmatch $buildPattern) {
        return $null
    }

    return @{ Release = $numeric.Substring(0, $index); Metadata = $metadata }
}

function Normalize-VersionParts {
    param([string] $release)

    if ([string]::IsNullOrWhiteSpace($release)) {
        return $null
    }

    $split = $release.Split('.')
    if ($split.Count -gt 3) {
        return $null
    }

    $parts = @()
    foreach ($rawPart in $split) {
        $part = $rawPart.Trim()
        if ($part.Length -eq 0) {
            return $null
        }
        $parts += $part
    }

    while ($parts.Count -lt 3) {
        $parts += '0'
    }

    return $parts
}

function ConvertTo-Version {
    param([string[]] $parts)

    try {
        return [System.Version]($parts -join '.')
    }
    catch {
        return $null
    }
}

function Validate-MsiVersion {
    param([System.Version] $version)

    # MSI ProductVersion constrains Major to 0–255.
    if ($version.Major -gt 255) {
        return $null
    }
    # MSI ProductVersion constrains Minor to 0–255.
    if ($version.Minor -gt 255) {
        return $null
    }

    # MSI ProductVersion caps Build at 65535.
    if ($version.Build -gt 65535) {
        return $null
    }

    return [System.Version]::new($version.Major, $version.Minor, $version.Build)
}

function Get-MsiVersionInfo {
    param([string] $candidate)

    # Pure query: runs the full trim -> metadata split -> normalize ->
    # convert -> validate pipeline. Returns
    # @{ Version; DiscardedMetadata; Numeric } or $null; emits no output, so
    # callers own any user-facing warning about discarded metadata.
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        return $null
    }

    $numeric = $candidate.Trim().TrimStart('v', 'V')
    if ([string]::IsNullOrWhiteSpace($numeric)) {
        return $null
    }

    $split = Remove-SemverMetadata $numeric
    if ($null -eq $split) {
        return $null
    }

    $parts = Normalize-VersionParts $split.Release
    if ($null -eq $parts) {
        return $null
    }

    $version = ConvertTo-Version $parts
    if ($null -eq $version) {
        return $null
    }

    $validated = Validate-MsiVersion $version
    if ($null -eq $validated) {
        return $null
    }

    return @{
        Version = $validated.ToString()
        DiscardedMetadata = $split.Metadata
        Numeric = $numeric
    }
}

function Get-MsiVersion {
    param([string] $candidate)

    $info = Get-MsiVersionInfo $candidate
    if ($null -eq $info) {
        return $null
    }

    return $info.Version
}

function Write-DiscardedMetadataWarning {
    param([hashtable] $info)

    if ($null -ne $info -and $null -ne $info.DiscardedMetadata) {
        Write-Log -Level 'Warning' -Message "Discarding semver metadata '$($info.DiscardedMetadata)' from '$($info.Numeric)'; MSI ProductVersion only carries numeric major.minor.build."
    }
}

function Resolve-TagVersion {
    param(
        [string] $refType,
        [string] $refName
    )

    if ($refType -ne 'tag') {
        if (-not [string]::IsNullOrWhiteSpace($refName)) {
            Write-Log -Level 'Info' -Message "Ignoring ref '$refName' of type '$refType' when resolving MSI version."
        }
        return $null
    }

    if ([string]::IsNullOrWhiteSpace($refName)) {
        return $null
    }

    $info = Get-MsiVersionInfo $refName
    if ($null -eq $info) {
        Write-Log -Level 'Warning' -Message "Tag '$refName' does not match v#.#.# semantics required for MSI ProductVersion. Falling back to 0.0.0."
        return $null
    }
    Write-DiscardedMetadataWarning $info

    return @{
        Version = $info.Version
        Source = "tag '$refName'"
        SourceKey = 'tag'
    }
}

function Invoke-ResolveVersion {
    $explicitVersion = $env:INPUT_VERSION
    if (-not [string]::IsNullOrWhiteSpace($explicitVersion)) {
        $info = Get-MsiVersionInfo $explicitVersion
        if ($null -eq $info) {
            Write-Log -Level 'Error' -Message "Invalid MSI version '$explicitVersion'. Provide numeric major.minor.build where major/minor are 0–255 and build is 0–65535."
            exit 1
        }
        Write-DiscardedMetadataWarning $info

        $resolved = $info.Version
        Write-Log -Level 'Info' -Message "Resolved version (input): $resolved"
        "version=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
        "versionSource=input" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
        return
    }

    $tagResult = Resolve-TagVersion -refType $env:GITHUB_REF_TYPE -refName $env:GITHUB_REF_NAME
    if ($null -ne $tagResult) {
        $resolved = $tagResult.Version
        Write-Log -Level 'Info' -Message "Resolved version ($($tagResult.Source)): $resolved"
        "version=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
        "versionSource=$($tagResult.SourceKey)" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
        return
    }

    $resolved = '0.0.0'
    Write-Log -Level 'Info' -Message "Resolved version (default): $resolved"
    "version=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
    "versionSource=default" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-ResolveVersion
}
