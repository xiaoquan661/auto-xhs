# auto-xhs V1.0 / V1.5 / V2.0 需求追踪矩阵

关联文档：[自动多账号小红书运营系统 PRD](PRD.md)、[V2.0 智能回复](AUTO-XHS-V2-INTELLIGENT-REPLY.md)

状态定义：`SPEC_APPROVED`、`PLANNED`、`IN_PROGRESS`、`IMPLEMENTED`、`AUTOMATED_TESTED`、
`AUTOMATED_TESTED_PARTIAL`、`REAL_READ_ONLY_TESTED`、`REAL_MODEL_TESTED`、
`REAL_DEVICE_VERIFIED`、`BLOCKED`、`DEFERRED`。

`SPEC_APPROVED` 只表示产品规则已经批准，不表示代码可用。V1.5 需求必须依次经过实现、自动化测试和
真实设备验收，才能在 README 或用户指南中标记为当前可用。

## V1.0：2026-08-13 冻结基线

- 用户通过 WebUI 自主添加账号和选择 Chrome Profile；
- 所有账号采用用户维护的热登录，Chrome 页面由用户手动打开并保持在线；
- Codex 自带任务暂时承担定时触发，允许无人值守自动点赞；
- 指定评论和回复仍需最终草稿确认；随机评论由 WebUI 当前点击一次性授权 1–3 条直接发送；私信进入后续版本；
- 数据采集产品、复杂 SQLite 任务系统、运营报告和 WebUI 自建调度暂缓；
- 成功标准是在另一台 Windows 电脑通过 WebUI 完成配置，再由 Agent 执行定时任务。

