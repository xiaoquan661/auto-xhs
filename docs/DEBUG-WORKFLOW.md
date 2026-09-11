# 员工故障与修复

员工上报问题，经理根据执行证据复现和修复，发布针对该机器的修复包。员工安装后回传测试与
备份记录，经理验证服务并登记验收结论。系统设置中的「故障与修复」提供同一套操作入口。

## 角色与状态

| 阶段 | 执行方 | 留存结果 |
| --- | --- | --- |
| 报障 | 员工执行链或经理登记 | 机器、槽位、任务、错误、现象、证据位置 |
| 诊断 | 经理 Agent | 领取人、复现过程、根因、修复方案、交接单 |
| 修复 | 经理项目分支 | 修改后的源码及复现/回归用例 |
| 构建 | 经理调试服务 | 实际 pytest 结果、文件清单、目标机器、修复目录和 ZIP |
| 下发 | 既有 SSH 传输服务 | 传输编号、员工落盘目录、送达状态 |
| 安装 | 员工调试服务 | 备份、文件替换、编译、回归结果、安装或回滚回执 |
| 验证 | 经理调试服务 | 执行服务重载和健康回读 |
| 验收 | 经理 Agent | 验收结论、必要的原任务结果核对结论 |

工单依次呈现待诊断、诊断中、修复中、待下发、安装中、待服务验证、待验收、已关闭。
包另行记录 READY、DELIVERED、DEFERRED、INSTALLING、AWAITING_VERIFICATION、VERIFIED、
RESULT_UNKNOWN、OFFLINE、FAILED、ROLLED_BACK。故障类型包括代码、运行环境和结果核对。

FAILED、BLOCKED、RESULT_UNKNOWN 任务结果会自动生成工单；PARTIAL_SUCCESS 携带错误码时
生成工单。同一机器、槽位、执行编号和错误去重；原始回执及其修订仍由既有结果存储保留。
BLOCKED 默认进入运行环境类，结果未知进入核对类，经理依据实际根因决定是否需要代码修复。

## 员工报障

失败任务沿用结果回传流程自动报障。员工 Agent 也可以在任务目录编写 `bug.json`：

```json
{
  "summary": "评论结果读取失败",
  "error_code": "COMMENT_READBACK_FAILED",
  "reproduction": "完成填写后读取结果，返回结构解析错误",
  "expected": "返回明确的动作状态",
  "actual": "读取结果异常",
  "category": "code"
}
```

在员工任务目录使用固定入口：

```text
call task-entry.cmd report-bug --input-file bug.json
```

账号、任务和执行编号由任务文件提供，证据指向原任务目录。返回 `QUEUED` 表示故障 JSON
已保存到任务目录 `pending-bugs/`；外层 runner 使用员工服务的实际 outbox 配置发布，成功后
保存同名 `.receipt`。发布失败保留 JSON 和 `.error`，任务正式结果仍按原有流程回传。
跨机器传输继续使用同一份 outbox
索引，新增 `debug_incident` 与 `debug_receipt` 记录；经理离线时记录留在员工机。
普通任务之外可通过 `manager_worker.py` 的 JSON stdin 请求 `debug-report`。

## 经理处理

界面入口：**系统设置 → 故障与修复 → 查看与处理**。领取诊断会生成经理数据目录中的
`debug/handoffs/<工单编号>/REPAIR.md` 与 `incident.json`，经理 Agent 读取后在项目分支修复。
源码修改由经理 Agent 执行；后台服务承担存储、测试、传输、安装和验收状态管理。

CLI 与界面共用服务：

```text
<经理 Python> <项目>/scripts/manager_cli.py debug list
<经理 Python> <项目>/scripts/manager_cli.py debug <操作> --input-file <JSON 绝对路径>
```

| 操作 | JSON 输入 |
| --- | --- |
| report | machine_id、account_slot、summary，以及可选错误和证据字段 |
| show / claim | incident_id；claim 可带 owner |
| diagnose | incident_id、reproduction、root_cause、resolution |
| build | incident_id、files 数组、tests 数组，可选 project_root 绝对路径 |
| dispatch / apply / reconcile / verify / rollback | release_id |
| accept | incident_id、acceptance_note；结果未知工单还需 reconciliation_note |
| resolve | 运行环境或结果核对类 incident_id、resolution |
| reopen | incident_id、reason |

构建示例：

```json
{
  "incident_id": "DBG-示例编号",
  "files": ["scripts/xhs/feed_detail.py"],
  "tests": ["tests/test_content_helpers.py"]
}
```

`build` 在指定经理源码目录运行 pytest，将指定源码与测试放入包中；测试执行结果和日志路径
由实际进程产生。pytest 参数采用测试文件路径或 `tests/test_example.py::test_case`。
测试涉及额外 fixtures、辅助模块时，把这些文件也列入 files。支持 Python 代码、WebUI、
扩展和技能文件；测试应覆盖修改功能，需要 Node 验证时可在 pytest 用例中调用已有 Node 测试。

修复包存储在经理数据目录 `debug/releases/<修复编号>/`，包括 `manifest.json`、
`payload/`、`INSTALL.md`，旁边留存同版本 ZIP。SSH 使用现有目录传输接口投递修复目录，
transfer_type 为 `repair_package`。此存储用于具体故障的代码部署；功能版本发布采用项目
正式功能包规范。

## 安装与验收

1. `dispatch` 确认修复目录送达员工。
2. `apply` 检查当前运行任务，有任务运行时返回顺延。进入安装后暂停员工及本地执行服务，
   备份受影响文件、替换、编译并运行回归测试，失败恢复备份。
3. `reconcile` 读取安装状态，可在网络中断后重复查询；员工 outbox 同时保留安装回执。
4. `verify` 在取得安装成功和员工测试通过回执后重载执行服务，并核对健康状态。
5. `accept` 登记复现问题的验收结果，关闭工单。涉及扩展文件的修复还应完成扩展重载及页面
   验证，再登记结论；服务健康回读只说明执行服务可用。
6. 按运营安排使用既有机器恢复操作继续任务。安装结束后机器保持暂停，原任务记录保留。

安装响应中断显示 RESULT_UNKNOWN，先回收安装状态。原业务动作的 RESULT_UNKNOWN 仍需
按照原执行证据核对，代码安装回执不改变业务结果。重复安装请求返回既有安装记录。
进程中断留有 INSTALLING 记录时，先回滚并核对，再为修复创建新版本。

`rollback` 恢复该包安装前的源码，包含移除该包新增的文件，并保留回滚回执。
已经重载过服务的包回滚后，应通过现有员工服务重载入口加载恢复后的代码，再核对服务。
账号配置、Chrome Profile 和运营数据沿用现有存储。

## 接口与验证范围

- `GET /api/v1/manager/debug`：读取工单和修复包。
- `POST /api/v1/manager/debug/<操作>`：与 CLI 同名操作，沿用经理 WebUI 会话。
- 员工 SSH 入口：`debug-report`、`debug-apply`、`debug-status`、`debug-rollback`。
- `tests/test_debug_workflow.py`：报障回收、去重、文件传输、安装、测试失败回滚、响应丢失核对、
  忙碌顺延、健康验证、账号隔离、验收、延迟回执和接口测试。

首次使用需将调试模块及协议入口同步至经理和员工项目，重载经理服务。代码自动测试与真实
机器部署分别登记；真实平台业务恢复使用原有授权及结果核对流程。
