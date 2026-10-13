---
type: work-log
project: xiaohongshu-skills
date: 2026-10-13
status: completed
---

# PC023 小红书 Manager 与 Worker 工作区目录登记

## 目标

按本机小红书项目的实际路径，整理 Manager 与 Worker 两层的源码入口和经理运行目录，并在 NexaFlow 局域网账本中登记。

## 已确认结论与涉及文件

- 本机 PC023 的项目源码根目录为 `C:\Users\EDY\Documents\Codex\2026-07-31\readme-md\xiaohongshu-skills`。Manager 的调度、回收、数据库与 WebUI 源码，以及 Worker 的任务执行、平台自动化、扩展、Skills 和员工工作台源码共用此仓库。
- 经理运行根目录为 `C:\auto-xhs\manager-state`，当前包含任务状态、运营数据库、回执回收等运行文件。账本登记保留现有路径与运行方式。
- 本机目录图为 `C:\Users\EDY\Downloads\小红书Mila-Manager-Worker-工作区目录-20261013.html`；对应登记清单与结果分别为同目录下的 `小红书Mila-Manager-Worker-目录登记清单-20261013.json` 和 `小红书Mila-Manager-Worker-账本登记结果-20261013.json`。
- PC023 账本现有两个根：`xhs_project`（源码）与 `xhs_manager_state`（经理运行）。目录图按 Manager、Worker 两层展示真实文件路径。账本保存路径、类型、说明和职责范围；实体文件沿用本机原位置。

## 验证

- 2026-10-13 使用授权账本视图回读：revision `2081`，登记清单 160 项均与账本中的路径、类型、说明和职责范围一致。
- 局域网 `http://192.168.110.8:8812/api/files/snapshot` 显示 PC023 的两个根目录，状态为 `OWNER_REGISTERED`。`/api/files/list` 回读源码根的 `extension`、`scripts`、`skills`、`webui` 四个分层与经理运行根的 14 个直属条目；进一步回读确认 `scripts/manager_service.py`、`scripts/manager_worker.py`、`scripts/executor_server.py`、`scripts/xhs`、`webui/worker` 等目录或文件已可展开。
- 本机 HTML 以独立浏览器配置渲染并检查，Manager 首屏目录、路径与搜索布局显示正常。

## 下一步

项目路径发生实际变化时，按新路径更新 PC023 账本条目。员工机实体工作区由各机器对应的授权节点登记。

## 来源与相关笔记

- `C:\Users\EDY\Downloads\NexaFlow-Dream-三层目录-账本原版V0.16.html`：ManagerStation、WorkerStation 的分类参考。
- `C:\Users\EDY\Downloads\NexaFlow-Ledger-XQ\README.txt`：局域网账本工作区与条目接口说明。
- [系统架构](../02-系统架构.md)与[当前状态](../03-当前状态.md)：本机经理、员工协作链及运行边界。
