# 第二套经理机与员工机部署指南

本文用于在新的 Windows 系统中部署一套独立的经理机和员工机。新系统从 GitHub 获取项目代码，
经理机维护独立运营状态，员工机维护各自的账号槽位、Chrome Profile、Bridge、任务收发目录和执行服务。

## 一、部署结构

推荐让一组员工机归属一台经理机：

```text
MILA-MANAGER-02
├── PC081
├── PC082
└── PC083
```

每台员工机使用唯一的 `machine_id`。经理机通过 SSH/SFTP 向员工机 `inbox` 投递任务，员工机执行后
写入 `outbox`，经理机回收并导入本机运营数据库。

GitHub 仓库负责同步项目代码。运行状态按机器保存：

| 内容 | 保存位置 |
| --- | --- |
| 经理状态与运营数据库 | `C:\auto-xhs\manager-state` |
| 员工协议状态 | `%USERPROFILE%\.xhs\auto-xhs\manager-worker` |
| 员工任务收件箱 | `C:\auto-xhs\manager-task-inbox` |
| 员工结果发件箱 | `C:\auto-xhs\manager-task-outbox` |
| 员工项目目录 | `C:\auto-xhs\xiaohongshu-skills` |
| Chrome Profile、登录会话和扩展 | 对应员工机本地 |

## 二、准备信息

部署前确定以下内容：

| 项目 | 示例 |
| --- | --- |
| 新经理编号 | `MILA-MANAGER-02` |
| 员工机编号 | `PC081`、`PC082` |
| 员工机地址 | `192.168.110.81` |
| 员工机 Windows 用户 | `EDY` |
| 经理机运营台端口 | `8765` |
| 每台员工机的账号槽位 | `6-1`、`6-2` |

经理机和员工机安装 Windows 11 x64、Git、Python 3.12。员工机启用 Windows OpenSSH Server，
经理机安装 OpenSSH Client。

## 三、安装新经理机

### 1. 获取项目

在新经理机 PowerShell 中执行：

```powershell
git clone https://github.com/xiaoquan661/auto-xhs.git C:\auto-xhs\xiaohongshu-skills
Set-Location C:\auto-xhs\xiaohongshu-skills
git switch main
git pull --ff-only origin main
```

### 2. 准备运行环境

```powershell
powershell -ExecutionPolicy Bypass `
  -File C:\auto-xhs\xiaohongshu-skills\scripts\bootstrap.ps1 `
  -Prepare
```

脚本会创建项目 `.venv` 并安装依赖。完成后验证经理 CLI：

```powershell
C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_cli.py --help
```

### 3. 设置经理状态目录

```powershell
[Environment]::SetEnvironmentVariable(
  "XHS_MANAGER_HOME",
  "C:\auto-xhs\manager-state",
  "User"
)
```

关闭并重新打开 PowerShell，使用户环境变量生效。

### 4. 启动运营台

```powershell
powershell -ExecutionPolicy Bypass `
  -File C:\auto-xhs\xiaohongshu-skills\scripts\start-webui.ps1 `
  -ListenAddress 0.0.0.0 `
  -Port 8765
```

在经理机访问 `http://127.0.0.1:8765`。局域网设备使用经理机实际 IP 和相同端口访问。

## 四、配置经理机 SSH

### 1. 创建经理机专用密钥

```powershell
ssh-keygen -t ed25519 -f C:\Users\EDY\.ssh\auto_xhs_manager_02
```

将生成的 `.pub` 公钥内容加入每台员工机当前 Windows 用户的 OpenSSH `authorized_keys`，然后在
经理机验证：

```powershell
ssh -i C:\Users\EDY\.ssh\auto_xhs_manager_02 EDY@192.168.110.81 hostname
```

命令应返回目标员工机名称。

## 五、安装员工机

以下操作在每台新员工机执行。

### 1. 获取项目和依赖

```powershell
git clone https://github.com/xiaoquan661/auto-xhs.git C:\auto-xhs\xiaohongshu-skills
Set-Location C:\auto-xhs\xiaohongshu-skills
git switch main
git pull --ff-only origin main

powershell -ExecutionPolicy Bypass `
  -File C:\auto-xhs\xiaohongshu-skills\scripts\bootstrap.ps1 `
  -Prepare
```

### 2. 配置员工协议目录

以 `PC081` 为例，新建 `C:\auto-xhs\worker-config-PC081.json`：

```json
{
  "action": "configure",
  "payload": {
    "machine_id": "PC081",
    "role": "executor",
    "project_version": "0.1.0",
    "package_root": "C:\\auto-xhs\\manager-packages",
    "task_inbox_root": "C:\\auto-xhs\\manager-task-inbox",
    "task_outbox_root": "C:\\auto-xhs\\manager-task-outbox"
  }
}
```

应用配置：

