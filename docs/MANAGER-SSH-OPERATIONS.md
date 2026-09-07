# 经理机与 Windows 执行机 SSH 调度

本文对应 `REQ-20260824-002 v1.5` 的 `MANAGER-01` 至 `MANAGER-07`。经理机保存执行机连接清单、账号长期人设配置、运营目标、运营任务、调度状态和汇总结果；账号槽位、Chrome Profile、Cookie、Bridge 与登录状态始终留在各执行机本地。

## 组件与固定入口

| 位置 | 入口 | 用途 |
|---|---|---|
| 经理机 | `scripts/manager_cli.py` | 注册检查、任务控制、包传输、结果回收与汇总 |
| 经理机 | `scripts/manager_transport.py` | 通过 SSH 唤醒固定入口，并用 SFTP 投递任务、回收结果 |
| 执行机 | `scripts/manager_worker.py` | 从标准输入接收单个 JSON 请求，向标准输出返回单个 JSON 结果 |
| 员工机 Agent | `scripts/manager_worker_service.py` | 从 inbox 领取任务、快速 ACK、异步执行并写入 outbox |
| 执行机 | `scripts/executor_server.py` | API 执行服务与本机只读 Worker Console，承载账号身份、账号锁、配额、规则与真实页面回读链 |

经理机状态默认保存在 `%USERPROFILE%\.xhs\auto-xhs\manager`；执行机协议状态默认保存在 `%USERPROFILE%\.xhs\auto-xhs\manager-worker`。可分别用 `XHS_MANAGER_HOME` 和 `XHS_MANAGER_WORKER_HOME` 指定其他绝对目录。

## 一、准备执行机

1. 在执行机安装并启动 Windows OpenSSH Server，设置公钥登录，确认经理机可用 `ssh user@host` 登录。
2. 在执行机部署同一项目版本并创建 `.venv`。
3. 执行机无需启动完整 WebUI；API 执行服务可预先启动，也可由经理机通过 SSH 启动。
4. 在项目根目录配置固定入口。每台执行机使用独立的 `machine_id` 和本地目录：

```powershell
Get-Content -Raw -Encoding UTF8 `
  "C:\auto-xhs\xiaohongshu-skills\docs\examples\manager-worker-config.request.json" |
  C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_worker.py
```

返回 `success=true` 后，可在执行机本地做只读健康检查：

```powershell
'{"action":"health","payload":{}}' |
  C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe `
  C:\auto-xhs\xiaohongshu-skills\scripts\manager_worker.py
```

健康结果包含执行机编号、角色、项目实际版本、暂停状态、账号槽位摘要以及任务交换目录。
`task_inbox_root` 和 `task_outbox_root` 必须与经理机登记的路径一致。该检查不启动 Chrome、
不连接 Bridge，也不读取或复制浏览器登录数据。

固定入口还提供执行服务状态、启动和重启动作：

```powershell
'{"action":"executor-status","payload":{}}' | .venv\Scripts\python.exe scripts\manager_worker.py
'{"action":"start-executor","payload":{}}' | .venv\Scripts\python.exe scripts\manager_worker.py
'{"action":"restart-executor","payload":{}}' | .venv\Scripts\python.exe scripts\manager_worker.py
```

执行服务默认监听 `127.0.0.1:8766`。任务执行仍采用 API-only 模式；同一地址提供可选的本机
Worker Console，只展示本机账号摘要、执行记录、Agent 消息和 outbox 状态，不提供任务创建入口。
启动和重启完成后会立即回读健康状态；存在运行中任务时保留当前进程。经理机控制台的执行机卡片会
根据最近一次健康检查显示“启动执行服务”或“重启执行服务”，操作结束后更新 SSH、执行服务和
调度状态。旧版 `webui-status`、`start-webui`、`restart-webui` SSH 动作仍作为兼容入口映射到执行服务。

`C:\Users\EDY\Desktop\OpenSSH` 中的 Win32-OpenSSH 可作为安装与客户端程序来源。经理机可通过 `--openssh-home` 指向该根目录或其 `OpenSSH-Win64` 子目录。连接地址、Windows 用户和公钥按实际三台执行机分别配置。

## 二、注册和检查执行机

先复制并修改 `docs/examples/manager-machine.json`，再执行：

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  --openssh-home "C:\Users\EDY\Desktop\OpenSSH" `
  machine register `
  --input-file "C:\manager-input\executor-01.json"

.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  --openssh-home "C:\Users\EDY\Desktop\OpenSSH" `
  machine check --machine-id EXECUTOR-01
```

正常检查返回 `READY`；执行机自行暂停时返回 `PAUSED`；离线、地址错误、身份不符、角色错误、版本不兼容和重复编号均返回独立错误码。`identity_file` 只登记经理机本地私钥的绝对路径，不保存私钥内容。

## 三、派发、查询、暂停、恢复和对账

