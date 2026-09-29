param(
    [Parameter(Mandatory = $true)][string]$MachineId,
    [Parameter(Mandatory = $true)][string]$SshHost,
    [string]$SshUser = 'EDY',
    [string]$IdentityFile = 'C:\Users\EDY\.ssh\openclaw_device_ed25519',
    [string]$ManagerHome = 'C:\auto-xhs\manager-state',
    [string]$ProjectRoot = 'C:\Users\EDY\Documents\Codex\2026-07-31\readme-md\xiaohongshu-skills'
)

$ErrorActionPreference = 'Stop'
$MachineId = $MachineId.ToUpperInvariant()
$inputFile = Join-Path $env:TEMP "manager-machine-$MachineId.json"
$machine = @{
    manager_id = 'MILA-MANAGER'
    machine_id = $MachineId
    host_alias = $MachineId
    ssh_host = $SshHost
    ssh_port = 22
    ssh_user = $SshUser
    working_directory = 'C:\auto-xhs\xiaohongshu-skills'
    python_executable = 'C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe'
    identity_file = $IdentityFile
    task_inbox_root = 'C:\auto-xhs\manager-task-inbox'
    task_outbox_root = 'C:\auto-xhs\manager-task-outbox'
    allowed_versions = @('0.1.0')
    expected_role = 'executor'
    connect_timeout_seconds = 10
}
[System.IO.File]::WriteAllText(
    $inputFile,
    ($machine | ConvertTo-Json -Depth 4),
    (New-Object System.Text.UTF8Encoding($false))
)
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$cli = Join-Path $ProjectRoot 'scripts\manager_cli.py'
& $python $cli --manager-home $ManagerHome machine register --input-file $inputFile
if ($LASTEXITCODE -ne 0) { throw '经理机登记失败' }
& $python $cli --manager-home $ManagerHome machine check --machine-id $MachineId
if ($LASTEXITCODE -ne 0) { throw '经理机检查失败' }
