[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path -Path $PSScriptRoot -ChildPath 'common.ps1')

$script:PythonCommand = $null
$script:PythonArgs = @()

function Ensure-WixToolAvailable {
    $wixCommand = Get-Command -Name wix -ErrorAction SilentlyContinue
    if (-not $wixCommand) {
        Write-Error 'WiX toolset not found on PATH. Ensure install_wix_cli.ps1 completed successfully.'
        exit 1
    }

    if ([string]::IsNullOrWhiteSpace($wixCommand.Source)) {
        $wixCommand = [PSCustomObject]@{
            Name   = $wixCommand.Name
            Source = $wixCommand.Name
        }
    }

    return $wixCommand
}

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

function Invoke-WithTemporaryInputVersion {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ScriptBlock]
        $ScriptBlock,

        [string]
        $Version
    )

    $hadInputVersion = Test-Path Env:INPUT_VERSION
    $previousInputVersion = if ($hadInputVersion) { $env:INPUT_VERSION } else { $null }
    $hadNonEmptyInputVersion = $hadInputVersion -and -not [string]::IsNullOrWhiteSpace($previousInputVersion)

    $setNewInputVersion = $false
    try {
        if (-not [string]::IsNullOrWhiteSpace($Version)) {
            $env:INPUT_VERSION = $Version.Trim()
            if (-not $hadInputVersion) {
                $setNewInputVersion = $true
            }
        }
        elseif (-not $hadNonEmptyInputVersion) {
            throw [System.InvalidOperationException]::new('VERSION environment variable or INPUT_VERSION must be provided to generate WiX authoring.')
        }

        return & $ScriptBlock
    }
    finally {
        if ($hadInputVersion) {
            $env:INPUT_VERSION = $previousInputVersion
        }
        elseif ($setNewInputVersion) {
            Remove-Item Env:INPUT_VERSION -ErrorAction SilentlyContinue
        }
    }
}

function Ensure-OutputDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]
        $Path
    )

    $directory = Split-Path -Path $Path -Parent
    if (-not [string]::IsNullOrWhiteSpace($directory) -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
}

function Build-ConverterArguments {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]
        $Converter,

        [Parameter(Mandatory = $true)]
        [string]
        $SourcePath
    )

    $arguments = @(
        $Converter,
        '--input',
        $SourcePath
    )

    if (-not [string]::IsNullOrWhiteSpace($env:LICENSE_RTF_PATH)) {
        $rtfTarget = [System.IO.Path]::GetFullPath($env:LICENSE_RTF_PATH)
        Ensure-OutputDirectory -Path $rtfTarget
        $arguments += @('--output', $rtfTarget)
    }

    return ,$arguments
}

function Process-LicenseFile {
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

        $arguments = Build-ConverterArguments -Converter $converter -SourcePath $sourcePath.Path

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

    return $env:LICENSE_RTF_PATH
}

function Build-WxsGenerationArguments {
    param(
        [Parameter(Mandatory = $true)]
        [string]
        $Generator,

        [Parameter(Mandatory = $true)]
        [string]
        $TargetWxs,

        [Parameter(Mandatory = $true)]
        [string]
        $Architecture,

        [Parameter(Mandatory = $true)]
        [string]
        $ApplicationSpec
    )

    $arguments = @(
        $Generator,
        '--output',
        $TargetWxs,
        '--architecture',
        $Architecture,
        '--application',
        $ApplicationSpec
    )

    $optionalArgs = @{
        'PRODUCT_NAME'    = '--product-name'
        'MANUFACTURER'    = '--manufacturer'
        'INSTALL_DIR_NAME' = '--install-dir-name'
        'DESCRIPTION'     = '--description'
        'UPGRADE_CODE'    = '--upgrade-code'
        'LICENSE_RTF_PATH' = '--license-path'
    }

    foreach ($envVar in $optionalArgs.Keys) {
        $value = ${env:$envVar}
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $arguments += @($optionalArgs[$envVar], $value)
        }
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

    return ,$arguments
}

function Resolve-ApplicationSpecification {
    [CmdletBinding()]
    param()

    $spec = if ([string]::IsNullOrWhiteSpace($env:APPLICATION_SPEC)) { '' } else { $env:APPLICATION_SPEC.Trim() }
    if ([string]::IsNullOrWhiteSpace($spec)) {
        Write-Error 'application-path input is required when wxs-path is not provided.'
        exit 1
    }

    return $spec
}

function Validate-GeneratorScript {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]
        $GeneratorPath
    )

    if (-not (Test-Path -LiteralPath $GeneratorPath)) {
        Write-Error "WiX generator script not found: $GeneratorPath"
        exit 1
    }

    return $GeneratorPath
}

function Build-PythonCommandDisplay {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string[]]
        $Arguments
    )

    Ensure-PythonCommand
    $pythonExecutable = if ($script:PythonCommand.Source) { $script:PythonCommand.Source } else { $script:PythonCommand.Name }
    $pythonArgumentList = @()
    if ($script:PythonArgs.Count -gt 0) {
        $pythonArgumentList += $script:PythonArgs
    }
    $pythonArgumentList += $Arguments

    return @{
        Executable  = $pythonExecutable
        FullCommand = $pythonArgumentList
    }
}