任务请求示例位于 `docs/examples/manager-task.json`。下发命令：

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  task dispatch --input-file "C:\manager-input\task-001.json"
```

常用控制命令：

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py --manager-home "C:\auto-xhs\manager-state" task status --task-id manager-demo-001 --refresh
.venv\Scripts\python.exe scripts\manager_cli.py --manager-home "C:\auto-xhs\manager-state" task pause --task-id manager-demo-001
.venv\Scripts\python.exe scripts\manager_cli.py --manager-home "C:\auto-xhs\manager-state" task resume --task-id manager-demo-001
.venv\Scripts\python.exe scripts\manager_cli.py --manager-home "C:\auto-xhs\manager-state" task reconcile --task-id manager-demo-001
.venv\Scripts\python.exe scripts\manager_cli.py --manager-home "C:\auto-xhs\manager-state" machine pause --machine-id EXECUTOR-01
.venv\Scripts\python.exe scripts\manager_cli.py --manager-home "C:\auto-xhs\manager-state" machine resume --machine-id EXECUTOR-01
```

每次派发按以下顺序完成：

1. 经理机把完整任务信封写入本地 `task-outbox/<machine-id>`；
2. SFTP 先上传 `.uploading` 文件，再改名为员工机 `inbox/pending/<task-id>.json`；
3. 经理机通过 SSH 发送一次 `wake-task`，SSH 请求中只携带任务编号和文件名；
4. 员工机领取文件、启动独立后台执行进程并在数秒内返回 `TASK_ACK`；
5. SSH 会话结束，浏览器业务在员工机后台继续；
6. ACK、进度事件和最终结果写入员工机 outbox，由经理机同步服务通过 SFTP 拉回入库。

`TASK_ACK` 只表示任务已领取并进入员工机执行队列，不代表业务完成。经理机状态依次为
`TRANSFERRED`、`ACKNOWLEDGED`、`RUNNING`、`COMPLETED`；最终业务状态仅来自员工机结果回读。

任务编号和派发编号在两端都幂等。`allowed_versions` 限定执行机项目版本，
`allowed_function_versions` 限定本次任务可选择的功能包版本。接单时选定的功能包版本会写入
`actual_version` 并保持到任务结算；`project_version` 单独记录项目运行版本。离线前尚未确认接收的
任务保留为 `PENDING_DISPATCH`；SFTP 原子改名结果不明时进入 `TRANSFER_UNKNOWN`，任务包已经
到达但 SSH 唤醒确认中断时进入 `WAKE_UNKNOWN`。此时先检查员工机 inbox、outbox 或执行状态，
不得直接重新派发写操作。

授权对象只保存业务授权编号、范围、规则版本等调度信息；密码、Cookie、会话令牌、访问令牌和私钥会被拒绝。真正执行由执行机无页面执行服务完成账号身份、串行锁、配额、规则和页面回读检查。

## 四、传输任务输入、功能包和配置

`transfer_type` 支持 `task_package`、`function_package` 和 `config`：

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  transfer --input-file "C:\manager-input\transfer-001.json"
```

功能包必须是目录，并具备 `manifest.json`、`README.md`、`runtime/`、`config/`、`tests/`、
历史回放样本、`CHANGELOG.md`、`ROLLBACK.md`、`RUN_RESULT.json` 和
`VERIFICATION_REPORT.md`。manifest 登记业务模块、功能编号、任务编号、名称、版本、适用层级、
执行方式、环境、依赖、调用关系、包内相对入口和回退版本。执行机按
`模块/功能编号/版本` 建立新目录；已有版本不覆盖，不兼容版本不启用。跨磁盘安装先在目标盘完成
复制，再以同盘改名落位。任务输入和配置可传单个文件，仍按传输编号建立独立版本目录。传输失败
保留失败记录和暂存材料，既有安装目录及稳定版本指针保持不变。

新安装功能包先登记为 `DISABLED`。确认固定测试、历史回放与允许范围后，再用版本策略明确设为
`FORMAL`（正式）或 `TRIAL`（试用）。请求示例位于
`docs/examples/manager-version-policy.json`：

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  version set --input-file "C:\manager-input\version-policy.json"

.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  version list --machine-id EXECUTOR-01 --function-id EXPLORE-02
```

版本策略分别列出允许的经理、设备、账号和动作；`*` 表示该维度明确允许全部。只有正式版本可以
成为稳定版本。稳定版必须先切换后才能停用；仍有在途任务的版本不能停用。试用版只供匹配范围的
新任务选择，其他新任务使用稳定版。

## 五、账号人设、运营目标和 Agent 计划

账号人设按“机器 + 账号槽位”绑定。首次保存生成第一版，后续更新同一 `persona_id` 会增加版本，
历史版本随卡片保留。WebUI 可直接维护人设卡；CLI 使用绝对路径 JSON：

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  persona save --input-file "C:\manager-input\manager-persona.json"