| ID | 需求 | 当前状态 | 第一版验收证据 |
|---|---|---|---|
| SYS-001 | Windows 一键启动并打开本地 WebUI | AUTOMATED_TESTED | 双击入口、一实例和健康检查已测试；新设备待实机验收 |
| ENV-001 | 优先复用项目/本机 Python，缺失时经确认用 uv 准备运行时 | AUTOMATED_TESTED | 环境分支测试与新设备验收 |
| WEB-001 | WebUI/API 只监听回环地址 | AUTOMATED_TESTED | 非回环绑定拒绝、本地会话保护与 HTTP 测试 |
| WEB-002 | 用户可在 WebUI 自主添加账号、选择 Profile、配对和核验身份 | AUTOMATED_TESTED | 模拟新建/导入、配对和 UID 核验已测试；真实 Profile 待验收 |
| WEB-003 | WebUI、CLI 和 Agent 共用应用服务层 | AUTOMATED_TESTED | 统一账号、Bridge、任务、草稿、确认、暂停和能力策略服务已测试 |
| WEB-004 | WebUI 可编辑评论和回复草稿 | AUTOMATED_TESTED | 草稿保存、修改、持久化和页面入口已测试 |
| WEB-005 | WebUI 对最终外发文本执行不可混淆确认 | AUTOMATED_TESTED | 修改草稿失效、账号/UID/目标匹配和一次性消费已测试 |
| ACC-001 | 支持至少 5 个热登录账号槽位 | IMPLEMENTED | 多槽位配置与真实运行验收 |
| ACC-002 | WebUI 新建独立 Profile 或绑定已有 Profile | AUTOMATED_TESTED | 两种服务与页面入口已测试，实机待验收 |
| ACC-003 | 不复制 Cookie/Profile，删除默认保留浏览器数据 | IMPLEMENTED | 数据保护回归测试 |
| ACC-004 | 账号注册全局锁、原子写入和失败回滚 | AUTOMATED_TESTED | 跨线程/进程锁、并发端口分配和故障回滚已测试 |
| PROF-001 | 只连接用户已打开的槽位指定 Chrome Profile | AUTOMATED_TESTED | 热登录连接、离线 BLOCKED 和人工恢复测试 |
| PROF-002 | Profile 声明和已配对扩展实例必须与槽位一致 | AUTOMATED_TESTED | 错声明、错实例拒绝和诊断测试 |
| PROF-003 | Profile、配对实例和 UID 一致后才进入业务 READY | AUTOMATED_TESTED | 匹配 UID 进入 READY、身份漂移拒绝已测试；真实 UID 待验收 |
| PROF-004 | V1.0 系统不得自动启动、重启或关闭 Chrome | AUTOMATED_TESTED | 执行路径回归测试；真实浏览器观察待验收 |
| PAIR-001 | 所有 Profile 共用同一扩展代码目录 | AUTOMATED_TESTED | 双 Profile 独立实例验收 |
| PAIR-002 | 配对请求短时、单次、不可跨账号复用 | AUTOMATED_TESTED | 过期、重放和错槽位测试 |
| PAIR-003 | 撤销配对轮换凭据，共享扩展不含账号密钥 | AUTOMATED_TESTED | 配置扫描与轮换测试 |
| BRG-001 | 每槽位独立端口并校验 CLI/Bridge/扩展身份 | AUTOMATED_TESTED | 双 Bridge 错路由拒绝测试 |
| BRG-002 | Bridge 脱离临时父会话运行 | AUTOMATED_TESTED | 关闭父会话后状态可读 |
| BRG-003 | Bridge PID、stop/restart、健康守护和有限恢复 | AUTOMATED_TESTED | PID 登记、重复启动、异常 PID 和仅登记进程停止已测试 |
| ID-001 | 读取、记录并强制校验当前小红书 UID | AUTOMATED_TESTED | 真实账号身份验收 |
| ID-002 | 安全换号状态机暂停业务并保留历史 | AUTOMATED_TESTED | 换号成功、取消和中断测试 |
| PERM-001 | 所有能力登记固定 L0-L3 权限元数据 | AUTOMATED_TESTED | 公开 CLI 命令元数据覆盖，复合命令操作级策略已测试 |
| PERM-002 | L3 必须显式确认，不能由 UI 或 Agent 降级 | AUTOMATED_TESTED | 账号、配对、身份和设置服务/API 均校验确认 |
| OUT-001 | 图文、视频、长文发布在 V1.0 禁用 | AUTOMATED_TESTED | V1.0 一步发布入口仍拒绝；V1.5 仅开放分步预览与受控确认 |
| OUT-002 | 指定评论和回复使用最终草稿确认；随机评论使用当前点击的一次性批量授权 | AUTOMATED_TESTED | 草稿确认、随机目标、直接发送授权、1–3 条限制和逐项结果已测试 |
| OUT-003 | 私信和公开资料修改在 V1.0 禁用 | AUTOMATED_TESTED | 未注册能力从服务、任务和草稿入口被拒绝 |
| TASK-001 | 任务具有统一终态和最小执行记录 | AUTOMATED_TESTED | 任务状态、业务执行记录和服务中断恢复已测试 |
| TASK-002 | 同账号串行、跨账号最多 3 个业务任务并行 | AUTOMATED_TESTED | 同账号串行与跨账号并行测试通过 |
| APPROVAL-001 | 草稿修改后旧确认失效，确认短时且只能消费一次 | AUTOMATED_TESTED | 草稿版本、过期、上下文匹配和重复消费已测试 |
| CONTROL-001 | 全局暂停阻止新业务任务开始 | AUTOMATED_TESTED | 创建与执行入口均检查全局暂停 |
| SCHED-001 | Codex 定时任务可在热登录账号 READY 时自动点赞 | IMPLEMENTED | 稳定任务服务入口已实现；目标电脑定时点赞待实机验收 |
| SAFE-001 | L1 配额、去重、间隔、熔断和结果回读 | AUTOMATED_TESTED | 配额、去重、熔断、账号锁和结果未知场景已测试 |
| AUD-001 | 保存最小确认和执行结果且不含秘密 | AUTOMATED_TESTED | 最小记录和诊断脱敏故障注入测试通过 |
| DB-001 | 完整 SQLite 任务、事件和迁移体系 | DEFERRED | 后续数据阶段验收 |
| DATA-001 | 数据采集产品、日报和周报 | AUTOMATED_TESTED | 指标时间序列、相邻有效增量、日报、周报、单篇复盘及修正版保留测试通过 |
| UX-001 | 普通流程无需 PowerShell、Python 路径或端口知识 | AUTOMATED_TESTED | 双击入口、WebUI 设置、1366×768 Chrome 视觉检查和用户手册已完成；真实用户体验待验收 |
| TEST-001 | 另一台 Windows 电脑完成 WebUI 配置和 Agent 定时任务 | PLANNED | 签署跨电脑验收报告 |

