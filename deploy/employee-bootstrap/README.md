# 员工机接入包

本包用于 Windows 11 x64、Python 3.12 员工机。从经理机运行一条命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\onboard.ps1 -MachineId PC065 -SshHost 192.168.110.48
```

接入程序通过 SSH 传输包内项目源码和 Cockpit 安装包，随后在目标机创建 `.venv`、
从包内 `wheelhouse` 安装项目依赖、配置 `C:\auto-xhs\manager-task-inbox` 和
`C:\auto-xhs\manager-task-outbox`、启动本地执行服务，并保持机器暂停。
虚拟环境按目标机的 Python 创建，路径固定在项目目录下。经理机自动登记并检查状态。

新机器替换机器编号和 SSH 地址即可。登记后用 `machine check` 核对 SSH、执行服务和目录。
账号运行前仍需在员工机绑定 Chrome Profile、加载扩展并连接 Bridge，登录 Codex；
确认账号可用后由经理机恢复该机器的调度。
