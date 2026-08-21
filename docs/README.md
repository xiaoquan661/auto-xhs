# 项目文档索引

最近审计：2026-08-21

本目录同时包含现行产品合同、操作手册、专题设计和历史实施材料。阅读发生冲突时，按以下顺序判断：

1. [PRD](PRD.md) 与 [需求追踪矩阵](PRD-TRACEABILITY.md)；
2. 仓库根目录 `SKILL.md` 与 `skills/*/SKILL.md` 的执行边界；
3. [用户指南](USER-GUIDE.md)、[故障恢复](TROUBLESHOOTING.md) 与根目录 `README.md`；
4. 带日期的报告、调研和历史实施计划。

## 现行文档

| 文档 | 用途 | 当前状态 |
|---|---|---|
| [PRD](PRD.md) | V1.0 基线与 V1.5 产品合同 | 现行；V2.0 另有专题文档 |
| [需求追踪矩阵](PRD-TRACEABILITY.md) | 区分规格、实现、自动化测试和实机验收 | 现行 |
| [用户指南](USER-GUIDE.md) | 安装、账号、WebUI、任务和确认流程 | 现行 |
| [故障恢复](TROUBLESHOOTING.md) | 常见启动、账号、任务、发布和模型问题 | 现行 |
| [闭环与数据回收架构](AUTO-XHS-CLOSED-LOOP-ARCHITECTURE.md) | 私信、主加、被动回复、指标和 WebUI 闭环 | 已批准，进度更新至 2026-08-21 |
| [V2.0 智能回复](AUTO-XHS-V2-INTELLIGENT-REPLY.md) | 评论上下文、模型配置、AI 草稿和验收边界 | 第一阶段已自动化测试，实机待验收 |
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

- 统一 V1.5 分步发布、联合启动、私信和 V2.0 智能回复的状态口径；
- 更新 WebUI 为六区本地工作台，不再描述为只读骨架；
- 补齐智能回复、发布确认、评论采集和指标链的文档入口；
- 修复跨电脑验收的本地失效链接；
- 以当前工作树 `287 passed` 作为本次自动化回归证据；真实账号状态未在本次文档审计中重新验证。
