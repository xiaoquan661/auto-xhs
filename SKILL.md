---
name: xiaohongshu-skills
description: |
  小红书经理与员工协作技能入口。通过本项目经理CLI、员工固定任务入口、
  Python应用服务、Bridge和Chrome扩展规划、执行及回收多账号运营任务。
  当用户要求配置、下发、查询、发布、互动、回复、采集或处理小红书任务时使用。
---

# 小红书项目技能入口

先按工作职责读取规则，再按用户意图进入具体技能。工程与开发基准见项目根`AGENTS.md`；
经理规则见`ManagerStation/Managers/AGENTS.md`与`ManagerStation/Managers/PC023/AGENTS.md`；
员工规则见`WorkerStation/Workers/AgentWorkers/AGENTS.md`及对应PC工作区入口。
产品规则以`docs/PRD.md`及`docs/PRD-TRACEABILITY.md`为准，当前实现以获准版本和能力注册表为准。

## 入口与版本

经理实现来自`ManagerStation/Managers/PC023/function_packages/xiaohongshu-skills/0.1.0/manager/runtime/scripts/`。
员工实现来自`ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/runtime/`，
技能和扩展来自同版本包的`skills/`及`extension/`。
各角色通过项目根固定入口调用所属版本的实现。输入文件使用绝对路径，CLI返回结构化JSON。

在实际项目根解析绝对路径后执行：

```powershell
$projectRoot = (Resolve-Path .).Path
python "$projectRoot\scripts\manager_cli.py" --help
python "$projectRoot\scripts\cli.py" --help
```

员工inbox任务先读取
`ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/skills/employee-task-entry.md`，
通过`call task-entry.cmd help --command <业务命令>`查询参数，再用原生JSON文件调用业务。
图片与页面异常按该指引使用browser-snapshot、view_image和browser-action。
当前操作采用绑定身份与既有执行进度；未知发送结果先核对原动作。

项目版本来自`pyproject.toml`，业务功能版本来自对应manifest及经理显式选用记录。
安装后登记停用，再用`version set`选择正式或试用版本与允许的经理、设备、账号、动作范围。
携带function_id的任务提供allowed_function_versions；员工锁定实际版本，
回执分别返回功能actual_version与项目project_version。

## 经理意图路由

经理负责账号策略、运营计划、任务投递、结果回收与业务汇总。账号槽位位于员工机，
WebUI展示状态、目标和结果。新增运营能力通过Agent/CLI下发；既有preview_ready任务、
回复规则配置、手动周期和单条草稿使用已有受控界面入口。

| 任务 | 固定入口或资料 |
| --- | --- |
| 设备与账号配置 | manager_cli machine/account相关命令及机器配置 |
| 人设与数量规划 | persona、daily-plan |
| 运营任务领取与计划 | operation-task list/claim/show/plan |
| 定时与批次投递 | schedule register、task dispatch |
| 回执回收及业务汇总 | 经理任务结果回收入口和数据库查询 |
| 运营验收 | database schema/query、acceptance |
| 员工故障 | debug list/claim及docs/DEBUG-WORKFLOW.md |
| 安装和版本选择 | manager_cli版本命令及正式包manifest |

安排当日任务前使用daily-plan list读取对应日期规划，按参与账号的确切数量创建目标和计划。
人设调整前使用persona list读取现有记录，更新同一persona_id并保留版本；
已下发任务采用创建时快照。输入通过UTF-8 JSON文件保存。

```powershell
python "$projectRoot\scripts\manager_cli.py" persona list
python "$projectRoot\scripts\manager_cli.py" daily-plan list
python "$projectRoot\scripts\manager_cli.py" daily-plan save --input-file "C:\manager-input\daily-plan.json"
python "$projectRoot\scripts\manager_cli.py" persona save --input-file "C:\manager-input\account-persona.json"
python "$projectRoot\scripts\manager_cli.py" operation-task list --state WAITING_AGENT
python "$projectRoot\scripts\manager_cli.py" operation-task claim `
  --operation-task-id <运营任务编号> --agent-id <经理执行身份>
python "$projectRoot\scripts\manager_cli.py" operation-task show --operation-task-id <运营任务编号>
python "$projectRoot\scripts\manager_cli.py" operation-task plan `
  --operation-task-id <运营任务编号> --input-file "C:\manager-input\manager-operation-plan.json"
python "$projectRoot\scripts\manager_cli.py" schedule register --input-file "C:\manager-input\manager-schedule.json"
python "$projectRoot\scripts\manager_cli.py" task dispatch --input-file "C:\manager-input\task.json"
```

