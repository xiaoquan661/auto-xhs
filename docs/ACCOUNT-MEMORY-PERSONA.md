# 账号记忆与人格

需求范围：`REQ-20260904-001 v1.1`，MEMORY-01 至 MEMORY-06、REPLY-01。

| 功能 | 使用方式与行为 |
| --- | --- |
| MEMORY-01 建立共享核心与账号长期记忆 | 账号管理 → 账号记忆，分别维护共享核心和账号长期经验；来源、类别、版本、审核人及生效时间可追溯。新建及修改知识进入待审核，批准后参与任务加载，拒绝或停用后退出加载。 |
| MEMORY-02 保存员工短期记忆 | 员工固定入口按账号身份、任务、会话保存观察、上下文、临时经验和摘要；默认有效期 168 小时。重启后可恢复同一任务，交接摘要独立保存并供同账号后续任务读取。 |
| REPLY-01 配置账号人格 | 账号管理 → 账号人设，配置身份背景、专业方向、表达语气、互动方式、回复长度和行为边界；保存版本历史和生效时间，关联平台 UID 对应的记忆空间。任务保存采用的人格快照，后续修改用于新的派发任务。 |
| MEMORY-03 回传动作事实与会话记忆 | 过程消息及最终结果沿用员工 outbox，由经理回收；动作事实和交接摘要直接留存，专业经验作为待审核建议。相同来源、身份、会话、事件的重传复用已有记录。 |
| MEMORY-04 审核经验并导入记忆宫殿 | 经理 Agent 审核普通经验，核心人格和共享治理规则由用户确认。五种审核状态与理由可查询；批准后按账号或共享范围生效，重复和冲突记录保留关联来源。 |
| MEMORY-05 检索记忆并加载执行上下文 | 按治理与运营规则、共享核心、账号长期记忆、相关短期记忆、当前任务顺序加载。任务执行、评论和回复生成接入上下文，记录加载与实际采用的来源版本。 |
| MEMORY-06 更新记忆与验证使用效果 | 纠正、替换和过期保留版本历史与原因；新知识审核后用于后续任务。覆盖跨日恢复、账号隔离、重复回传和更新前后生成内容采用不同版本。 |

## 架构与存储

2026-09-09 通过 SSH 只读核对 PC055 的 `resident_short_term.py` 和
`resident_memory_router.py`。本实现采用其分层存储、按身份召回、事实与经验分别处理、交接摘要
独立保存的组织方式，并接入本项目的 Manager / Worker / ApplicationService。

```text
经理：共享核心与账号长期经验 → 任务记忆快照 + 人格版本
  → 既有任务包 / 员工入口 → 本地任务加载有效短期记忆与交接摘要
  → 业务执行 → 过程事件 / 最终结果 → 员工 outbox
  → 经理回收 → 运行事实、交接摘要、待审核经验
```

- 经理记忆库：`<XHS_MANAGER_HOME>/memory/account-memory.sqlite3`。
- 员工记忆库：`<员工服务 root>/memory/account-memory.sqlite3`。
- `memories` 保存当前记录，`memory_versions` 保存历次正文、状态和来源。
- `shared_core` 是共享空间；账号空间由 `platform + uid` 标识，机器和槽位保留在身份元数据中。
  同一槽位更换 UID 后对应新的记忆空间；平台相同、UID 相同的账号在换机器后仍可关联原有记忆。
- `short_term` 是员工本地临时上下文，`handoff` 是独立的交接记录。任务结束时汇集短期观察摘要；
  经理回收交接摘要，员工本地保留短期记录。导入保留交接原过期时间。
- `runtime_fact` 记录实际状态及证据位置，`RESULT_UNKNOWN` 作为原状态保存。
  回传知识始终进入 `PENDING_REVIEW`；知识审核不会改变执行结果计数。
- 检索使用文本关键词与身份、任务、会话过滤。任务先接收经理端已生效知识与人格快照，
  开始执行时补充员工本地有效上下文；本地业务执行前核对记忆身份与实时登录 UID。
- 每层任务记忆优先加载关键词相关记录，再补充近期记录，最多 30 条；员工恢复短期快照时，
  以本地最新版本与过期时间为准，并核对所属任务。
- 交接摘要和经验建议按 `source_version` 推进同一来源事件的版本；旧版本重传复用当前记录。
  已批准经验收到新版正文后重新进入待审核，审核历史与来源版本分别保存。
- 记忆写入失败时，任务执行结果仍持久保存，记忆状态标记 `PENDING_REPAIR`；经理回收失败可重试导入。

## 经验审核与知识更新

2026-09-09 用户确认的审核分工：普通经验由经理 Agent 审核，核心人格和共享治理规则由用户确认。
经理 Agent 每 60 秒检查一轮，最多处理 3 条建议，复用系统设置中的模型配置。
`memory_review_service.py` 向模型提供建议正文、同范围有效知识和任务事实，要求返回决定与理由。
涉及人格或治理的普通类别建议可转为用户确认；依据不足的建议保持待审核，补充修订后再次进入队列。
模型未配置时返回 `WAITING_MODEL`；调用失败时保留待审核状态，并在本轮经理同步结果中记录错误。

`review_status` 区分 `PENDING_REVIEW`、`APPROVED`、`REJECTED`、`DUPLICATE`、`CONFLICT`。
`state` 另记录知识当前是否有效，包括 `ACTIVE`、`RETIRED`、`EXPIRED` 和已导入共享范围的 `PROMOTED`。
批准先检查同范围有效知识：正文相同记为重复；相同 `canonical_key` 的不同正文记为冲突；
模型也可根据语义指出关联记录。批准不会使重复或冲突内容进入当前召回。

