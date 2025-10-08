[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path -Path $PSScriptRoot -ChildPath 'common.ps1')

$script:PythonCommand = $null
$script:PythonArgs = @()

function Ensure-PythonCommand {
    if ($script:PythonCommand) {
        return
    }
    $script:PythonCommand = Get-Command -Name python -ErrorAction SilentlyContinue
    $script:PythonArgs = @()
    if (-not $script:PythonCommand) {
        $script:PythonCommand = Get-Command -Name py -ErrorAction SilentlyContinue
        if ($script:PythonCommand) {
            $script:PythonArgs = @('-3')
        }
    }
    if (-not $script:PythonCommand) {
        Write-Error 'Python 3 is required but neither python nor py -3 was found on PATH.'
        exit 1
    }
}

function Invoke-PythonScript {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]
        $Arguments
    )

    Ensure-PythonCommand
    $allArguments = @()
    if ($script:PythonArgs.Count -gt 0) {
        $allArguments += $script:PythonArgs
    }
    $allArguments += $Arguments
    $output = & $script:PythonCommand.Source @allArguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    return $output
}

if (-not [string]::IsNullOrWhiteSpace($env:LICENSE_TEXT_PATH)) {
    $sourcePath = Resolve-Path -LiteralPath $env:LICENSE_TEXT_PATH -ErrorAction SilentlyContinue
    if (-not $sourcePath) {
        Write-Error "License text file not found: $env:LICENSE_TEXT_PATH"
        exit 1
    }

    $converter = Join-Path -Path $env:GITHUB_ACTION_PATH -ChildPath 'scripts\plaintext_to_rtf.py'
    if (-not (Test-Path -LiteralPath $converter)) {
        Write-Error "Converter script not found: $converter"
        exit 1
    }

    $arguments = @($converter, '--input', $sourcePath.Path)
    if (-not [string]::IsNullOrWhiteSpace($env:LICENSE_RTF_PATH)) {
        $rtfTarget = [System.IO.Path]::GetFullPath($env:LICENSE_RTF_PATH)
        $rtfDirectory = Split-Path -Path $rtfTarget -Parent
        if (-not [string]::IsNullOrWhiteSpace($rtfDirectory) -and -not (Test-Path -LiteralPath $rtfDirectory)) {
            New-Item -ItemType Directory -Path $rtfDirectory -Force | Out-Null
        }
        $arguments += @('--output', $rtfTarget)
    }

    $generatedOutput = Invoke-PythonScript -Arguments $arguments
    $generatedPath = $generatedOutput.Trim()
    if (-not (Test-Path -LiteralPath $generatedPath)) {
        Write-Error "Expected license RTF file was not created: $generatedPath"
        exit 1
    }

    $resolvedRtfPath = (Resolve-Path -LiteralPath $generatedPath).Path
    $env:LICENSE_RTF_PATH = $resolvedRtfPath
    Write-Host "Converted license text to RTF: $resolvedRtfPath"
}
elseif (-not [string]::IsNullOrWhiteSpace($env:LICENSE_RTF_PATH)) {
    $resolvedLicense = Resolve-Path -LiteralPath $env:LICENSE_RTF_PATH -ErrorAction SilentlyContinue
    if (-not $resolvedLicense) {
        Write-Error "License RTF file not found: $env:LICENSE_RTF_PATH"
        exit 1
    }
    $env:LICENSE_RTF_PATH = $resolvedLicense.Path
}

$outDir = $env:OUTPUT_DIRECTORY
if (Test-Path -LiteralPath $outDir) {
    $outDirItem = Get-Item -LiteralPath $outDir
}
else {
    $outDirItem = New-Item -ItemType Directory -Path $outDir
}

