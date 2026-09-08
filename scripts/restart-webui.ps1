[CmdletBinding()]
param(
    [int]$Port = 8765,
    [ValidateSet("0.0.0.0", "127.0.0.1")][string]$ListenAddress = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BootstrapOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $PSScriptRoot "bootstrap.ps1") -Prepare
$BootstrapResult = @($BootstrapOutput)[-1] | ConvertFrom-Json

if ($BootstrapResult.status -eq "python_missing") {
    Write-Host "未找到 Python 3.11 或更高版本。安装运行环境需要你的确认。" -ForegroundColor Yellow
    exit 3
}
if ($BootstrapResult.status -ne "ready") {
    Write-Host $BootstrapResult.message -ForegroundColor Red
    exit 1
}

& $BootstrapResult.python_executable (Join-Path $PSScriptRoot "web_lifecycle.py") `
    restart --python $BootstrapResult.python_executable --project-root $ProjectRoot `
    --host $ListenAddress --port $Port
exit $LASTEXITCODE
