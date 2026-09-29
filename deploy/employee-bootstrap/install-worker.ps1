param(
    [Parameter(Mandatory = $true)][string]$MachineId,
    [string]$ProjectRoot = 'C:\auto-xhs\xiaohongshu-skills',
    [switch]$StartExecutor
)

$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$MachineId = $MachineId.ToUpperInvariant()
$sourceZip = Join-Path $PSScriptRoot 'xiaohongshu-skills.zip'
if (-not (Test-Path (Join-Path $ProjectRoot 'scripts\manager_worker.py'))) {
    if (-not (Test-Path $sourceZip)) { throw '缺少项目源码包' }
    New-Item -ItemType Directory -Force -Path $ProjectRoot | Out-Null
    Expand-Archive -LiteralPath $sourceZip -DestinationPath $ProjectRoot -Force
}
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    & python -m venv (Join-Path $ProjectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 虚拟环境创建失败' }
}
$wheelhouse = Join-Path $PSScriptRoot 'wheelhouse'
if (-not (Test-Path $wheelhouse)) { throw '缺少 wheelhouse 依赖目录' }
& $python -m pip install --disable-pip-version-check --no-index --find-links $wheelhouse setuptools wheel
if ($LASTEXITCODE -ne 0) { throw '构建依赖安装失败' }
& $python -m pip install --disable-pip-version-check --no-index --find-links $wheelhouse 'python-socks>=2.8.1' 'requests>=2.28.0' 'websockets>=12.0'
if ($LASTEXITCODE -ne 0) { throw '项目依赖安装失败' }

$request = @{
    action = 'configure'
    payload = @{
        machine_id = $MachineId
        role = 'executor'
        project_version = '0.1.0'
        package_root = 'C:\auto-xhs\manager-packages'
        task_inbox_root = 'C:\auto-xhs\manager-task-inbox'
        task_outbox_root = 'C:\auto-xhs\manager-task-outbox'
    }
} | ConvertTo-Json -Depth 4 -Compress
$entry = Join-Path $ProjectRoot 'scripts\manager_worker.py'
$result = $request | & $python $entry | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $result.success) { throw '员工机配置失败' }

if ($StartExecutor) {
    $started = '{"action":"start-executor","payload":{}}' | & $python $entry | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $started.success) { throw '员工执行服务启动失败' }
}
# A newly registered machine stays paused until its account and browser are ready.
$pause = '{"action":"pause-machine","payload":{}}' | & $python $entry | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $pause.success) { throw '员工机暂停设置失败' }
$health = '{"action":"health","payload":{}}' | & $python $entry | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $health.success) { throw '员工机健康检查失败' }
$health | ConvertTo-Json -Depth 8
