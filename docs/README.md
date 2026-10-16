# 项目文档索引

- [账号记忆与人格](ACCOUNT-MEMORY-PERSONA.md)：分层存储、经验审核、任务与生成召回、版本更新和采用追溯。

最近审计：2026-08-21

本目录同时包含现行产品合同、操作手册、专题设计和历史实施材料。阅读发生冲突时，按以下顺序判断：

1. [PRD](PRD.md) 与 [需求追踪矩阵](PRD-TRACEABILITY.md)；
2. 仓库根目录 `SKILL.md` 与 `skills/*/SKILL.md` 的执行边界；
3. [用户指南](USER-GUIDE.md)、[故障恢复](TROUBLESHOOTING.md) 与根目录 `README.md`；
4. 带日期的报告、调研和历史实施计划。

## 现行文档

[项目记忆库](../project-memory/README.md)提供 Agent 与 Obsidian 共用的项目背景、架构、
当前状态、决策和工作交接入口。

| 文档 | 用途 | 当前状态 |
|---|---|---|
| [架构与使用手册](项目框架结构.html) | 角色路线、成功与异常任务样例、排查索引；全流程详图支持全屏、缩放、搜索和返回定位；通信与数据流程图标明通道、周期、回执及本地留存，功能包位置在节点 FAQ 中查询，配套代码功能策略表与 FAQ | 项目 0.1.0 / V0.20 布局；2026-10-16 核对；单文件浏览器打开 |
| [新手项目说明](BEGINNER-PROJECT-GUIDE.md) | 从产品定位、架构、上手、日常使用到开发维护的完整导读 | 现行；建议第一次接触项目时先读 |
| [PRD](PRD.md) | V1.0 基线与 V1.5 产品合同 | 现行；V2.0 另有专题文档 |
| [需求追踪矩阵](PRD-TRACEABILITY.md) | 区分规格、实现、自动化测试和实机验收 | 现行 |
| [经理机 SSH 调度](MANAGER-SSH-OPERATIONS.md) | MANAGER-01 至 MANAGER-04 的三机部署、调度、传输、对账和结果回收 | 自动化测试通过；三机实机待验收 |
| [第二套经理机与员工机部署](SECOND-SYSTEM-DEPLOYMENT.md) | 从 GitHub 安装独立经理机、接入新员工机并完成 SSH、账号和回执闭环验收 | 现行部署手册 |
| [GitHub 双人协作开发](GITHUB-COLLABORATION-GUIDE.md) | 邀请后的任务分配、分支开发、测试、PR 审核与部署交接 | 现行协作手册 |
| [用户指南](USER-GUIDE.md) | 安装、账号、WebUI、任务和确认流程 | 现行 |
| [故障恢复](TROUBLESHOOTING.md) | 常见启动、账号、任务、发布和模型问题 | 现行 |
| [闭环与数据回收架构](AUTO-XHS-CLOSED-LOOP-ARCHITECTURE.md) | 私信、主加、被动回复、指标和 WebUI 闭环 | 已批准，进度更新至 2026-08-21 |
| [V2.0 Codex 回复](AUTO-XHS-V2-INTELLIGENT-REPLY.md) | 评论与入站私信上下文、Codex 决策、规则复核和回读边界 | `reply-inbox` 已完成自动化测试；真实账号待验收 |
| [跨电脑验收](OTHER-COMPUTER-ACCEPTANCE-TEST.md) | 在另一台 Windows 电脑验证安装、Profile 和只读链 | 现行验收模板 |

## 专题与历史材料

- [V1 优化与开发执行总计划](AUTO-XHS-V1-OPTIMIZATION-IMPLEMENTATION-PLAN.md)：历史执行计划，阶段状态以追踪矩阵为准；
- [多账号改造对比报告](XIAOHONGSHU-MULTI-ACCOUNT-REFACTOR-REPORT-2026-08-03.md)：带日期的架构快照；
- [任务板块产品基线 1.0](任务板块产品基线-1.0.md)：历史产品基线；
- [Codex 模拟操作清单](小红书Codex模拟操作清单.md)：模拟与验收参考，不代表真实账号结果；
- [小红书与 RedNote 网页端兼容性调研](小红书与RedNote网页端兼容性调研报告.md)：工程调研，不能替代 RedNote 实机验收；
- `superpowers/`：早期 NetLogger 设计与实施记录，不覆盖现行 PRD 和 Skill 边界。

## 维护口径

- “代码存在”“自动化测试通过”“真实设备验证”分别记录，不能互相替代；
- CLI 命令以 `python scripts/cli.py --help` 为准；产品是否开放仍以 PRD、能力注册表和 Skill 为准；
- 对外操作必须保留账号/UID 核验、预览或确认要求以及平台回读；没有可靠回读时使用 `RESULT_UNKNOWN`；
- 历史文档保留当时结论，不为追求表面一致而改写历史；在现行索引中标明其时间和用途即可。

## 本次审计结论

2026-08-21 已完成以下修订：

- 统一 V1.5 分步发布、联合启动、主动私信和 V2.0 评论/入站私信智能回复的状态口径；
- 更新 WebUI 为六区本地工作台，不再描述为只读骨架；
- 补齐智能回复、发布确认、评论采集和指标链的文档入口；
- 修复跨电脑验收的本地失效链接；
- 补齐入站私信收件箱、独立提示词、AI 草稿、逐条确认和发送回读的文档入口；
- 以当前工作树 `294 passed` 作为本次自动化回归证据；本次文档审计没有重新执行真实账号读写，实机状态沿用已有记录并单独标注。