function Invoke-WxsGeneration {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string[]]
        $Arguments
    )

    try {
        $generationResult = Invoke-WithTemporaryInputVersion -Version $env:VERSION -ScriptBlock {
            Invoke-PythonScript -Arguments $Arguments
        }
    }
    catch [System.InvalidOperationException] {
        Write-Error $_.Exception.Message
        exit 1
    }

    $generatedWxs = $generationResult.Trim()
    if (-not (Test-Path -LiteralPath $generatedWxs)) {
        Write-Error "Expected WiX authoring was not created: $generatedWxs"
        exit 1
    }

    return $generatedWxs
}

function Ensure-WxsFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]
        $Architecture
    )

    if ([string]::IsNullOrWhiteSpace($env:WXS_PATH)) {
        $applicationSpec = Resolve-ApplicationSpecification

        $generator = Join-Path -Path $env:GITHUB_ACTION_PATH -ChildPath 'scripts\generate_wxs.py'
        $generatorPath = Validate-GeneratorScript -GeneratorPath $generator

        $targetWxs = Join-Path -Path ([System.IO.Path]::GetTempPath()) -ChildPath ("windows-package-{0}.wxs" -f ([guid]::NewGuid()))
        $arguments = Build-WxsGenerationArguments -Generator $generatorPath -TargetWxs $targetWxs -Architecture $Architecture -ApplicationSpec $applicationSpec

        Write-Host "Generating WXS file at: $targetWxs"
        $commandInfo = Build-PythonCommandDisplay -Arguments $arguments
        Write-Host "Generation command: $($commandInfo.Executable) $($commandInfo.FullCommand -join ' ')"

        $generatedWxs = Invoke-WxsGeneration -Arguments $arguments
        $env:WXS_PATH = (Resolve-Path -LiteralPath $generatedWxs).Path
        Write-Host "Generated default WiX authoring: $($env:WXS_PATH)"
    }
    elseif (-not (Test-Path -LiteralPath $env:WXS_PATH)) {
        Write-Error "WiX source file not found: $env:WXS_PATH"
        exit 1
    }

    return $env:WXS_PATH
}

function Build-WixExtensionArguments {
    [CmdletBinding()]
    param(
        [string]
        $ExtensionList
    )

    if ([string]::IsNullOrWhiteSpace($ExtensionList)) {
        return ,@()
    }

    $arguments = @()
    foreach ($extension in ($ExtensionList -split '[,\s]+')) {
        $trimmed = $extension.Trim()
        if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
            $arguments += @('-ext', $trimmed)
        }
    }

    return ,$arguments
}

function Write-WixOutput {
    [CmdletBinding()]
    param(
        [string[]]
        $Output
    )

    if (-not $Output) {
        return
    }

    Write-Host 'WiX output:'
    foreach ($line in $Output) {
        Write-Host $line
    }
}

function Build-MsiPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]
        $OutputPath,

        [Parameter(Mandatory = $true)]
        [string]
        $Architecture
    )

    $wixCommand = Ensure-WixToolAvailable

    if ([string]::IsNullOrWhiteSpace($env:VERSION)) {
        Write-Error "VERSION environment variable is required. Run resolve_version.ps1 or supply VERSION before invoking Build-MsiPackage."
        exit 1
    }

    $arguments = @('build', $env:WXS_PATH)
    $arguments += Build-WixExtensionArguments -ExtensionList $env:WIX_EXTENSION

    $arguments += @('-arch', $Architecture, '-d', "Version=$($env:VERSION)", '-o', $OutputPath)
    $wixExecutable = $wixCommand.Name
    $wixExecutablePath = $wixCommand.Source
    Write-Host "Executing WiX command: $wixExecutablePath $($arguments -join ' ')"

    $previousPreference = $ErrorActionPreference
    $wixOutput = @()
    $wixExitCode = 0
    try {
        $ErrorActionPreference = 'Continue'
        $wixOutput = & $wixExecutable @arguments 2>&1
        $wixExitCode = $LASTEXITCODE
    }
    catch {
        $wixExitCode = if ($LASTEXITCODE -ne 0) { $LASTEXITCODE } else { 1 }
        $errorMessage = ($_ | Out-String).TrimEnd()
        if ([string]::IsNullOrWhiteSpace($errorMessage)) {
            $errorMessage = $_.ToString()
        }
        $wixOutput = @($errorMessage)
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    Write-WixOutput -Output $wixOutput

    if ($wixExitCode -ne 0) {
        Write-Error -Message "WiX build failed with exit code $wixExitCode. See output above for details." -ErrorAction Continue
        $global:LASTEXITCODE = $wixExitCode
        if ($Host -and ($Host.PSObject.Properties.Name -contains 'SetShouldExit')) {
            $Host.SetShouldExit($wixExitCode)
        }
        exit $wixExitCode
    }

    if (-not (Test-Path -LiteralPath $OutputPath)) {
        Write-Error "MSI build completed with exit code 0 but output file '$OutputPath' was not created. This may indicate a WiX configuration issue."
        exit 1
    }

    return (Resolve-Path -LiteralPath $OutputPath).Path
}

function Invoke-Main {
    Process-LicenseFile | Out-Null

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

    Ensure-WxsFile -Architecture $arch | Out-Null

    $outputPath = Join-Path -Path $outDirItem.FullName -ChildPath ("{0}-{1}-{2}.msi" -f $safeBaseName, $env:VERSION, $arch)
    $resolved = Build-MsiPackage -OutputPath $outputPath -Architecture $arch

    "msi-path=$resolved" | Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-Main
}
