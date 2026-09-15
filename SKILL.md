---
name: xiaohongshu-skills
description: |
  小红书单账号及多账号自动化技能集合。通过本项目的 Python CLI、通用 Chrome
  扩展、Bridge 和本地 WebUI 管理账号槽位、半自动换号、内容发现、互动和内容运营。
  当用户要求配置、启动、识别、切换或操作一个或多个小红书账号时使用。
---

# 小红书自动化 Skills

根据用户意图路由到对应子技能，并始终通过本项目执行。

员工 inbox 任务使用 [固定任务入口](skills/employee-task-entry.md)。通过
`call task-entry.cmd help --command <业务命令>` 查询参数，再用原生 JSON 文件调用业务。

图片内容或页面异常可通过 `browser-snapshot` 截图，使用 Agent 的 `view_image` 识别，
再通过 `browser-action` 执行当前授权范围内的页面操作并查看操作后截图。
视觉步骤、坐标与恢复约定见 [员工任务执行入口](skills/employee-task-entry.md)。
普通异常复用绑定身份和当前步骤进度；身份异常才重新核验，未知发送结果先核对原动作。

## 规则优先级

按以下顺序解释规则：

```text
docs/PRD.md 的版本规则
→ 本文件的路由和强制边界
→ 子 SKILL.md 的执行流程
→ README.md 和 docs/USER-GUIDE.md
```

下层说明不得覆盖上层规则。

## 版本与执行状态

当前发布任务按经理目标生成内容后直接正式发布，图文和视频填写命令内完成提交，
长文在 `next-step` 完成提交，统一回读平台结果并回传。

- **V1.0**：冻结基线。发布、定时发布、私信和公开资料修改由产品入口禁用；
  指定评论和回复仍使用当前确认链；Chrome 由用户手动打开。
- **V1.5**：已批准的下一阶段规则。目标开放发布、定时发布、私信、公开资料修改、
  评论一次点击直发、规则化自动回复，以及 Bridge 与绑定 Chrome Profile 联合启动。
- **主加**：关注属于单向操作，默认无需审批。Agent 可直接执行，并在内部完成目标预览、关注和
  结果回读；只有用户特别说明需要审批时才暂停等待确认。
- **主动私信**：Codex/CLI 执行链支持首次私信和已有会话续发。用户指定账号、联系人和联系目的后，
  Codex 读取每位联系人的真实上下文，生成不同的最终文本并直接发送、回读和汇总。单批最多 10 人；
  恢复批次只重试 `FAILED` 或 `BLOCKED` 项。真实账号发送仍需明确验收目标。
- **V2.0 Codex 回复**：评论和入站私信使用 `reply-inbox`。Codex 读取笔记或会话上下文、账号人设、
  知识与规则，生成并提交 `reply` 或 `skip`；Python/Bridge 负责账号核验、发送前校验、
  页面回读和记录。API 模型 `reply-cycle` 保留为兼容运行方式，真实账号周期待验收。
- **执行门槛**：V1.5 规则不等于代码已经可用。在能力注册表、CLI/WebUI 执行链、测试和
  真实设备验收完成前，不得绕过 `CAPABILITY_DISABLED`，不得把不存在的命令描述为已执行。

## 强制边界

所有小红书操作只能通过工作区中的 `python scripts/cli.py <子命令>` 或复用同一应用服务的 WebUI
受控入口完成。

- 不使用其他小红书 MCP、Go 工具或外部自动化项目。
- 多账号命令统一使用 `python scripts/cli.py --account <账号别名> <子命令>`。
- 先确认目标槽位；不同账号可以并发，同一账号必须由 CLI 账号锁串行执行。
- 账号首次绑定时读取并确认 UID；日常业务复用槽位绑定直接执行。手动换号后重新绑定，主动检查身份时读取页面 UID。
- 所有 Profile 使用当前工作区的同一份 `extension`，每个 Profile 独立配对槽位。
- 文件路径使用绝对路径，CLI 输出以 JSON 结构化反馈。
- 完成当前任务后停止，不主动扩展成持续任务或批量操作。
- 后续新增运营能力默认只由 Agent/Python CLI 下发；WebUI 作为控制面板展示状态和结果。当前例外是
  继续既有 `preview_ready` 发布任务，以及账号回复规则配置、手动运行周期和单条草稿生成。

