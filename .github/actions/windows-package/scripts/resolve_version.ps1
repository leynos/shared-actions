[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$InformationPreference = 'Continue'

<#
.SYNOPSIS
Resolve a Windows Installer ProductVersion with consistent validation and logging.

.DESCRIPTION
The script accepts an explicit version input, falls back to tags when
available, and ultimately emits a deterministic three-part ProductVersion.

Get-MsiVersion is the focal point: it normalises raw candidates, rejects
invalid combinations, and pads missing components so that downstream callers do
not replicate defensive logic. Helper functions keep each normalisation stage
focused on a single responsibility so future edits remain readable without
heavy inline commentary.
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

function Normalize-VersionParts {
    param([string] $candidate)

    if ([string]::IsNullOrWhiteSpace($candidate)) {
        return $null
    }

    $trimmed = $candidate.Trim()
    $numeric = $trimmed.TrimStart('v', 'V')
    if ([string]::IsNullOrWhiteSpace($numeric)) {
        return $null
    }

    $split = $numeric.Split('.')
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

function Get-MsiVersion {
    param([string] $candidate)

    $parts = Normalize-VersionParts $candidate
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

    return $validated.ToString()
}

function Invoke-ResolveVersion {
    $versionSource = 'default'
    $explicitVersion = $env:INPUT_VERSION
    $resolved = $null
    if (-not [string]::IsNullOrWhiteSpace($explicitVersion)) {
        $resolved = Get-MsiVersion $explicitVersion
        if ($null -eq $resolved) {
            Write-Log -Level 'Error' -Message "Invalid MSI version '$explicitVersion'. Provide numeric major.minor.build where major/minor are 0–255 and build is 0–65535."
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
                Write-Log -Level 'Warning' -Message "Tag '$refName' does not match v#.#.# semantics required for MSI ProductVersion. Falling back to 0.0.0."
            }
            else {
                $versionSource = "tag '$refName'"
            }
        }
        elseif (-not [string]::IsNullOrWhiteSpace($refName)) {
            Write-Log -Level 'Info' -Message "Ignoring ref '$refName' of type '$refType' when resolving MSI version."
        }
    }

    if ($null -eq $resolved) {
        $resolved = '0.0.0'
    }

    Write-Log -Level 'Info' -Message "Resolved version ($versionSource): $resolved"
    "version=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-ResolveVersion
}
