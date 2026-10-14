# Launch the bound Chrome environment in its owner's interactive Windows session.
$ErrorActionPreference = 'Stop'
$encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $encoding
[Console]::OutputEncoding = $encoding
$taskName = $null
$folder = $null

try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $desktopSessions = @(
        Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" | ForEach-Object {
            $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid
            if ($owner.Sid -eq $identity.User.Value -and $_.SessionId -gt 0) {
                [int]$_.SessionId
            }
        } | Select-Object -Unique
    )
    if ($desktopSessions.Count -ne 1) {
        throw '请确保当前 Windows 用户已登录一个员工机桌面，再启动 Profile'
    }
    $desktopSession = $desktopSessions[0]
    $dataPattern = '(?:^|\s)--user-data-dir=' +
        [regex]::Escape([string]$request.user_data_dir) + '(?=\s|$)'
    function Get-BoundChrome {
        @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object {
            $_.CommandLine -and $_.CommandLine -notmatch '--type=' -and
            $_.CommandLine.Replace('"', '') -match $dataPattern
        })
    }
    $background = @(Get-BoundChrome | Where-Object { $_.SessionId -ne $desktopSession })
    if ($background.Count -gt 0) {
        $processIds = ($background | ForEach-Object { $_.ProcessId }) -join ', '
        throw "该 Chrome 用户目录被其他会话占用（进程 $processIds）；请先关闭该 Chrome 再从桌面启动"
    }

    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $definition = $scheduler.NewTask(0)
    $definition.Principal.UserId = $identity.Name
    $definition.Principal.LogonType = 3 # TASK_LOGON_INTERACTIVE_TOKEN
    $definition.Principal.RunLevel = 0
    $definition.Settings.AllowDemandStart = $true
    $definition.Settings.DisallowStartIfOnBatteries = $false
    $definition.Settings.StopIfGoingOnBatteries = $false
    $definition.Settings.ExecutionTimeLimit = 'PT0S'
    $action = $definition.Actions.Create(0)
    $action.Path = [string]$request.executable
    $action.Arguments = [string]$request.arguments
    $taskName = 'AutoXHS-DesktopChrome-' + [guid]::NewGuid().ToString('N')
    $task = $folder.RegisterTaskDefinition($taskName, $definition, 6, $identity.Name, $null, 3)
    $null = $task.RunEx($null, 4, $desktopSession, $null) # TASK_RUN_USE_SESSION_ID
    $chrome = $null
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        $chrome = Get-BoundChrome | Where-Object { $_.SessionId -eq $desktopSession } |
            Select-Object -First 1
        if ($chrome -and $task.State -ne 2) { break }
    }
    if (-not $chrome) {
        throw "Chrome 尚未出现在员工机桌面会话 $desktopSession，请检查桌面启动情况"
    }
    @{
        success = $true
        pid = [int]$chrome.ProcessId
        session_id = $desktopSession
        launch_method = 'interactive_desktop'
    } | ConvertTo-Json -Compress
} catch {
    @{ success = $false; message = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
} finally {
    if ($folder -and $taskName) {
        try { $folder.DeleteTask($taskName, 0) } catch { }
    }
}
