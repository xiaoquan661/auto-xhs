# xiaohongshu-skills

小红书自动化项目。项目根维护工程规则和开发基准，经理与员工工作区维护对应角色职责。

## 角色入口与源码基准

- 经理公共规则：`ManagerStation/Managers/AGENTS.md`。
- PC023 经理入口：`ManagerStation/Managers/PC023/AGENTS.md`。
- 员工公共规则：`WorkerStation/Workers/AgentWorkers/AGENTS.md`。
- 五机员工入口：`WorkerStation/Workers/AgentWorkers/PC052/AGENTS.md`、
  `PC057/AGENTS.md`、`PC065/AGENTS.md`、`PC071/AGENTS.md`、`PC073/AGENTS.md`。
- 程序实例定义：`WorkerStation/Workers/ProgramWorkers/instances/<PC编号>/instance.json`。
- 技能总入口：`SKILL.md`；个人规则按任务索引到当前技能的 `SKILL.md`。

角色目录的 PC 编号是本项目的本地职责工作区标识。经理业务身份沿用 `MILA-MANAGER`。
员工账号人设、授权及计划属于业务配置，按机器和账号槽位隔离。

经理实现基准：
`ManagerStation/Managers/PC023/function_packages/xiaohongshu-skills/0.1.0/manager/runtime/scripts/`。
员工实现基准：
`ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/runtime/`。
员工包包含 `runtime/scripts/`、`runtime/shared/`、`runtime/execution/`、
`skills/`、`extension/`，由经理维护并按版本交付员工。
项目根 `scripts/` 提供固定调用入口；具体实现与发布版本绑定。

Git 仓库、`.github/`、`pyproject.toml`、`uv.lock`、`docs/`、完整 `tests/` 和
`project-memory/` 在项目根维护。功能级合同、测试、依赖与交付说明随对应版本包归集。
公共组件依赖按照各组件已有登记和运行入口引用；小红书实现按业务功能包维护。

## 工程工作约定

- 校验围绕实际改动和具体风险，保持与项目规模相称。
- 使用路径、版本、文件数量、字节数、行为测试与实机回读作为交付证据。
- 标题、文件名、注释、提交和交付说明描述最终采用的状态。
- 历史事实、已执行外部操作、兼容要求及必要诊断按交付需要保留。
- 代码、状态、业务材料、日志与开发文档按各自职责归位；源码维护采用一个权威位置。

## 项目记忆库

- 开始项目任务时，先读 `project-memory/README.md`、`project-memory/03-当前状态.md`，
  然后按任务读取相关主题和源码。
- 完成有持续价值的项目工作后，在 `project-memory/工作记录/` 登记结论、来源、验证结果与下一步，
  并更新该目录索引；阶段变化时更新当前状态，用户确认的新决策归入决策记录。
- 记忆是项目上下文索引；产品规则以 PRD 和追踪矩阵为准，实现以当前源码为准。
- 只读取相关材料。临时资料放入收件箱；推测和已确认事实分开记录，动态状态注明日期。
- 凭据、Cookie、验证码、令牌和私密消息正文保留在既有受控存储中，记忆库记录证据位置和摘要。

## Git 工作流

- 所有代码修改必须在分支上进行。
- 分支开发完成后通过 PR 合入 `main`。

## 开发命令

在项目根执行：

```powershell
uv sync
uv run ruff check .
uv run ruff format .
uv run pytest
```

## 程序结构与入口

- 员工版本包 `runtime/scripts/xhs/`：浏览、搜索、评论、消息和发布的页面实现。
- 员工版本包 `runtime/scripts/application_service.py`：CLI、WebUI 和 Agent 共用的业务服务。
- 员工版本包 `runtime/execution/bridge_server.py` 与 `extension/`：账号 Bridge 和浏览器侧能力。
- 经理版本包 `runtime/scripts/manager_service.py`：经理任务和跨机器协作业务。
- 经理版本包 `runtime/scripts/manager_store.py`、`manager_database.py`：经理配置与业务记录。
- 经理版本包中的 `webui/`：经理业务工作台；员工本机 API 沿员工程序入口提供。
- 员工版本包 `skills/<skill_id>/SKILL.md`：Agent 业务技能指引。

运行命令使用实际项目根和输入文件的绝对路径。在项目根解析当前路径后调用固定入口：

```powershell
$projectRoot = (Resolve-Path .).Path
python "$projectRoot\scripts\cli.py" --help
python "$projectRoot\scripts\manager_cli.py" --help
```

## 代码规范

- 行长度上限 100 字符。
- 使用完整 type hints 和 `from __future__ import annotations`。
- 异常继承 `XHSError`（员工版本包 `runtime/scripts/xhs/errors.py`）。
- CLI exit code：0=成功，1=未登录，2=错误。
- 用户可见错误信息使用中文。
- JSON 输出使用 `ensure_ascii=False`。
- 敏感输入通过文件传递，命令只传绝对文件路径。
- 每次修改扩展可执行文件时递增其 Chrome `manifest.json` 版本；部署验收使用目标
  Chrome Profile 的 Bridge 回报版本，重载并核对后执行对应业务。

## 功能包打包规范

- 本项目职责包以 `xiaohongshu-skills/0.1.0` 维护经理实现与员工源实现，项目版本来自
  `pyproject.toml`；业务功能包沿真实功能编号及版本登记。
- 正式业务包位于经理 manifest 的 `paths.formal_function_packages` 下 `<业务模块>/<功能编号>/<版本>/`，保留各版本，
  同步更新 `../功能包/版本清单.json`。
- 每包包含 `manifest.json`、`README.md`、`runtime/`、`config/`、`tests/`、
  `CHANGELOG.md`、`ROLLBACK.md`、`RUN_RESULT.json`、`VERIFICATION_REPORT.md`。
- 角色交付包按所属角色包含 `manager/` 或 `worker/`，实现、合同与测试分别归入
  `runtime/`、`contract/`、`tests/`；生效配置和任务数据由工作区路径引用。
- `manifest.json` 保持一致的功能编号、任务编号、名称、包名、适用层、执行方式、
  依赖、调用关系、运行入口和证据文件。
- 运行入口须可执行；测试覆盖固定行为和相关历史回放。
- 实机证据记录设备、系统、Python、动作、结果和结论；自动测试、实机验证、上传、
  开放及正式安装分别登记状态。
- 一个 ZIP 对应一个功能的一个版本；提交前从 ZIP 内复核身份、审核字段、必需文件与运行证据。