## V1.5：2026-08-18 已批准目标

| ID | 需求 | 当前状态 | 完成条件 |
|---|---|---|---|
| V15-VER-001 | V1.0 保留为稳定基线，新增 V1.5 规则和实现状态 | IMPLEMENTED | PRD、Skill、README 和指南已区分规格、自动化测试和实机验收 |
| MANAGER-01 / REQ-20260824-002 | 注册并通过 SSH 固定入口检查 Windows 执行机 | AUTOMATED_TESTED | 唯一编号、连接配置、角色、项目实际版本、健康状态和账号槽位摘要已测试；三台实际执行机待联网验收 |
| MANAGER-02 / REQ-20260824-002 | SSH 派发、查询、暂停、恢复、离线保留和恢复对账 | AUTOMATED_TESTED | 经理端与执行端幂等状态机、本地应用服务执行链、超时未知及先对账规则已测试；多执行机在途任务待实机验收 |
| MANAGER-03 / REQ-20260824-002 | 通过 SCP 传输任务输入、功能包和配置 | AUTOMATED_TESTED | 三类传输、正式包完整性、跨磁盘安装、模块/功能/版本目录、正式/试用/停用与作用域、任务版本锁定、只新增不覆盖及稳定版保留已测试；执行机安装待实机验收 |
| MANAGER-04 / REQ-20260824-002 | 增量回收结构化结果、证据和数据并汇总 | AUTOMATED_TESTED | 业务终态、逐项结果、错误分类、人工处理、实际版本、缺采/观察空值、增量自然键去重、游标续拉、证据重试和修订目录已测试；跨机断线恢复待实机验收 |
| MANAGER-05 / REQ-20260824-002 | 按账号维护版本化人设卡并在 WebUI 设定运营目标 | AUTOMATED_TESTED | 单账号启用人设、历史版本、目标草稿/启用/暂停/完成、目标指标汇总和 API/CLI/WebUI 路由已测试；目标保存与启用均不创建计划或任务 |
| MANAGER-06 / REQ-20260824-002 | WebUI 创建运营任务，经理 Agent 领取、规划并下发 | AUTOMATED_TESTED | 常驻经理 Agent 自动处理 `WAITING_AGENT` 收件箱，生成浏览/点赞/评论计划并通过 SSH 派发；API/CLI 保留为恢复入口，WebUI 展示经理与员工 Agent 交接记录 |
| MANAGER-07 / REQ-20260824-002 | 运营任务贯穿 SSH 员工机执行与数据回收 | AUTOMATED_TESTED | `operation_task_id` 随任务进入员工机本地服务；常驻经理 Agent 自动回收并按目标指标核销，结果与增量记录保留任务、目标和人设关联；多机持续周期待实机验收 |
| V15-PUB-001 | 开放图文、视频和长文发布，必须填写、浏览器预览、用户确认后发布 | AUTOMATED_TESTED | CLI 状态机、素材校验、预览版本、一次性确认和结果回读测试通过，真实账号待验收 |
| V15-PUB-002 | 开放保存草稿和定时发布，定时任务确认内容、账号和时间 | AUTOMATED_TESTED | 草稿正向证据、预览中的定时时间和结果未知终态已测试，真实定时发布待验收 |
| V15-PUB-003 | 发布预览由 Agent/Python CLI 创建，WebUI 可对 `preview_ready` 单任务受控确认或保存草稿 | AUTOMATED_TESTED | WebUI 持续轮询；确认弹窗核对账号、UID、内容、素材和时间，后端拒绝账号、UID、阶段或预览版本不匹配 |
| MESSAGE-01 / REQ-20260824-002 | 用户指定账号、联系人和联系目的；Codex 区分首次/续发并结合上下文生成个性化文本，直接发送、回读和汇总 | AUTOMATED_TESTED | 1–10 人逐条执行、真实会话类型校验、独立失败、三态结果和恢复跳过规则已测试；首次联系实机待验收 |
| MESSAGE-02 / REQ-20260824-002 | 只读扫描一对一私信会话和自己笔记评论线程，将收到的新私信与评论保存为去重入站事件 | REAL_DEVICE_VERIFIED | 评论和私信按渠道及平台标识分别去重；部分页面失败保留成功采集结果，读取失败不创建事件，采集链不发送回复；真实账号两类收件箱已完成只读采集，私信完成重复扫描去重验证 |
| V15-MSG-002 | 主动私信批次只由 Codex/Python CLI 下发，WebUI 只读展示整批与逐人结果 | AUTOMATED_TESTED | 通用 WebUI/API 不创建主动私信批次；CLI 可恢复批次并只重试 `FAILED` 或 `BLOCKED` 项 |
| V15-FOLLOW-001 | 关注属于单向操作，Agent/CLI 默认无需审批，内部预览、执行一次并回读 | REAL_DEVICE_VERIFIED | 页面执行器、身份检查、任务记录和结果回读已测试；测试账号两个随机目标均回读“已关注” |
| V15-FOLLOW-002 | 主加任务不由 WebUI 下发，WebUI 只作为控制面板展示状态与结果 | AUTOMATED_TESTED | 通用 WebUI/API 创建入口拒绝主加能力；Agent/CLI 使用内部统一任务服务 |
| V15-PROFILE-001 | 开放公开资料修改，执行前展示前后差异并确认 | SPEC_APPROVED | 新增预览、确认、执行、回读和审计链 |
| V15-AUTH-001 | 产品登录只保留状态检查、自动退出、用户手动登录和新 UID 核验 | AUTOMATED_TESTED | Skill、WebUI 和 CLI 主流程不再引导二维码或手机验证码登录；真实换号继续验收 |
| V15-CHROME-001 | 启动账号时先启动 Bridge，扩展未连接再自动打开绑定 Profile | AUTOMATED_TESTED | 正确 Profile 复用、错误 Profile 拒绝和超时已测试；真实 Windows 待验收 |
| V15-COMMENT-001 | 指定评论和随机评论均由当前任务点击授权后直接发送 | SPEC_APPROVED | 授权范围、配额、逐项结果和防重复测试通过 |
| V15-REPLY-001 | 按账号启用自动回复规则，符合规则的回复不逐条确认 | AUTOMATED_TESTED | 评论/私信开关、时段、配额、暂停、决策、回读与记录已测试；真实账号周期待验收 |
| V15-OS-001 | 正式支持 Windows 10/11，不声明 macOS/Linux 正式支持 | SPEC_APPROVED | 子 Skill 元数据和启动说明统一，完成两台 Windows 验收 |
| V15-DOC-001 | 文档优先级固定为 PRD → 主 Skill → 子 Skill → README/用户指南 | IMPLEMENTED | 文档索引、状态口径和本地链接检查已更新 |

