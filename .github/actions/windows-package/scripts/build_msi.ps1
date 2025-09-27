[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $env:WXS_PATH)) {
    Write-Error "WiX source file not found: $env:WXS_PATH"
    exit 1
}

if (-not [string]::IsNullOrWhiteSpace($env:LICENSE_TEXT_PATH)) {
    $pythonCommand = Get-Command -Name python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        Write-Error 'Python is required to convert the licence text file but was not found on PATH.'
        exit 1
    }

    $sourcePath = Resolve-Path -LiteralPath $env:LICENSE_TEXT_PATH -ErrorAction SilentlyContinue
    if (-not $sourcePath) {
        Write-Error "Licence text file not found: $env:LICENSE_TEXT_PATH"
        exit 1
    }

    $scriptPath = Join-Path -Path $env:GITHUB_ACTION_PATH -ChildPath 'scripts\plaintext_to_rtf.py'
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        Write-Error "Converter script not found: $scriptPath"
        exit 1
    }

    $arguments = @($scriptPath, '--input', $sourcePath.Path)
    if (-not [string]::IsNullOrWhiteSpace($env:LICENSE_RTF_PATH)) {
        $rtfTarget = [System.IO.Path]::GetFullPath($env:LICENSE_RTF_PATH)
        $rtfDirectory = Split-Path -Path $rtfTarget -Parent
        if (-not [string]::IsNullOrWhiteSpace($rtfDirectory) -and -not (Test-Path -LiteralPath $rtfDirectory)) {
            New-Item -ItemType Directory -Path $rtfDirectory -Force | Out-Null
        }
        $arguments += @('--output', $rtfTarget)
    }

    $process = & $pythonCommand.Source @arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    $generatedPath = $process.Trim()
    if (-not (Test-Path -LiteralPath $generatedPath)) {
        Write-Error "Expected licence RTF file was not created: $generatedPath"
        exit 1
    }

    Write-Host "Converted licence text to RTF: $generatedPath"
}

function Get-SafeName([string] $value, [string] $fallback) {
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $fallback
    }
    $sanitised = $value -replace '[^A-Za-z0-9._-]', '-'
    $sanitised = $sanitised.Trim('-_.')
    if ([string]::IsNullOrWhiteSpace($sanitised)) {
        return $fallback
    }
    return $sanitised
}

$outDir = $env:OUTPUT_DIRECTORY
if (Test-Path -LiteralPath $outDir) {
    $outDirItem = Get-Item -LiteralPath $outDir
}
else {
    $outDirItem = New-Item -ItemType Directory -Path $outDir
}

$safeBaseName = Get-SafeName -value $env:OUTPUT_BASENAME -fallback 'package'
$archInput = if ([string]::IsNullOrWhiteSpace($env:ARCHITECTURE)) { 'x64' } else { $env:ARCHITECTURE }
$archToken = $archInput.Trim()
switch -Regex ($archToken.ToLowerInvariant()) {
    '^(x64|amd64)$' { $arch = 'x64'; break }
    '^(x86|ia32|win32)$' { $arch = 'x86'; break }
    '^(arm64|aarch64)$' { $arch = 'arm64'; break }
    default {
        Write-Error "Unsupported architecture '$archInput'. Use x86, x64, or arm64."
        exit 1
    }
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

$arguments += @('-arch', $arch, "-dVersion=$($env:VERSION)", '-o', $outputPath)
wix @arguments

if (-not (Test-Path -LiteralPath $outputPath)) {
    Write-Error "MSI build failed: output file '$outputPath' was not created."
    exit 1
}

$resolved = (Resolve-Path -LiteralPath $outputPath).Path
"msi-path=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
