# 小红书经理与员工目录

本项目按 NexaFlow V0.20 的经理维护源版本、员工引用执行版本的职责组织源码。
PC023 是经理工作区，业务经理身份沿用 `MILA-MANAGER`；PC052、PC057、PC065、PC071、
PC073 各有员工规则及程序实例定义。目录中的版本 `0.1.0` 来自项目 `pyproject.toml`。

## 实现位置

以下位置相对于项目根。实际调用时先解析项目根，再使用绝对路径。

| 职责 | 权威实现位置 | 主要内容 |
| --- | --- | --- |
| 经理业务 | `ManagerStation/Managers/PC023/function_packages/xiaohongshu-skills/0.1.0/manager/runtime/scripts/` | 计划、策略、跨机器投递、回收、数据库、运营汇总、经理实时知识 |
| 经理界面 | 同版本 `manager/runtime/webui/` | 经理控制台、远端员工管理、账号汇总、调试与结果展示 |
| 员工业务 | `ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/runtime/scripts/` | ApplicationService、CLI、任务、互动、发布、采集、回复、xhs 页面库 |
| 员工执行支撑 | 同版本 `worker/runtime/execution/` | 任务领取、Agent runner、账号环境、Bridge、Chrome 与执行服务进程 |
| 项目共用能力 | 同版本 `worker/runtime/shared/` | JSON、错误、记忆模型、日期与回执展开、本机应用 HTTP 接口、资源路径 |
| 员工界面 | 同版本 `worker/runtime/webui/` | 本机员工控制台与共用图标 |
| 员工技能、扩展、提示词 | 同版本 `worker/skills/`、`worker/extension/`、`worker/prompts/` | Agent 指引、真实浏览器执行能力、回复文本模板 |
| 开发与交付 | `ManagerStation/Managers/PC023/scripts/development/` | 环境准备、功能包构建、经理及员工代码修复工具 |
| 五机员工岗位 | `WorkerStation/Workers/AgentWorkers/<PC编号>/` | AGENTS、Personal、Role、worker manifest |
| 五机程序实例 | `WorkerStation/Workers/ProgramWorkers/instances/<PC编号>/` | 交付源版本与设备部署路径 |

经理与员工分别维护职责规则，根 `AGENTS.md` 维护工程基准。业务账号人设属于经理配置和任务
快照；员工 `Personal.md` 记录岗位沟通偏好。员工 runner 将公共规则、本机规则、个人规则、
岗位说明及当前技能的绝对路径加入任务执行上下文。

## 职责接口

经理入口 `web_server.py` 处理经理接口，并调用共享的 `application_http.py` 本机应用接口。
员工 `executor_server.py` 直接使用本机应用接口和员工状态服务。

经理运营汇总由 `manager_employee_operations.py` 实现；共用的 `employee_operations.py` 提供
日期、回执展开和兼容导出。经理实时知识由 `ManagerReplyMemoryResolver` 显式注入经理本机应用
服务；员工按原任务缓存和本地记忆生成回复。

跨机器任务继续采用项目现有的 JSON、SSH/SFTP、inbox、领取、outbox、回收和数据库导入协议。
业务接收、领取、执行结果与导入状态分别沿各自记录维护。Hub 接入接口属于后续联调阶段。

## 路径解析与运行入口

`source-layout.json` 记录 Python 模块、职责、PowerShell 入口和资源根。
根 `scripts/` 是稳定调用层，加载器把旧入口、普通模块导入与 `scripts.*` 导入解析到同一实现。
源码模块的 `__file__` 指向职责目录中的真实文件；新开发修改该文件。

`project_paths.py` 统一提供项目根、扩展、技能、提示词及静态资源位置。
`webui-layout.json` 将现有 HTTP 静态 URL 解析到经理、员工或共用资源，页面调用 URL 延续。
两端相同图标使用同一份资源。

```powershell
$projectRoot = (Resolve-Path .).Path
python "$projectRoot\scripts\manager_cli.py" --help
python "$projectRoot\scripts\cli.py" --help
python "$projectRoot\scripts\manager_worker.py" --help
python "$projectRoot\scripts\executor_server.py" --help
```

Python 命令也支持通过职责目录中的真实入口直接运行。
根 `start-auto-xhs.cmd`、`restart-auto-xhs.cmd` 经稳定 PowerShell 入口调用经理实现。

## 源版本与功能版本

职责源包标识项目源码组合。正式业务功能版本沿原 `task_code`、`function_id`、`version`
登记，在经理 manifest 的 `paths.formal_function_packages` 指向的功能包目录 保留各版本体及原验证证据。PC023 的功能目录清单和包配套关系引用这些
真实版本，以及所属经理或员工源实现。功能编号、源项目版本和设备加载版本分别记录。

记忆功能包构建器从路径映射读取真实实现依赖闭包，将源码、稳定入口、加载器、资源映射和员工
角色规则放入独立快照；包内来源验证核对实际模块来自该快照。源码 Release 同时包含职责目录、
固定入口、启动 CMD、构建模板、测试和开发资料。

代码修复包登记目录协议版本与真实实现位置。经理在传输前读取目标设备的目录版本，员工在安装前
核对本机目录协议；旧源码版本继续使用对应的修复构建入口。

## 工作区与运行数据

Git、`.github/`、项目依赖、产品文档、完整测试及项目记忆在项目根维护。
PC023 经理状态读取 `XHS_MANAGER_HOME`；当前本机配置为 `C:\auto-xhs\manager-state`。
素材读取 `C:\小红书Mila文件存档\素材`。

五机实例定义引用 `C:\auto-xhs\xiaohongshu-skills`、机器配置中的 inbox/outbox，及
`C:\Users\EDY\.xhs\auto-xhs\manager-worker`。运行数据、配置、数据库、账号状态与历史回执由
既有存储继续管理。设备安装、服务重载和实际加载版本通过各设备部署证据登记。

本轮交付是本机源目录及规则的适配；五机定义用于后续交付和部署核对。
本地自动测试和远机实际安装分别登记在验收记录中。
