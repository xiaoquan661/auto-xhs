# Chrome Profile 桌面启动

Windows 上的账号启动入口会将绑定的 Chrome Profile 打开到员工机已登录用户的桌面。
CLI、本地应用服务和经理机远程账号操作共用 `scripts/chrome_lifecycle.py`。

## 启动流程

1. 确认账号绑定的 Chrome 用户数据目录和 Profile 目录。
2. 启动 Bridge 并检查当前扩展连接；已核验的连接可直接复用。
3. 需要打开 Chrome 时，由 `start-chrome-desktop.ps1` 查找当前 Windows 用户的桌面会话。
4. 创建一次性 Windows 计划任务，以交互式登录令牌在该桌面启动绑定 Profile 的新窗口。
5. 核对 Chrome 主进程属于目标桌面会话，并删除本次计划任务定义。
6. 等待扩展连接及 Profile 核验，返回实际就绪状态。

员工机需要登录对应的 Windows 用户。桌面会话不能确定时，入口返回具体提示。
同一 Chrome 用户数据目录被其他会话占用时，需要先处理占用进程，再启动桌面窗口。

每个 Profile 分别加载项目扩展并完成账号槽位配对。完成并正常保存后，Chrome 后续启动
会沿用该 Profile 的扩展配置。桌面窗口出现和扩展完成连接是两个分别核对的状态。

计划任务使用 `TASK_LOGON_INTERACTIVE_TOKEN` 与指定桌面会话的 `RunEx`。
Windows 接口依据：[Microsoft RunEx 文档](https://learn.microsoft.com/en-us/windows/win32/taskschd/registeredtask-runex)。

## 耗时记录

员工机的 `manager-worker/worker-state.json` 在 `profile_startups` 中保留最近 100 条记录。
记录包含账号、时间、请求耗时、就绪状态、错误，以及 Bridge、Chrome 启动和扩展等待阶段耗时。
启动结果的 `chrome` 字段还包含桌面 `session_id` 和 `launch_method=interactive_desktop`。

统计时区分：

- Chrome 桌面启动阶段：交互式启动请求到检测到桌面 Chrome 主进程。
- 扩展就绪阶段：等待扩展与绑定 Profile 完成核验。
- 本机 API 请求：包含以上阶段以及接口处理。
- SSH 往返：另含远程连接建立及命令执行开销。

一般成功启动耗时应使用 `ready=true` 且 `timing.mode=launch` 的成功记录统计。
`mode=reuse` 单独统计。扩展未连接的记录用于诊断等待时间。
