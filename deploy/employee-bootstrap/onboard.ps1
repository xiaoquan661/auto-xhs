param(
    [Parameter(Mandatory = $true)][string]$MachineId,
    [Parameter(Mandatory = $true)][string]$SshHost,
    [string]$SshUser = 'EDY',
    [string]$IdentityFile = 'C:\Users\EDY\.ssh\openclaw_device_ed25519',
    [string]$ManagerHome = 'C:\auto-xhs\manager-state'
)

$ErrorActionPreference = 'Stop'
$MachineId = $MachineId.ToUpperInvariant()
$archive = Join-Path $env:TEMP "employee-bootstrap-$MachineId.zip"
Compress-Archive -Path (Join-Path $PSScriptRoot '*') -DestinationPath $archive -Force
$remote = "${SshUser}@${SshHost}"
& scp -O -i $IdentityFile -o BatchMode=yes $archive "${remote}:C:/Users/$SshUser/Downloads/"
if ($LASTEXITCODE -ne 0) { throw '接入包传输失败' }
$remoteZip = "C:\Users\$SshUser\Downloads\employee-bootstrap-$MachineId.zip"
$remoteRoot = 'C:\auto-xhs\employee-bootstrap'
& ssh -i $IdentityFile -o BatchMode=yes $remote "powershell -NoProfile -Command Expand-Archive -LiteralPath $remoteZip -DestinationPath $remoteRoot -Force"
if ($LASTEXITCODE -ne 0) { throw '接入包展开失败' }
& ssh -i $IdentityFile -o BatchMode=yes $remote "powershell -NoProfile -ExecutionPolicy Bypass -File $remoteRoot\install-worker.ps1 -MachineId $MachineId -StartExecutor"
if ($LASTEXITCODE -ne 0) { throw '员工机安装失败' }
& (Join-Path $PSScriptRoot 'register-manager.ps1') -MachineId $MachineId -SshHost $SshHost -SshUser $SshUser -IdentityFile $IdentityFile -ManagerHome $ManagerHome
if (-not $?) { throw '经理机登记失败' }
