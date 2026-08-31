# 经理机与 Windows 执行机 SSH 调度

本文对应 `REQ-20260824-002 v1.5` 的 `MANAGER-01` 至 `MANAGER-04`。经理机只保存执行机连接清单、调度状态和汇总结果；账号槽位、Chrome Profile、Cookie、Bridge 与登录状态始终留在各执行机本地。

## 组件与固定入口

| 位置 | 入口 | 用途 |
|---|---|---|
| 经理机 | `scripts/manager_cli.py` | 注册检查、任务控制、包传输、结果回收与汇总 |
| 经理机 | `scripts/manager_transport.py` | 通过 OpenSSH 调用固定入口，并用 SCP 上传或下载 |
| 执行机 | `scripts/manager_worker.py` | 从标准输入接收单个 JSON 请求，向标准输出返回单个 JSON 结果 |
| 执行机 | `scripts/manager_worker_service.py` | 本地版本校验、幂等状态、暂停恢复、安装、对账和增量结果 |
| 执行机 | 本地 WebUI API | 执行账号身份、账号锁、配额、规则与真实页面回读链 |

经理机状态默认保存在 `%USERPROFILE%\.xhs\auto-xhs\manager`；执行机协议状态默认保存在 `%USERPROFILE%\.xhs\auto-xhs\manager-worker`。可分别用 `XHS_MANAGER_HOME` 和 `XHS_MANAGER_WORKER_HOME` 指定其他绝对目录。

## 一、准备执行机

1. 在执行机安装并启动 Windows OpenSSH Server，设置公钥登录，确认经理机可用 `ssh user@host` 登录。
2. 在执行机部署同一项目版本并创建 `.venv`。
3. 启动执行机本地 WebUI，使 `http://127.0.0.1:8765/api/v1/session` 可读。
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

健康结果包含执行机编号、角色、项目实际版本、暂停状态和账号槽位摘要。该检查不启动 Chrome、不连接 Bridge，也不读取或复制浏览器登录数据。

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

任务编号和派发编号在两端都幂等。`allowed_versions` 限定执行机项目版本，
`allowed_function_versions` 限定本次任务可选择的功能包版本。接单时选定的功能包版本会写入
`actual_version` 并保持到任务结算；`project_version` 单独记录项目运行版本。离线前尚未确认接收的
任务保留为 `PENDING_DISPATCH`；接收或执行响应中断时进入 `DISPATCH_UNKNOWN` 或
`RECONCILIATION_REQUIRED`。此时先执行 `task reconcile`，不得直接重新派发写操作。

授权对象只保存业务授权编号、范围、规则版本等调度信息；密码、Cookie、会话令牌、访问令牌和私钥会被拒绝。真正执行仍由执行机本地 WebUI 应用服务完成账号身份、串行锁、配额、规则和页面回读检查。

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

## 五、回收、汇总和恢复

```powershell
.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  collect --machine-id EXECUTOR-01

.venv\Scripts\python.exe scripts\manager_cli.py `
  --manager-home "C:\auto-xhs\manager-state" `
  result list --machine-id EXECUTOR-01
```

执行机只发布终态结果，并附带项目版本、任务锁定的功能包实际版本、逐项结果、错误分类、人工处理
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

## 自动化与实机验收边界

当前自动化测试覆盖唯一注册、健康检查、本地应用服务探测、离线与错误角色、任务幂等、授权信息
保留、暂停恢复、SSH 超时对账、三类传输、跨磁盘安装、版本不覆盖、正式功能包结构与入口校验、
正式/试用/停用和范围策略、任务版本锁定、稳定版保留、增量收取、自然键去重、证据重试和经理端
汇总。

三台实际执行机的地址、Windows 用户、公钥、项目目录和六个账号槽位准备完成后，按以下顺序登记实机证据：三机健康检查 → 六槽位摘要 → 只读任务 → 获准写任务 → 暂停恢复 → 人为断开 SSH 后对账 → 功能包新版本与不兼容版本 → 离线恢复增量拉取。真实页面动作仍以各业务功能自身的获准验收范围为准。
