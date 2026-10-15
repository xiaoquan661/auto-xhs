# 员工机 V0.20 安装与验收

## 安装职责

PC023 维护员工源包，员工机安装对应的执行版本。五机的项目根为
`C:\auto-xhs\xiaohongshu-skills`，稳定调用入口为根 `scripts/`。

| 内容 | 项目根内位置 |
| --- | --- |
| 程序版本 | `WorkerStation/Workers/ProgramWorkers/packages/xiaohongshu-skills/0.1.0/worker/` |
| 程序实例 | `WorkerStation/Workers/ProgramWorkers/instances/<PC编号>/instance.json` |
| Python 环境引用 | `WorkerStation/Workers/ProgramWorkers/environments/existing-python/environment.json` |
| Agent 岗位 | `WorkerStation/Workers/AgentWorkers/<PC编号>/` |
| 模块与资源绑定 | `source-layout.json`、`webui-layout.json` |

程序版本包含 `runtime/scripts/`、`runtime/execution/`、`runtime/shared/`、`runtime/webui/`、
`skills/`、`extension/`、`prompts/`、合同与测试选择入口。执行服务、任务协议和 CLI 从稳定入口
解析到本机员工版本。员工修复安装器随执行支撑交付；服务健康与占用检查由共用模块提供。

## 数据与登录环境

Python 沿用项目根 `.venv`。账号、绑定身份和扩展配对位于本机 `.xhs/accounts/`；Chrome
沿用账号配置中的 User Data 与 Profile。已加载扩展沿账号的 `extension_dir` 使用既有安装位置。
版本包的 `worker/extension/` 是程序交付源，账号当前引用的扩展是本机运行安装。

任务交换沿 `C:\auto-xhs\manager-task-inbox`、`C:\auto-xhs\manager-task-outbox`。
协议状态、Agent 材料和日志沿 `C:\Users\EDY\.xhs\auto-xhs\manager-worker`。
这些权威路径由实例配置引用，部署过程保持账号调度开关、机器暂停状态和历史任务状态。

## 构建与安装

在干净的项目分支中构建指定机器的安装目录，目标目录使用本次新的绝对路径：

```powershell
$projectRoot = (Resolve-Path .).Path
python "$projectRoot\deploy\build-worker-layout.py" `
  --source $projectRoot --destination "C:\auto-xhs-build\PC065-v020" --machine-id PC065
```

将该目录内容压成 ZIP，经项目 OpenSSHTransport 传入目标机器本次
`C:\auto-xhs\deployments\<部署编号>\`。同时传入 `install-worker-layout.py`。
下载上传件并逐字节比较后，在目标机器运行：

```powershell
& "C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe" `
  "C:\auto-xhs\deployments\<部署编号>\install-worker-layout.py" `
  --archive "C:\auto-xhs\deployments\<部署编号>\package.zip" `
  --project-root "C:\auto-xhs\xiaohongshu-skills" `
  --backup-root "C:\auto-xhs\deployments\<部署编号>\backup" --machine-id PC065
```

安装器先检查协议任务、本机任务和 pending inbox；运行或待执行工作结束后才安装。
原 `scripts/`、`skills/`、`prompts/`、`webui/` 整体保存到 `backup/previous/`，包括本地源码修改。
被替换的根文件也在该处留存。备份中的 `baseline.json` 和受控配置参考件用于部署后核对。
安装回执记录传输文件数、字节数、逐项比较和备份位置。

## 逐机验收

1. 在安装目录中导入所有登记的员工模块，核对真实实现位于 `WorkerStation`；调用 CLI、
   员工固定任务入口和执行服务的帮助命令。
2. 空闲时重载执行服务。核对运行实例标识变化，以及健康接口、本机快照、首页、CSS、JS
   都返回 HTTP 200。端口响应与新代码实际重载分别核对。
3. 通过经理正常投递链下发 `employee-agent-probe`，使用 `deployment-probe` 测试槽位。
   探针只读本地任务并原样返回 `probe_token`。
4. 核对 `TASK_ACK`、`agent_claimed`、最终结果、`outbox_written`，经 SFTP 回收到经理库，
   逐项核对任务与结果。员工任务成功与经理导入成功分别登记。
5. 核对账号与配对配置逐字节一致，原历史任务状态和历史 inbox/outbox 文件保留，机器与
   账号暂停设置一致。保留每台机器的实际验收及程序包 `acceptance.json`。

2026-10-15 先以 PC065 完成全链验收，再安装其余四台。设备证据见
[本次部署记录](../ManagerStation/Managers/PC023/migration/2026-10-15-worker-v020-deployment.json)。
PC052、PC057、PC065、PC071、PC073 的接单、Agent 领取、本地探针回传与经理入库均通过。
PC073 登录恢复后完成最终验证，任务与结果在经理数据库均为 SUCCESS，返回的 probe_token
与原任务一致；五机的程序目录及本地连接验收完成。
