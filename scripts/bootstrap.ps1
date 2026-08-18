[CmdletBinding()]
param(
    [switch]$Prepare,
    [switch]$InstallMissingPython,
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$MinimumPython = [version]"3.11.0"

function Write-BootstrapResult {
    param(
        [string]$Status,
        [string]$Message,
        [string]$PythonExecutable = "",
        [string]$PythonVersionValue = "",
        [string]$Source = "",
        [bool]$NeedsUserConsent = $false,
        [string]$NextCommand = ""
    )

    [ordered]@{
        status = $Status
        message = $Message
        python_executable = $PythonExecutable
        python_version = $PythonVersionValue
        source = $Source
        needs_user_consent = $NeedsUserConsent
        next_command = $NextCommand
    } | ConvertTo-Json -Compress
}

function Get-PythonProbe {
    param(
        [string]$Executable,
        [string[]]$PrefixArguments = @(),
        [string]$Source
    )

    try {
        $ProbeCode = "import json, sys; print(json.dumps({'executable': sys.executable, 'version': '.'.join(map(str, sys.version_info[:3]))}))"
        $Raw = & $Executable @PrefixArguments -c $ProbeCode 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $Raw) {
            return $null
        }
        $Data = @($Raw)[-1] | ConvertFrom-Json
        $Version = [version]$Data.version
        if ($Version -lt $MinimumPython) {
            return $null
        }
        return [pscustomobject]@{
            executable = [string]$Data.executable
            version = [string]$Data.version
            source = $Source
        }
    } catch {
        return $null
    }
}

function Find-CompatiblePython {
    if ($env:XHS_PYTHON) {
        $Probe = Get-PythonProbe -Executable $env:XHS_PYTHON -Source "explicit"
        if ($Probe) {
            return $Probe
        }
    }

    if (Test-Path -LiteralPath $VenvPython) {
        $Probe = Get-PythonProbe -Executable $VenvPython -Source "project_venv"
        if ($Probe) {
            return $Probe
        }
    }

    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        foreach ($Request in @("-3.14", "-3.13", "-3.12", "-3.11", "-3")) {
            $Probe = Get-PythonProbe -Executable $PyLauncher.Source `
                -PrefixArguments @($Request) -Source "system"
            if ($Probe) {
                return $Probe
            }
        }
    }

    foreach ($Name in @("python.exe", "python3.exe")) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) {
            $Probe = Get-PythonProbe -Executable $Command.Source -Source "system"
            if ($Probe) {
                return $Probe
            }
        }
    }
    return $null
}

function Test-ProjectReady {
    param([string]$PythonExecutable)

    $null = & $PythonExecutable (Join-Path $ProjectRoot "scripts\cli.py") --help 2>&1
    if ($LASTEXITCODE -ne 0) {
        return $false
    }

    # WebUI imports the Bridge client during startup. Checking this entry point
    # catches missing runtime packages such as websockets before the detached
    # server process hides the import error behind a health-check timeout.
    $null = & $PythonExecutable (Join-Path $ProjectRoot "scripts\web_server.py") --help 2>&1
    return $LASTEXITCODE -eq 0
}

function Invoke-Checked {
    param(
        [string]$Executable,
        [string[]]$Arguments,
        [string]$Description
    )

    $Output = & $Executable @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed: $($Output -join [Environment]::NewLine)"
    }
}

function Resolve-Uv {
    $Command = Get-Command uv.exe -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }
    foreach ($Candidate in @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\uv.exe")
    )) {
        if (Test-Path -LiteralPath $Candidate) {
            return $Candidate
        }
    }
    return $null
}

function Install-ManagedPythonEnvironment {
    $UvExecutable = Resolve-Uv
    if (-not $UvExecutable) {
        $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $Winget) {
            throw "Python, uv, and winget are unavailable; install Python >= 3.11 or official uv"
        }
        Invoke-Checked -Executable $Winget.Source -Description "Install uv" -Arguments @(
            "install",
            "--id=astral-sh.uv",
            "-e",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--silent"
        )
        $UvExecutable = Resolve-Uv
        if (-not $UvExecutable) {
            throw "uv was installed but is not visible in this PowerShell; reopen the terminal and retry"
        }
    }

    Invoke-Checked -Executable $UvExecutable -Description "Install managed Python" `
        -Arguments @("python", "install", $PythonVersion)

    Push-Location $ProjectRoot
    try {
        Invoke-Checked -Executable $UvExecutable -Description "Sync project environment" `
            -Arguments @("sync", "--extra", "dev", "--python", $PythonVersion)
    } finally {
        Pop-Location
    }
}

$Python = Find-CompatiblePython
if (-not $Python) {
    if (-not $InstallMissingPython) {
        Write-BootstrapResult -Status "python_missing" `
            -Message "Python >= 3.11 was not found; user consent is required before installation" `
            -NeedsUserConsent $true `
            -NextCommand "powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Prepare -InstallMissingPython"
        exit 3
    }

    try {
        Install-ManagedPythonEnvironment
        $Python = Find-CompatiblePython
        if (-not $Python) {
            throw "Managed Python was installed but the project interpreter is still unavailable"
        }
    } catch {
        Write-BootstrapResult -Status "error" -Message $_.Exception.Message
        exit 1
    }
}

if (Test-ProjectReady -PythonExecutable $Python.executable) {
    Write-BootstrapResult -Status "ready" -Message "Python and project dependencies are ready" `
        -PythonExecutable $Python.executable -PythonVersionValue $Python.version `
        -Source $Python.source
    exit 0
}

if (-not $Prepare) {
    Write-BootstrapResult -Status "dependencies_missing" `
        -Message "Python is available but project dependencies are not ready" `
        -PythonExecutable $Python.executable -PythonVersionValue $Python.version `
        -Source $Python.source `
        -NextCommand "powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Prepare"
    exit 2
}

try {
    if ($Python.source -ne "project_venv") {
        Invoke-Checked -Executable $Python.executable -Description "Create project virtual environment" `
            -Arguments @("-m", "venv", (Join-Path $ProjectRoot ".venv"))
    }
    Invoke-Checked -Executable $VenvPython -Description "Install project dependencies" `
        -Arguments @("-m", "pip", "install", "--disable-pip-version-check", "-e", "${ProjectRoot}[dev]")
    $Python = Get-PythonProbe -Executable $VenvPython -Source "project_venv"
    if (-not $Python -or -not (Test-ProjectReady -PythonExecutable $Python.executable)) {
        throw "The environment was prepared but the CLI or WebUI self-check failed"
    }
    Write-BootstrapResult -Status "ready" -Message "Project virtual environment and dependencies are ready" `
        -PythonExecutable $Python.executable -PythonVersionValue $Python.version `
        -Source $Python.source
    exit 0
} catch {
    Write-BootstrapResult -Status "error" -Message $_.Exception.Message
    exit 1
}