$safeBaseName = Get-SafeName -Value $env:OUTPUT_BASENAME -Fallback 'package'
$archInput = if ([string]::IsNullOrWhiteSpace($env:ARCHITECTURE)) { 'x64' } else { $env:ARCHITECTURE }
try {
    $arch = Resolve-Architecture -Value $archInput
}
catch [System.ArgumentException] {
    Write-Error $_.Exception.Message
    exit 1
}

if ([string]::IsNullOrWhiteSpace($env:WXS_PATH)) {
    $applicationSpec = if ([string]::IsNullOrWhiteSpace($env:APPLICATION_SPEC)) { '' } else { $env:APPLICATION_SPEC.Trim() }
    if ([string]::IsNullOrWhiteSpace($applicationSpec)) {
        Write-Error 'application-path input is required when wxs-path is not provided.'
        exit 1
    }

    $generator = Join-Path -Path $env:GITHUB_ACTION_PATH -ChildPath 'scripts\generate_wxs.py'
    if (-not (Test-Path -LiteralPath $generator)) {
        Write-Error "WiX generator script not found: $generator"
        exit 1
    }

    $targetWxs = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ("windows-package-{0}.wxs" -f ([guid]::NewGuid()))
    $arguments = @(
        $generator,
        '--output',
        $targetWxs,
        '--version',
        $env:VERSION,
        '--architecture',
        $arch,
        '--application',
        $applicationSpec
    )

    if (-not [string]::IsNullOrWhiteSpace($env:PRODUCT_NAME)) {
        $arguments += @('--product-name', $env:PRODUCT_NAME)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:MANUFACTURER)) {
        $arguments += @('--manufacturer', $env:MANUFACTURER)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:INSTALL_DIR_NAME)) {
        $arguments += @('--install-dir-name', $env:INSTALL_DIR_NAME)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:DESCRIPTION)) {
        $arguments += @('--description', $env:DESCRIPTION)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:UPGRADE_CODE)) {
        $arguments += @('--upgrade-code', $env:UPGRADE_CODE)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LICENSE_RTF_PATH)) {
        $arguments += @('--license-path', $env:LICENSE_RTF_PATH)
    }

    if (-not [string]::IsNullOrWhiteSpace($env:ADDITIONAL_FILES)) {
        $entries = $env:ADDITIONAL_FILES -split "(`r`n|`n|`r)"
        foreach ($entry in $entries) {
            $trimmed = $entry.Trim()
            if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
                $arguments += @('--additional-file', $trimmed)
            }
        }
    }

    $generationResult = Invoke-PythonScript -Arguments $arguments
    $generatedWxs = $generationResult.Trim()
    if (-not (Test-Path -LiteralPath $generatedWxs)) {
        Write-Error "Expected WiX authoring was not created: $generatedWxs"
        exit 1
    }
    $env:WXS_PATH = (Resolve-Path -LiteralPath $generatedWxs).Path
    Write-Host "Generated default WiX authoring: $($env:WXS_PATH)"
}
elseif (-not (Test-Path -LiteralPath $env:WXS_PATH)) {
    Write-Error "WiX source file not found: $env:WXS_PATH"
    exit 1
}

$outputPath = Join-Path -Path $outDirItem.FullName -ChildPath ("{0}-{1}-{2}.msi" -f $safeBaseName, $env:VERSION, $arch)
$arguments = @('build', $env:WXS_PATH)
if (-not [string]::IsNullOrWhiteSpace($env:WIX_EXTENSION)) {
    $extensions = $env:WIX_EXTENSION -split '[,\s]+'
    foreach ($extension in $extensions) {
        $trimmed = $extension.Trim()
        if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
            $arguments += @('-ext', $trimmed)
        }
    }
}

$arguments += @('-arch', $arch, '-d', "Version=$($env:VERSION)", '-o', $outputPath)
wix @arguments

if (-not (Test-Path -LiteralPath $outputPath)) {
    Write-Error "MSI build failed: output file '$outputPath' was not created."
    exit 1
}

$resolved = (Resolve-Path -LiteralPath $outputPath).Path
"msi-path=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