### 发布功能包交付映射

| 功能编号 | 任务编号 | 功能范围 | 当前状态 |
|---|---|---|---|
| PUBLISH-01 | REQ-20260824-002 / CHG-6-16 | 准备图文、视频和长文真实预览，在 WebUI 逐任务核对账号、UID、内容、素材和时间 | AUTOMATED_TESTED |
| PUBLISH-02 | REQ-20260824-002 / PLAN-2-27 | 依有效确认发布或保存平台草稿，并按页面证据结算任务终态 | AUTOMATED_TESTED |

## V2.0：2026-08-21 评论与入站私信智能回复

| ID | 需求 | 当前状态 | 完成条件 |
|---|---|---|---|
| V20-CTX-001 | 评论事件保留笔记、父评论和评论者上下文 | AUTOMATED_TESTED | 采集、持久化和去重测试通过；真实评论待验收 |
| V20-LLM-001 | 使用 OpenAI-compatible 模型生成结构化回复候选 | REAL_MODEL_TESTED | 配置、请求、解析和错误分支已测试；DeepSeek 已为真实入站私信事件生成草稿 |
| V20-DM-001 | 读取一对一私信收件箱，以最新连续对方消息作为回复单位并去重 | REAL_READ_ONLY_TESTED | 真实 `/chat` 页面采集基线已记录；连续消息合并、平台提示和己方最后发言过滤已测试 |
| V20-DM-002 | 私信上下文生成规则候选，发送前校验会话版本并在发送后回读 | AUTOMATED_TESTED | 会话变化停止发送、消息气泡回读及 `RESULT_UNKNOWN` 不重发已测试；自动回复实机待验收 |
| V20-DM-003 | 按私信会话维护 Codex/人工所有权和恢复边界 | AUTOMATED_TESTED | `pending` 和发送未闭环转人工、全会话快照、`HUMAN_REPLY/CODEX_OUTBOUND` 归因、延迟回读补正及显式恢复已测试；真实账号待验收 |
| V20-DRAFT-001 | AI 结果只创建待人工确认草稿，不自动发送 | AUTOMATED_TESTED | 同事件唯一任务/草稿、质量提示和既有确认链测试通过 |
| V20-WEB-001 | WebUI 可选择 READY 账号采集评论或入站私信，并为明确事件生成草稿 | AUTOMATED_TESTED_PARTIAL | 账号状态、事件列表、模型设置和生成接口测试通过；私信只读采集已有真实页面记录，确认发送待验收 |
| V20-AUTO-001 | 按账号规则运行有界自动回复周期 | AUTOMATED_TESTED | CLI/API/WebUI、渠道开关、范围、时段、配额、`reply/pending/skip`、任务与回复记录已测试；计划调度与真实账号待验收 |
| V20-CODEX-001 | Codex 读取入站上下文并提交结构化回复决策 | AUTOMATED_TESTED | `reply-inbox` 采集、列表、上下文、决策文件、规则复核、发送回读和专用 Skill 已测试；计划调度与真实账号待验收 |

## V1.0 阶段顺序

| 阶段 | 需求范围 | 完成条件 |
|---|---|---|
| 0 | 新 PRD 与追踪基线 | 热登录、互动确认和 Codex 调度边界冻结 |
| 1 | PROF-001/002/003/004、BRG-001 | 热登录连接可验证，两个真实 Profile 不串号且 Chrome 不被系统启停 |
| 2 | SYS-001、WEB-001/002/003、ACC-002 | 不用命令行完成环境和账号配置 |
| 3 | ACC-004、BRG-003、ID-001/002 | Bridge 生命周期、事务和身份恢复稳定 |
| 4 | WEB-004/005、OUT-001/002/003、SCHED-001、SAFE-001、TASK-001/002、APPROVAL-001、CONTROL-001 | 定时点赞和确认后互动闭环可用 |
| 5 | AUD-001、UX-001、TEST-001 | 另一台电脑完成最终验收 |

任何涉及 Chrome、扩展、Windows 或真实登录身份的需求，在取得真实设备证据前不得从
`AUTOMATED_TESTED` 直接视为发布完成。
