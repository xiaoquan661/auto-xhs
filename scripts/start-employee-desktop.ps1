# Start an employee task with the logged-in user's interactive token.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $encoding
[Console]::OutputEncoding = $encoding
$folder = $null
$taskName = $null
try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $sessions = @(
        Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {
            $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid
            if ($owner.Sid -eq $identity.User.Value -and $_.SessionId -gt 0) {
                [int]$_.SessionId
            }
        } | Select-Object -Unique
    )
    if ($sessions.Count -ne 1) { throw '请先登录员工机的 Windows 桌面再执行任务' }
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $definition = $scheduler.NewTask(0)
    $definition.Principal.UserId = $identity.Name
    $definition.Principal.LogonType = 3
    $definition.Principal.RunLevel = 0
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $definition.Settings.ExecutionTimeLimit = 'PT0S'
    $action = $definition.Actions.Create(0)
    $action.Path = [string]$request.executable
    $action.Arguments = [string]$request.arguments
    $action.WorkingDirectory = [string]$request.working_directory
    $taskName = 'AutoXHS-Employee-' + [guid]::NewGuid().ToString('N')
    $task = $folder.RegisterTaskDefinition($taskName, $definition, 6, $identity.Name, $null, 3)
    $null = $task.RunEx($null, 4, $sessions[0], $null)
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-Path -LiteralPath $request.receipt_path) {
            $receipt = Get-Content -LiteralPath $request.receipt_path -Raw -Encoding UTF8 |
                ConvertFrom-Json
            @{success=$true; pid=[int]$receipt.pid; session_id=$sessions[0]} |
                ConvertTo-Json -Compress
            exit 0
        }
    }
    throw '员工执行进程尚未返回桌面启动回执'
} catch {
    @{success=$false; message=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
} finally {
    if ($folder -and $taskName) {
        try { $folder.DeleteTask($taskName, 0) } catch { }
    }
}