计划继承operation_task_id、目标和人设快照，领取后生成员工计划及任务。
回收后按operation_task_id汇总操作和业务数据，保留schedule_id、goal_id、目标版本、
persona_id与人设版本。常驻经理沿对账记录结算，人工恢复可使用operation-task finish。
完整配置、投递、回收及在途任务对账见`docs/MANAGER-SSH-OPERATIONS.md`。

## 员工业务技能路由

以下路径相对于项目根，具体技能位于`ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/skills/`。

| 用户意图 | 子技能 |
| --- | --- |
| 登录状态、退出、配对、身份核验与半自动换号 | xhs-auth/SKILL.md |
| 图文、视频、长文、草稿和定时发布 | xhs-publish/SKILL.md |
| 首页、搜索、详情、评论读取和用户主页 | xhs-explore/SKILL.md |
| 智能评论、主动私信、主加、点赞和收藏 | xhs-interact/SKILL.md |
| 新评论或入站私信的上下文回复 | xhs-reply/SKILL.md |
| 竞品分析、热点追踪、内容创作、复合运营和指标快照 | xhs-content-ops/SKILL.md |

主动评论由员工Agent先读取真实笔记截图或DOM，结合账号人设生成最终评论，
再用post-comment提交，按本次平台成功提示保存证据及逐项回执。
批量评论按原任务配额逐条执行同一流程。发布和私信按员工公共规则及对应技能核对原动作，
生成、提交并回读业务结果。各账号的身份、配额、暂停和回复开关沿本机配置与任务输入生效。

## 采集与回复调用

```powershell
python "$projectRoot\scripts\cli.py" --account <账号别名> collect-note-comments --max-notes 20
python "$projectRoot\scripts\cli.py" --account <账号别名> collect-private-messages --max-conversations 20
python "$projectRoot\scripts\cli.py" --account <账号别名> reply-inbox collect --limit 20
python "$projectRoot\scripts\cli.py" --account <账号别名> reply-inbox list --state NEW --limit 20
python "$projectRoot\scripts\cli.py" --account <账号别名> collect-operations-metrics --max-notes 50
python "$projectRoot\scripts\cli.py" --account <账号别名> generate-reply-draft `
  --event-id <事件ID> --verified-uid <当前已核验UID>
```

采集读取平台数据，草稿写本地候选状态。对每个事件读取reply-inbox context，
Agent生成reply或skip决策文件，以绝对路径调用reply-inbox apply。reply提交并回读，
skip记录原因；私信会话按原任务持续处理。兼容模型方式沿reply-cycle配置使用。

首页浏览和点赞使用单会话复合命令：

```powershell
python "$projectRoot\scripts\cli.py" --account <账号别名> home-engagement `
  --browse-count 6 --like-count 2 `
  --duration-minutes 10 --min-read-seconds 15 --max-read-seconds 45
```

两次点赞间隔90–180秒，中间至少浏览两篇。点赞单次点击并回读平台状态；
尚待确认的结果保留result_unknown。需要评论时从已浏览笔记读取完整上下文后智能生成。

## 新电脑与账号准备

```powershell
powershell -ExecutionPolicy Bypass -File "$projectRoot\scripts\bootstrap.ps1" -Prepare
```

准备脚本优先采用项目.venv或兼容本机Python，检查CLI及服务依赖。
返回python_missing且用户同意安装时增加-InstallMissingPython，后续使用返回的python_executable。

```powershell
python "$projectRoot\scripts\cli.py" account-onboard --name brand-a --confirm
python "$projectRoot\scripts\cli.py" account-discover
python "$projectRoot\scripts\cli.py" account-import `
  --name brand-a --user-data-dir "C:\Users\<用户>\AppData\Local\Google\Chrome\User Data" `
  --profile-directory "Profile 2"
python "$projectRoot\scripts\cli.py" --account brand-a account-pair-status
python "$projectRoot\scripts\cli.py" account-doctor --name brand-a --require-ready
```

账号准备、启动、半自动换号及故障处理采用xhs-auth技能和员工公共规则。
账号身份异常时暂停本槽位并报告；日常业务复用槽位绑定UID、Profile和原执行进度。
依赖、Bridge、扩展连接和Profile问题按实际诊断结果处理，能力开放以注册表和设备验收为准。
