[CmdletBinding()]
param(
    [int]$Port = 8765,
    [ValidateSet("0.0.0.0", "127.0.0.1")][string]$ListenAddress = "0.0.0.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
while (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "source-layout.json"))) {
    $ProjectRoot = Split-Path $ProjectRoot -Parent
}
$ScriptsRoot = Join-Path $ProjectRoot "scripts"
$BootstrapOutput = & powershell -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $ScriptsRoot "bootstrap.ps1") -Prepare
$BootstrapResult = @($BootstrapOutput)[-1] | ConvertFrom-Json

if ($BootstrapResult.status -eq "python_missing") {
    Write-Host "未找到 Python 3.11 或更高版本。安装运行环境需要你的确认。" -ForegroundColor Yellow
    Write-Host "请在 WebUI 安装说明或项目文档中继续。"
    exit 3
}
if ($BootstrapResult.status -ne "ready") {
    Write-Host $BootstrapResult.message -ForegroundColor Red
    exit 1
}

& $BootstrapResult.python_executable (Join-Path $ScriptsRoot "web_lifecycle.py") `
    start --python $BootstrapResult.python_executable --project-root $ProjectRoot `
    --host $ListenAddress --port $Port --open-browser
exit $LASTEXITCODE