默认批准到原范围。选择 `target_layer=shared_core` 时，将通用经验导入共享知识，并保留
`origin_identity`、`origin_memory_id` 及原建议的导入关联。核心人格和共享治理规则仍须用户确认。
在 WebUI 可编辑知识、填写变更原因和有效期，或将记录设为过期；冲突记录支持替换关联知识版本，
新版本审核后生效。`update` 接口接受 `change=correct|replace|expire`、`memory_id`、`reason`；
纠正和替换另带 `content`，可附 `source`、`summary`、`expires_at`。

## 执行与生成的采用记录

`memory_context.py` 统一组织有序上下文。治理类别知识归入首层，交接摘要并入短期上下文，
记录稳定的 `memory_id@version` 引用。员工启动时加载本地有效短期记忆，并缓存收到的账号上下文，
评论、手动回复草稿和自动回复周期在生成前读取记忆。经理机本地生成优先读取所管理账号的当前知识；
员工生成使用该账号收到的上下文及本地观察。跨机知识更新随后续任务上下文送达。

`memory_usage` 保存任务、会话、阶段、加载来源、采用引用和结果。任务准备、执行上下文、评论生成、
回复生成使用任务的人格快照，并登记人格编号与版本；经理本地独立生成读取当前有效账号人格。
各阶段分别留痕，员工使用记录随最终结果回收。生成器只接受本次上下文中存在的来源引用，
`adoption_reported` 区分是否收到模型的采用回报；查询时分别显示已加载和已采用。
模型回报用于追溯其声明的依据，实机阶段还需结合生成正文和平台结果评估效果。

WebUI“查看采用来源”按账号和可选任务编号查询，经理 CLI 与 HTTP 使用 `memory/usage`。

## 经理入口

WebUI：账号管理 → 账号记忆。可查询长期知识、交接摘要、运行事实，查看版本，批准、拒绝及停用知识。
账号人设详情显示人格版本记录。

HTTP：`POST /api/v1/manager/memory/{save|search|review|history|recall|update|usage|review-pending}`，沿用现有会话请求头。
搜索使用 POST 传递账号、任务及筛选条件。

CLI：

```powershell
& 'C:\Users\EDY\Documents\Codex\2026-07-31\readme-md\xiaohongshu-skills\.venv\Scripts\python.exe' 'C:\Users\EDY\Documents\Codex\2026-07-31\readme-md\xiaohongshu-skills\scripts\manager_cli.py' memory save --input-file 'C:\xhs-inputs\memory.json'
```

`save` 请求示例：

```json
{
  "machine_id": "PC057",
  "account_slot": "brand-a",
  "layer": "account_long_term",
  "category": "experience",
  "content": "讨论盆栽养护时先确认光照与通风环境。",
  "summary": "盆栽养护沟通经验",
  "source": "已回读的任务记录编号"
}
```

维护既有知识时添加 `memory_id`。`search` 使用 `layer`、`query`、`task_id`、`session_id`、
`include_inactive` 和 `limit`；账号层另带 `machine_id` 与 `account_slot`。
`review` 使用 `memory_id`、`decision`（`approve`、`reject`、`retire`）和 `reviewer`。
还可传入 `reason`、`expected_version`、`target_layer`；人工标记重复或冲突时使用
`decision=duplicate|conflict` 和 `related_id`。
`history` 使用 `memory_id`。`recall` 使用机器、槽位以及可选任务、会话、关键词。

## 员工入口

通过既有 `scripts/manager_worker.py` JSON 标准输入入口调用 `memory-save`、`memory-search`、
`memory-recall`。必须使用已接收任务的 `task_id`；服务从该任务取身份和会话，账号字段不能覆盖任务身份。

```json
{
  "action": "memory-save",
  "task_id": "DAILY-20260909-001",
  "layer": "short_term",
  "event_id": "observation-1",
  "content": "已完成前两篇的阅读，第三篇等待查看详情。",
  "summary": "接着阅读第三篇",
  "ttl_hours": 168
}
```

`short_term` 留在员工本地；`handoff`、`runtime_fact`、`account_long_term` 同时产生回传事件。
经验建议由经理审核。调用方重试同一观察时复用 `event_id`；恢复任务时调用 `memory-recall`。
最终结果也可携带 `business_result.memory_records`，每项包含层次、稳定事件编号和正文。

## 验证与交付状态

专项测试：`tests/test_account_memory.py`，覆盖知识审核与版本、UID/平台隔离、过期和重启恢复、
过程与最终回传去重、人格快照、实时身份变更及记忆库异常时的业务结果保留。
前端行为测试包含人格字段编辑与保存；隔离测试环境已完成浏览器知识审核及人格版本回读。

2026-09-09 验证结果：专项 17 项、全量 Python 回归 758 项通过，三个相关前端测试文件通过。

MEMORY-04 至 06 完成后：记忆专项共 28 项，全量 Python 回归 769 项通过；三个前端行为测试
文件通过。隔离浏览器验证了核心规则批准、审核历史、重复与冲突展示、采用来源查询、过期原因
回读及窄屏布局。审核与生成采用可控模型响应验证调用和数据链路，模型实际审核质量与真实账号
连续运行在实机阶段验收。

本地实现与自动化验证已完成。员工机部署、真实账号运行、正式功能包上传及安装分别验收。
