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
| DATA-001 | 数据采集产品、日报和周报 | DEFERRED | 后续数据阶段验收 |
| UX-001 | 普通流程无需 PowerShell、Python 路径或端口知识 | AUTOMATED_TESTED | 双击入口、WebUI 设置、1366×768 Chrome 视觉检查和用户手册已完成；真实用户体验待验收 |
| TEST-001 | 另一台 Windows 电脑完成 WebUI 配置和 Agent 定时任务 | PLANNED | 签署跨电脑验收报告 |

## V1.5：2026-08-18 已批准目标

| ID | 需求 | 当前状态 | 完成条件 |
|---|---|---|---|
| V15-VER-001 | V1.0 保留为稳定基线，新增 V1.5 规则和实现状态 | IMPLEMENTED | PRD、Skill、README 和指南已区分规格、自动化测试和实机验收 |

