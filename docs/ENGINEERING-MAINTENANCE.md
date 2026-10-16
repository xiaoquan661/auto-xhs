# 工程维护指南

适用版本：项目 0.1.0 / V0.20 职责布局。更新日期：2026-10-16。

## 阅读与源码入口

先读[项目首页](../README.md)、[项目当前状态](../project-memory/03-当前状态.md)和
[架构与使用手册](项目框架结构.html)。连续流程、定时采集和落盘路径见
[连续流程版](项目框架结构-连续流程版.html)。

- 经理源码由 PC023 的 `function_packages/xiaohongshu-skills/0.1.0/manager/runtime/` 维护。
- 员工源实现、技能与扩展由 PC023 的 `worker_packages/xiaohongshu-skills/0.1.0/worker/` 维护。
- 项目根 `scripts/` 是固定调用入口，具体模块按[源码布局](../source-layout.json)解析。
- 管理台静态资源沿[界面布局](../webui-layout.json)定位。
- 员工安装位置由 ProgramWorkers 实例定义登记，部署与 Git 合入分别验收。

## 分支开发与验证

从最新 main 创建 `codex/` 开发分支。提交明确的工程文件，通过 PR 合入 main，
再更新本地 main。当前工作区有草稿时先保存本地副本，按用途整理后继续开发。

在实际项目根执行：

```powershell
$projectRoot = (Resolve-Path .).Path
uv sync
uv run ruff check .
$testState = Join-Path $env:TEMP ('auto-xhs-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testState | Out-Null
$env:XHS_PRODUCT_HOME = Join-Path $testState 'product'
$env:XHS_MANAGER_HOME = Join-Path $testState 'manager'
uv run pytest
$nodeTests = @(Get-ChildItem "$projectRoot\tests" -Filter '*.cjs' | ForEach-Object { $_.FullName })
node --test @nodeTests
git diff --check
```

测试的产品状态与经理状态使用临时目录，通过 `XHS_PRODUCT_HOME` 和
`XHS_MANAGER_HOME` 指定。格式化针对本次修改的 Python 文件执行。
提交前核对暂存文件清单；合入后获取远程引用，核对本地 main 与 origin/main 双向差异均为零。

## 手册维护

保留原架构手册与连续流程版两个入口。内嵌 `manual-data` 保存图、节点解释、角色和源码映射；
节点含义、数据去向与 FAQ 随源码一同维护。

[手册回归](../tests/test_architecture_manual.cjs)核对 JSON、脚本语法、连线端点、源码路径和本指南链接。
浏览器布局、移动宽度及实机业务另按实际执行结果登记。

## 构建与交付

- 员工职责布局构建入口：[build-worker-layout.py](../deploy/build-worker-layout.py)。
- 员工布局安装入口：[install-worker-layout.py](../deploy/install-worker-layout.py)。
- 新机安装与登记：[员工接入目录](../deploy/employee-bootstrap/README.md)。
- 独立经理与员工环境：[第二套系统部署](SECOND-SYSTEM-DEPLOYMENT.md)。
- 协作流程：[GitHub 协作开发](GITHUB-COLLABORATION-GUIDE.md)。

构建与安装参数以对应脚本的 `--help` 为准，依赖以 `uv.lock` 为准。
正式功能版本保留经理 manifest 指定的项目外位置，按已有版本清单单独维护。

## 资料归集

工程记录保留原因、实现、来源、验证结果和后续事项。
原始运营记录、数据库、账号环境、素材和业务报表由本地业务存储保存；
原始草稿由本地工程归档及 Git stash 保存。通用规则与工程结论进入正式索引。