## 意图路由

1. 登录状态、退出、半自动换号、配对 → `xhs-auth`。
2. 图文、视频、长文、草稿、定时发布 → `xhs-publish`。
3. 首页、搜索、详情、评论读取、用户主页 → `xhs-explore`。
4. 主动私信、主加、主动评论、随机评论、点赞、收藏 → `xhs-interact`。
5. 竞品分析、热点追踪、内容创作、互动管理 → `xhs-content-ops`。
6. 公开资料修改 → 识别为 V1.5 目标能力；当前没有完整 CLI 执行链时明确报告待实现，
   不猜测命令或改用外部工具。
7. 新评论或入站私信的 Codex 回复 → `xhs-reply`；复合运营回收和运营指标快照 →
   `xhs-content-ops`。

## 账号与登录规则

V1.5 产品登录只保留半自动流程：

```text
检查登录状态
→ 自动退出当前账号
→ 用户在绑定的 Chrome Profile 中手动登录
→ 读取并核验新 UID
→ 完成换号
```

- 不从 Skill 引导二维码登录或手机验证码登录。
- 换号使用 `account-switch-begin --confirm` 和 `account-switch-complete`。
- 换号期间只允许认证、身份和换号命令，不绕过业务保护。

## V1.5 账号联合启动规则

目标流程是把“启动 Bridge”升级为“启动账号”：

```text
启动或复用 Bridge
→ 检查槽位绑定 Profile 是否已经连接
→ 未连接时自动打开 chrome_user_data_dir + chrome_profile_directory
→ 等待通用扩展连接
→ 核对 Profile 声明、扩展实例和已绑定 UID
→ READY
```

- 已连接正确 Profile 时不重复打开。
- 错误 Profile 返回 `PROFILE_MISMATCH`，不得误报 READY。
- 系统可以按上述规则打开 Chrome，但不自动关闭用户的 Chrome。
- 联合启动已完成自动化测试；真实 Windows 环境按 Profile、扩展实例和槽位绑定判断是否 READY。

## V1.5 对外操作规则

| 操作 | 授权和确认规则 |
|---|---|
| 图文、视频、长文发布 | 按任务目标生成内容、填写并正式发布，回读确认成功 |
| 定时发布 | 按任务指定账号、内容和发布时间提交，回读平台结果 |
| 保存草稿 | 用户取消发布时可以保存草稿，不得把保存草稿描述为已经发布 |
| 指定评论 | 用户点击创建当前任务后直接发送，不增加第二次逐条确认 |
| 随机评论 | 当前点击一次性授权本账号、本任务 1–3 条，每条单独记录结果 |
| 首页浏览点赞 | 当前点击授权本账号、本任务的浏览和点赞配额；只对本批已浏览笔记点赞并逐篇记录 |
| 自动回复 | 用户先按账号启用规则；符合规则的回复不逐条确认，但必须受暂停和记录约束 |
| 私信 | 当前任务指定账号、联系人和联系目的后，Codex 逐人读取上下文、生成文本、直接发送并回读 |
| 公开资料修改 | 展示修改前后差异，用户确认一次后执行 |

规则化自动回复必须由账号规则显式启用。Codex 回复通过 `reply-inbox` 执行；API 模型兼容模式通过
`reply-cycle` 执行。单条草稿入口用于生成候选内容。

## 子技能概览

