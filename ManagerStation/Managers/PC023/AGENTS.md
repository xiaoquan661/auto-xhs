# PC023 小红书经理工作区

本工作区使用 `workspace_id=PC023`，经理业务配置沿用 `manager_id=MILA-MANAGER`。
本文件索引项目职责与实际路径，共同规则见 `../AGENTS.md`，工程基准见项目根 `AGENTS.md`。

## 本经理职责

管理 PC052、PC057、PC065、PC071、PC073 的小红书账号策略、运营计划、素材选择、任务投递、
回执回收、业务汇总、员工故障及功能版本。账号内容人设通过经理业务配置和任务快照管理。

任务规划、查询、下发和验收按项目根 `SKILL.md` 的经理路由执行。
身份和路径见同层 `manager_manifest.json`，实现与交付关系沿功能包 manifest 引用。

## 项目路径索引

- 经理权威实现：`ManagerStation/Managers/PC023/function_packages/xiaohongshu-skills/0.1.0/manager/runtime/scripts/`。
- 员工源实现：`ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/runtime/scripts/`。
- 员工技能：`ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/skills/<skill_id>/SKILL.md`。
- 浏览器扩展：`ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/extension/`。
- 固定入口：项目根 `scripts/manager_cli.py`、`scripts/cli.py`。
- 源码、产品文档、完整测试与交接：项目根 Git、`docs/`、`tests/`、`project-memory/`。
- 正式业务功能版本：项目外 `../功能包/`；本工作区记录对应来源和配套版本。
- 经理状态：`C:\auto-xhs\manager-state`；素材：`C:\小红书Mila文件存档\素材`。

以上源码索引相对于项目根。实际运行先解析项目根，程序及输入均使用绝对路径：

```powershell
$projectRoot = (Resolve-Path .).Path
python "$projectRoot\scripts\manager_cli.py" database schema
python "$projectRoot\scripts\manager_cli.py" persona list
python "$projectRoot\scripts\manager_cli.py" daily-plan list
```

开始任务先读项目记忆库首页及当前状态，再按任务读产品文档、对应技能与源码。
任务材料沿原任务身份归集，当前状态通过既有业务存储读取；变更后登记来源、验证和下一步。