.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" persona list
```

样例见 `docs/examples/manager-persona.json`。每个账号同时只有一张启用中的人设卡。运营目标先保存为
`DRAFT`，启用时冻结目标版本及各目标账号当前启用的人设快照：

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  goal save --input-file "C:\manager-input\manager-goal.json"

.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" goal activate --goal-id GOAL-XXXXXXXXXX
```

样例见 `docs/examples/manager-goal.json`。保存和启用目标都不创建计划或任务。用户在 WebUI 的
“运营任务收件箱”关联一个启用目标并提交后，经理机生成 `WAITING_AGENT` 任务。WebUI 服务内的
常驻经理 Agent 会自动领取任务，根据目标指标和人设生成计划，通过 SSH 向员工机 Agent 派发，
回收结果并核销完成标准。每次任务派发、员工机确认、结果回收和最终结算都记录为 Agent 交接消息。

以下命令保留为故障恢复和人工复核入口：

```powershell
python scripts/manager_cli.py operation-task list --state WAITING_AGENT
python scripts/manager_cli.py operation-task claim `
  --operation-task-id OPERATION-SEPTEMBER-001 --agent-id MANAGER-AGENT-01
python scripts/manager_cli.py operation-task plan `
  --operation-task-id OPERATION-SEPTEMBER-001 `
  --input-file "C:\manager-input\manager-operation-plan.json"
python scripts/manager_cli.py schedule register `
  --input-file "C:\manager-input\manager-schedule.json"
```

运营任务、计划和结算输入样例分别见 `docs/examples/manager-operation-task.json`、
`docs/examples/manager-operation-plan.json`、`docs/examples/manager-schedule.json` 和
`docs/examples/manager-operation-finish.json`。未被经理 Agent 领取的运营任务不能登记员工机计划或
派发执行任务。计划输入同时携带 `operation_task_id` 和 `goal_id`；实际任务通过 `schedule_id`
继承运营任务、目标和人设快照。目标暂停后，新计划与新任务停止进入该目标。

完成结果回收与对账后，常驻经理 Agent 自动结算运营任务；也可以使用以下命令人工结算：

```powershell
python scripts/manager_cli.py operation-task finish `
  --operation-task-id OPERATION-SEPTEMBER-001 `
  --input-file "C:\manager-input\manager-operation-finish.json"
```

经理机回收结果时写入 `operation_task_id`、`schedule_id`、`goal_id`、目标版本、`persona_id` 和
人设快照，增量记录继承同一关联。运营任务页与目标页按动作汇总目标指标和回收实际值，任务、计划、
结果页面可以沿同一关联对账。

## 六、回收、汇总和恢复

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  collect --machine-id EXECUTOR-01

.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  result list --machine-id EXECUTOR-01
```

执行机在 `task_outbox_root` 下维护 `index.json`，并分别发布
`acknowledgements/`、`events/` 和 `results/` 文件。经理机 Collector 先拉取索引，再按游标顺序通过
SFTP 回收新文件。终态结果附带项目版本、任务锁定的功能包实际版本、逐项结果、错误分类、人工处理
标记、建议动作、证据索引和增量数据。经理机按执行机、远端执行编号和结果修订号幂等入库，增量
记录按来源自然键去重；证据按结果修订号分目录下载，下载失败项保留为待重试。离线时游标保持
不变，恢复后从上一个游标继续。逐项结果或增量数据没有采到时记录 `missing` 及原因；明确观察到
空列表时记录 `observed`，不把缺采写成业务零。

业务状态来自执行机本地页面回读：

| 状态 | 含义 |
|---|---|
| `SUCCESS` | 平台页面或业务结果已明确回读成功 |
| `PARTIAL_SUCCESS` | 批量任务中只有部分项目明确成功 |
| `FAILED` | 已明确失败，可按错误分类处理 |
| `BLOCKED` | 身份、版本、配额、规则、确认或运行环境阻止执行 |
| `RESULT_UNKNOWN` | 动作可能已发生，但没有可靠回读；先人工或只读对账 |
| `CANCELLED` | 任务在可确认边界被取消 |

SSH 命令退出成功只表示协议响应已返回，不改变上述业务判定。

## 七、自动化与实机验收边界

当前自动化测试覆盖唯一注册、健康检查、本地应用服务探测、离线与错误角色、任务幂等、授权信息
保留、暂停恢复、SSH 超时对账、版本化人设卡、目标启停、目标/人设/计划/结果关联、三类传输、跨磁盘安装、版本不覆盖、正式功能包结构与入口校验、
正式/试用/停用和范围策略、任务版本锁定、稳定版保留、增量收取、自然键去重、证据重试和经理端
汇总。

三台实际执行机的地址、Windows 用户、公钥、项目目录和六个账号槽位准备完成后，按以下顺序登记实机证据：三机健康检查 → 六槽位摘要 → 只读任务 → 获准写任务 → 暂停恢复 → 人为断开 SSH 后对账 → 功能包新版本与不兼容版本 → 离线恢复增量拉取。真实页面动作仍以各业务功能自身的获准验收范围为准。