| 子技能 | 职责 |
|---|---|
| `xhs-auth` | 状态检查、退出、配对、身份核验和半自动换号 |
| `xhs-publish` | 图文、视频、长文正式发布与结果回读流程 |
| `xhs-explore` | 首页、搜索、详情、评论读取和用户主页 |
| `xhs-interact` | 私信、主加、评论、随机评论、规则化回复、点赞和收藏 |
| `xhs-reply` | Codex 读取评论或私信上下文并提交回复、待处理或跳过决策 |
| `xhs-content-ops` | 组合研究、创作、发布和互动流程 |

## 数据回收命令

```powershell
# 自动发现自己最近的笔记，回收新评论并写入事件收件箱
python scripts/cli.py --account <账号别名> collect-note-comments --max-notes 20

# 只读扫描最近一对一会话，回收对方最后发送的文字
python scripts/cli.py --account <账号别名> collect-private-messages --max-conversations 20

# 由 Codex 采集、读取上下文并处理评论和私信回复
python scripts/cli.py --account <账号别名> reply-inbox collect --limit 20
python scripts/cli.py --account <账号别名> reply-inbox list --state NEW --limit 20

# 回收账号与自己笔记的运营指标时间快照
python scripts/cli.py --account <账号别名> collect-operations-metrics --max-notes 50

# 为一个已采集的评论或入站私信事件生成待人工确认草稿（不会发送）
python scripts/cli.py --account <账号别名> generate-reply-draft `
  --event-id <事件ID> --verified-uid <当前已核验UID>
```

上述采集命令只读，草稿生成只写本地状态。Codex 对每个事件运行 `reply-inbox context`，再通过绝对
路径决策文件调用 `reply-inbox apply`。Agent 生成的 `reply` 直接发送，`skip` 记录忽略原因。
私信会话统一由 Agent 持续处理，发送结果通过平台回读核对。
主加和主动私信具备独立的 Agent/CLI 页面执行器、任务记录和回读链。

需要同一账号完成“浏览首页 + 点赞”时，使用单会话复合命令：

```powershell
python scripts/cli.py --account <账号别名> home-engagement `
  --browse-count 6 --like-count 2 `
  --duration-minutes 10 --min-read-seconds 15 --max-read-seconds 45
```

两次点赞间隔为 90–180 秒，中间至少完成两篇浏览；点赞使用单次点击和平台状态回读。
回读尚未确认时返回 `result_unknown` 并保留逐项结果。不同账号可并发，同一账号由账号锁串行。
需要评论时，
Agent 从已浏览结果中选定笔记，使用 `feed_id` 和 `xsec_token` 读取标题、正文、标签及可用图片文字，
生成与笔记上下文一致的最终评论，再通过 `post-comment` 发送并回读平台结果。

## 经理机跨电脑调度

经理机使用 `python scripts/manager_cli.py` 登记和检查 Windows 执行机，通过 SSH 固定调用
`scripts/manager_worker.py`。执行机固定入口只负责任务协议、版本、暂停恢复、对账和结果回收；真实
小红书任务继续交给执行机无页面执行服务，因此不得绕过账号身份、账号锁、配额、确认和页面
回读规则。

经理机与执行机之间不得传输 Chrome Profile、Cookie 或登录数据。SSH 响应超时的在途任务进入
`RECONCILIATION_REQUIRED`，先对账再继续；`RESULT_UNKNOWN` 不得自动重发。部署、配置样例和完整
命令见 `docs/MANAGER-SSH-OPERATIONS.md`。

经理机负责调度和数据汇总，账号槽位设在员工机。用户通过经理 Agent 对话配置账号人设、运营目标
和任务，经理 Agent 保存配置、记录计划，通过 SSH 下发执行并回收结果。WebUI 展示员工机账号、
账号人设、目标进展与执行记录，账号管理支持手动编辑专属人设。员工机详情按账号展示今日目标、
实际完成情况及往日记录，并展示最近一个已规划的未来运营日。

“明日任务规划”按日期保存每个员工机账号的参与状态、浏览、点赞、评论、发布数量和说明。
经理 Agent 在安排当日任务前使用 `daily-plan list` 读取对应日期的已保存规划，按参与账号的
确切数量创建运营目标和执行计划，并沿用账号人设。保存数量规划只更新经理机规划数据，
执行仍通过运营任务领取、计划和下发流程完成。

每个“员工机编号 + 账号槽位”对应一份人设。配置前先用 `persona list` 读取现有人设，后续调整更新
同一 `persona_id` 并保留版本；已下发任务使用创建时的人设快照。人设内容以用户配置为准，
通过 UTF-8 JSON 文件调用 `persona save --input-file <绝对路径>` 保存。以下命令用于配置和复核：

```powershell
python scripts/manager_cli.py persona list
python scripts/manager_cli.py daily-plan list
python scripts/manager_cli.py daily-plan save --input-file "C:\manager-input\daily-plan.json"
python scripts/manager_cli.py persona save --input-file "C:\manager-input\account-persona.json"
python scripts/manager_cli.py operation-task list --state WAITING_AGENT
python scripts/manager_cli.py operation-task claim `
  --operation-task-id <运营任务编号> --agent-id MANAGER-AGENT-01
python scripts/manager_cli.py operation-task show --operation-task-id <运营任务编号>
python scripts/manager_cli.py operation-task plan `
  --operation-task-id <运营任务编号> `
  --input-file "C:\manager-input\manager-operation-plan.json"
python scripts/manager_cli.py schedule register `
  --input-file "C:\manager-input\manager-schedule.json"
