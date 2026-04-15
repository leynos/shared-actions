function Get-SafeName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string] $Value,
        [Parameter(Mandatory = $true)][string] $Fallback
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $Fallback
    }

    $sanitised = $Value -replace '[^A-Za-z0-9._-]', '-'
    $sanitised = $sanitised.Trim('-_.')
    if ([string]::IsNullOrWhiteSpace($sanitised)) {
        return $Fallback
    }

    return $sanitised
}

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

function Assert-SupportedWixMajorVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int]
        $MajorVersion,

        [Parameter(Mandatory = $true)]
        [string]
        $VersionText
    )

    if ($MajorVersion -lt 7) {
        Write-Error "WiX CLI version '$VersionText' is not supported. WiX v7 or newer is required."
        exit 1
    }
}


function Resolve-Architecture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string] $Value
    )

    $token = $Value.Trim()
    switch -Regex ($token.ToLowerInvariant()) {
        '^(x64|amd64)$' { return 'x64' }
        '^(x86|ia32|win32)$' { return 'x86' }
        '^(arm64|aarch64)$' { return 'arm64' }
        default {
            $message = "Unsupported architecture '$Value'. Use x86, x64, or arm64."
            throw (New-Object System.ArgumentException($message))
        }
    }
}