```powershell
Get-Content -Raw -Encoding UTF8 C:\auto-xhs\worker-config-PC081.json |
  C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_worker.py
```

### 3. 启动执行服务并保持暂停

```powershell
'{"action":"start-executor","payload":{}}' |
  C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_worker.py

'{"action":"pause-machine","payload":{}}' |
  C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_worker.py
```

读取本机健康状态：

```powershell
'{"action":"health","payload":{}}' |
  C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_worker.py
```

确认返回的机器编号、角色、项目版本、暂停状态、`task_inbox_root` 和 `task_outbox_root` 与本机配置一致。

## 六、在经理机登记员工机

在新经理机为每台员工机创建登记文件。以 `C:\auto-xhs\manager-input\PC081.json` 为例：

```json
{
  "manager_id": "MILA-MANAGER-02",
  "machine_id": "PC081",
  "host_alias": "PC081",
  "ssh_host": "192.168.110.81",
  "ssh_port": 22,
  "ssh_user": "EDY",
  "working_directory": "C:\\auto-xhs\\xiaohongshu-skills",
  "python_executable": "C:\\auto-xhs\\xiaohongshu-skills\\.venv\\Scripts\\python.exe",
  "identity_file": "C:\\Users\\EDY\\.ssh\\auto_xhs_manager_02",
  "task_inbox_root": "C:\\auto-xhs\\manager-task-inbox",
  "task_outbox_root": "C:\\auto-xhs\\manager-task-outbox",
  "allowed_versions": ["0.1.0"],
  "expected_role": "executor",
  "connect_timeout_seconds": 10
}
```

登记并检查：

```powershell
C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_cli.py `
  --manager-home C:\auto-xhs\manager-state `
  machine register `
  --input-file C:\auto-xhs\manager-input\PC081.json

C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_cli.py `
  --manager-home C:\auto-xhs\manager-state `
  machine check `
  --machine-id PC081
```

账号环境尚在准备时，检查结果显示 `PAUSED` 属于正常接入状态。

## 七、配置员工机账号

在每台员工机完成以下操作：

1. 为每个账号创建独立账号槽位。
2. 将槽位绑定到本机对应的 Chrome Profile。
3. 在该 Profile 中加载项目 `C:\auto-xhs\xiaohongshu-skills\extension`。
4. 启动槽位 Bridge，并在扩展中完成配对。
5. 登录对应平台账号。
6. 在运营台核对槽位、平台、扩展版本、Bridge 状态和已绑定身份。
7. 为账号保存人设、回复开关和运营规则。

一台员工机可以配置多个账号槽位。同一机器上的账号由员工队列串行执行，各槽位的身份、数据和回执
保持隔离。

## 八、恢复调度并验收

账号和扩展准备完成后，在经理机执行：

```powershell
C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_cli.py `
  --manager-home C:\auto-xhs\manager-state `
  machine resume `
  --machine-id PC081
```

按以下顺序验收：

1. `machine check` 返回员工机在线、执行服务运行、调度可用。
2. 员工健康信息中的 inbox/outbox 路径与经理登记一致。
3. 运营台能够读取机器和账号槽位。
4. 下发 Agent 探针任务，员工机生成领取回执。
5. 员工机 outbox 生成结果，经理机完成回收和入库。
6. 使用只读业务任务验证扩展截图、页面识别和账号隔离。
7. 再按运营计划启用发布、评论、回复等写入任务。

`TASK_ACK` 表示员工 Agent 已领取任务。业务完成状态以员工最终回执、outbox 写入和经理数据库导入为准。

## 九、日常升级

经理机更新代码：

```powershell
Set-Location C:\auto-xhs\xiaohongshu-skills
git switch main
git pull --ff-only origin main
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 -Prepare
```

员工机在没有运行中任务时执行相同更新，再重启执行服务：

```powershell
'{"action":"restart-executor","payload":{}}' |
  C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_worker.py
```

扩展文件更新后，在各账号对应的 Chrome Profile 中重新加载扩展，并以 Bridge 回报的运行版本作为
生效依据。

## 十、部署完成清单

- [ ] 新经理机运行最新 `main`。
- [ ] 经理状态目录为 `C:\auto-xhs\manager-state`。
- [ ] 新经理编号已写入员工登记文件。
- [ ] 每台员工机具有唯一机器编号。
- [ ] SSH 公钥登录验证通过。
- [ ] 员工机执行服务已启动。
- [ ] 两端 inbox/outbox 路径一致。
- [ ] Chrome Profile、扩展和 Bridge 已按槽位配置。
- [ ] 账号身份、人设和回复规则已保存。
- [ ] Agent 探针完成领取、执行、outbox 和经理回收闭环。
- [ ] 只读业务任务完成实机验收。
- [ ] 员工机已按运营安排恢复调度。

更多协议与任务状态说明见[经理机与 Windows 员工机 SSH 调度](MANAGER-SSH-OPERATIONS.md)。
