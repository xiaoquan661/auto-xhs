[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Account
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Bootstrap = Join-Path $PSScriptRoot "bootstrap.ps1"
$Cli = Join-Path $PSScriptRoot "cli.py"

$Raw = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Bootstrap
if ($LASTEXITCODE -ne 0 -or -not $Raw) {
    exit 2
}
$Report = @($Raw)[-1] | ConvertFrom-Json
if ($Report.status -ne "ready" -or -not $Report.python_executable) {
    exit 2
}

Push-Location $ProjectRoot
try {
    & $Report.python_executable $Cli --account $Account account-start *> $null
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
