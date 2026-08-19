# auto-xhs V1.0 / V1.5 需求追踪矩阵

关联文档：[自动多账号小红书运营系统 PRD](PRD.md)

状态定义：`SPEC_APPROVED`、`PLANNED`、`IN_PROGRESS`、`IMPLEMENTED`、`AUTOMATED_TESTED`、
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
| PERM-002 | L3 必须显式确认，不能由 UI 或 Agent 降级 | AUTOMATED_TESTED | 账号、配对、身份、自启动和设置服务/API 均校验确认 |
| OUT-001 | 图文、视频、长文发布在 V1.0 禁用 | AUTOMATED_TESTED | 服务、任务和草稿入口均拒绝，WebUI 无发布入口 |
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
| DATA-001 | 数据采集产品、日报和周报 | DEFERRED | 后续数据阶段验收 |
| UX-001 | 普通流程无需 PowerShell、Python 路径或端口知识 | AUTOMATED_TESTED | 双击入口、WebUI 设置、1366×768 Chrome 视觉检查和用户手册已完成；真实用户体验待验收 |
| TEST-001 | 另一台 Windows 电脑完成 WebUI 配置和 Agent 定时任务 | PLANNED | 签署跨电脑验收报告 |

## V1.5：2026-08-18 已批准目标

| ID | 需求 | 当前状态 | 完成条件 |
|---|---|---|---|
| V15-VER-001 | V1.0 保留为稳定基线，新增 V1.5 规则和实现状态 | SPEC_APPROVED | PRD、Skill、README 和指南统一版本语义 |
| V15-PUB-001 | 开放图文、视频和长文发布，必须填写、浏览器预览、用户确认后发布 | AUTOMATED_TESTED | 一步直发已禁用；CLI 状态机、确认门槛和结果回读测试通过，真实账号待验收 |
| V15-PUB-002 | 开放保存草稿和定时发布，定时任务确认内容、账号和时间 | IMPLEMENTED | 草稿终态、预览中的定时时间和结果未知恢复已实现，真实定时发布待验收 |
| V15-PUB-003 | 发布任务只由 Agent/Python CLI 创建和执行，WebUI 只读监测 | AUTOMATED_TESTED | CLI 写入统一任务库；WebUI 持续轮询且不为发布任务提供执行、取消或重试按钮 |
| V15-MSG-001 | 首次私信和已有会话续发；明确最终文本直发，Agent 生成文本整批确认；每人个性化，单批最多 10 人 | AUTOMATED_TESTED | Agent/CLI、身份检查、逐人回读、独立失败和结果记录已测试；真实发送待验收 |
| V15-MSG-002 | 私信任务只由 Agent/Python CLI 下发，WebUI 只读展示整批与逐人结果 | AUTOMATED_TESTED | 通用 WebUI/API 创建、执行和重试入口拒绝私信；控制面板无私信表单或动作按钮 |
| V15-FOLLOW-001 | 关注属于单向操作，Agent/CLI 默认无需审批，内部预览、执行一次并回读 | REAL_DEVICE_VERIFIED | 页面执行器、身份检查、任务记录和结果回读已测试；测试账号两个随机目标均回读“已关注” |
| V15-FOLLOW-002 | 主加任务不由 WebUI 下发，WebUI 只作为控制面板展示状态与结果 | AUTOMATED_TESTED | 通用 WebUI/API 创建入口拒绝主加能力；Agent/CLI 使用内部统一任务服务 |
| V15-PROFILE-001 | 开放公开资料修改，执行前展示前后差异并确认 | SPEC_APPROVED | 新增预览、确认、执行、回读和审计链 |
| V15-AUTH-001 | 产品登录只保留状态检查、自动退出、用户手动登录和新 UID 核验 | SPEC_APPROVED | Skill、WebUI 和 CLI 主流程不再引导二维码或手机验证码登录 |
| V15-CHROME-001 | 启动账号时先启动 Bridge，扩展未连接再自动打开绑定 Profile | SPEC_APPROVED | 正确 Profile 复用、错误 Profile 拒绝、超时和实机测试通过 |
| V15-CHROME-002 | Windows 登录自启动按槽位恢复 Bridge 和绑定 Profile，不自动关闭 Chrome | SPEC_APPROVED | 仅启用槽位启动，重复启动和真实 Windows 登录验收通过 |
| V15-COMMENT-001 | 指定评论和随机评论均由当前任务点击授权后直接发送 | SPEC_APPROVED | 授权范围、配额、逐项结果和防重复测试通过 |
| V15-REPLY-001 | 按账号启用自动回复规则，符合规则的回复不逐条确认 | SPEC_APPROVED | 规则、时间范围、配额、暂停、审计和真实账号验收通过 |
| V15-OS-001 | 正式支持 Windows 10/11，不声明 macOS/Linux 正式支持 | SPEC_APPROVED | 子 Skill 元数据和启动说明统一，完成两台 Windows 验收 |
| V15-DOC-001 | 文档优先级固定为 PRD → 主 Skill → 子 Skill → README/用户指南 | SPEC_APPROVED | 冲突扫描无下层覆盖上层规则 |

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
