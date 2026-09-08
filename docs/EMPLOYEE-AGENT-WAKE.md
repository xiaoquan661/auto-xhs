# 员工 Agent 任务执行连接

经理机把已确认的任务投递至员工机 inbox，通过 SSH `wake-task` 启动员工 Codex 执行。每条任务使用员工机已安装、已登录的 Codex 独立运行，沿用机器配置的模型。任务上下文由任务文件、人设快照和项目技能提供。

## 执行流程

1. 经理机通过 SFTP 投递任务文件，调用 SSH `wake-task`。
2. 员工服务接收任务、保存执行编号，将文件从 `pending` 移至 `claimed`，返回接收回执。
3. Windows 员工机通过任务计划服务在已登录桌面会话中启动 `manager_worker_async.py`，按执行队列调用 `EmployeeCodexRunner` 和 `codex exec`。启动项收到进程回执后移除，每次唤醒对应一次执行。
4. Codex 读取任务后写入 `claimed.json`。员工服务据此生成 `agent_claimed` 进度记录。
5. Codex 依据任务、人设和授权，通过项目 CLI / ApplicationService 执行，保存证据并返回结构化结果。
6. 员工服务把执行结果写入 outbox。经理机后台每 300 秒拉取新增记录，去重入库并更新控制台。

接收回执表示任务进入员工执行队列；`agent_claimed` 表示模型实际读到了任务；最终业务状态表示执行结论。SSH 请求返回后，执行过程由员工机负责。

## 运行约定

- 员工机安装并登录 Codex；程序优先从 PATH 查找，其次查找当前用户的 Codex 安装目录。
- Windows 员工执行使用已登录用户的桌面会话、`workspace-write` 和 `windows.sandbox=unelevated`，命令工具指定系统 `cmd.exe`，文件读写使用项目 Python。Chrome 复用该桌面上的现有 Profile。
- 每次执行复用 Codex 的登录和模型配置，采用独立任务上下文。桌面 Codex 中已有的对话保持原有状态。
- 外部输出任务携带 `authorization.authorization_id`。任务能力遵循能力注册表的开放状态。
- 评论携带 `persona_snapshot` 或 `parameters.persona`；模型根据真实笔记内容生成人设一致的评论，并保存平台回读证据。
- 超时或暂停会停止本次 Codex 进程及其子进程；外部写入结果不完整时记为 `RESULT_UNKNOWN`，交由对账处理。
- 沿用员工任务队列和同任务幂等接收规则；账号调度仍由现有调度层负责。

## 执行证据

每条任务的证据位于员工状态目录下的 `agent-runs/<remote_execution_id>/`：

| 文件 | 内容 |
| --- | --- |
| `task.json` | 经理下发的任务、人设与授权 |
| `instructions.md` | 本次员工执行说明 |
| `process.json` | 执行进程编号 |
| `claimed.json` | 模型读取任务后的领取回执 |
| `events.jsonl` | Codex 执行事件和工具调用 |
| `result.json` | 结构化最终结果 |

Windows 启动回执为员工状态目录中的 `logs/desktop-<remote_execution_id>.json`，包含实际执行进程 PID。

本地连接验证能力为 `employee-agent-probe`：模型读取任务、写领取回执，并把 `parameters.probe_token` 原样返回。其结果用于验证 inbox、模型执行、outbox 和经理机采集链路。

Codex 非交互执行接口参考：[官方文档](https://learn.chatgpt.com/docs/non-interactive-mode)。

## PC057 验证结果

2026-09-08 实机任务 `pc057-agent-connection-20260908-03` 成功完成本地连接验证：接收后约 43 秒生成 Agent 领取回执，约 71 秒完成。经理机 13:57 的后台采集自动导入结果，控制台任务 API 返回 `SUCCESS`，该任务入库结果为 1 条。

相关自动测试 65 项通过，Ruff 和差异检查通过。时间仅代表本地连接验证；运营任务的 `timeout_seconds` 应包含模型处理、浏览停留和业务执行时间。

机器可读记录见 [实机验证记录](EMPLOYEE-AGENT-WAKE-VERIFICATION.json)。实机验证范围为本地文件读写与结果采集；评论业务执行另行验收。

同日完成员工桌面会话验证：Codex 通过项目 CLI 读取 tesssst 的真实 UID 并确认一致，Python SSL 加载和默认 SSL 上下文创建成功，启动链相关测试 63 项通过。详见[桌面执行验证](EMPLOYEE-AGENT-DESKTOP-VERIFICATION.json)。