python scripts/manager_cli.py task dispatch --input-file "C:\manager-input\task.json"
```

计划输入使用 `operation_task_id` 关联运营任务，并继承目标和账号人设快照。领取前不能生成员工机
计划或执行任务。结果回收后，按 `operation_task_id` 汇总操作记录与业务数据，同时保留
`schedule_id`、`goal_id`、目标版本、`persona_id` 和人设版本。完成对账后常驻经理 Agent 自动
结算运营任务；人工恢复时可以使用 `operation-task finish`。

功能包安装后先登记为停用状态，再通过 `manager_cli.py version set` 明确正式或试用状态以及允许的
经理、设备、账号和动作范围。带 `function_id` 的任务必须同时给出
`allowed_function_versions`；执行机接单时锁定实际版本，并在结果中分别返回功能包
`actual_version` 与项目 `project_version`。

## 新电脑准备

首次运行前执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Prepare
```

脚本优先使用项目 `.venv` 或兼容的本机 Python，并检查 CLI 与 WebUI 运行依赖。只有返回
`python_missing` 时，才能在用户同意后增加 `-InstallMissingPython`。后续命令使用返回的
`python_executable`。

## 当前可执行的账号准备

```powershell
# 创建槽位并准备配对
python scripts/cli.py account-onboard --name brand-a --confirm

# 或绑定已有 Profile
python scripts/cli.py account-discover
python scripts/cli.py account-import `
  --name brand-a `
  --user-data-dir "C:\Users\<用户>\AppData\Local\Google\Chrome\User Data" `
  --profile-directory "Profile 2"

# 检查配对与账号状态
python scripts/cli.py --account brand-a account-pair-status
python scripts/cli.py account-doctor --name brand-a --require-ready
```

## 失败处理

- **依赖不可用**：运行 `scripts/bootstrap.ps1 -Prepare`，不要改用未安装依赖的系统 Python。
- **Bridge 未启动**：运行只读诊断并按真实错误修复。
- **扩展未连接**：运行账号联合启动流程，按需打开绑定 Profile；连接后仍须核验 Profile 和扩展实例。
- **Profile 不一致**：停止业务命令，展示期望与实际 Profile，不自动改绑。
- **账号正在换号**：只执行身份或换号命令，登录新账号后运行 `account-switch-complete`。
- **UID 不一致**：停止发布和互动，先完成身份核验。
- **目标能力未实现**：明确报告 V1.5 规格已批准但执行链待实现，不调用外部工具替代。
