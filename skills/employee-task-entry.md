# 员工任务执行入口

员工在任务目录使用 `call task-entry.cmd`，命令工具选择 Windows cmd、`login=false`。
宿主负责 Python、项目和任务目录的绝对路径。账号由 `task.json` 注入。

1. `call task-entry.cmd claim` 写领取回执并返回任务、授权、参数和记忆文件路径。
   记忆上下文完整保留在 `memory-context.json`，按治理、共享核心、账号长期、相关短期和
   当前任务的顺序读取。完整原任务保留在 `task.json`。
2. `call task-entry.cmd help` 查询顶层帮助；
   `call task-entry.cmd help --command reply-inbox collect` 查询采集参数。
3. 写入原生 JSON 参数文件，执行 `call task-entry.cmd invoke --input-file collect.json`。
4. 故障材料交给 `report-bug --input-file bug.json`，由 runner 回传；结果写入
   `execution-result.json` 后执行 `call task-entry.cmd finish`。

回复流程的合法参数示例：

```json
{"args":["reply-inbox","collect","--comments-only","--limit","20"]}
{"args":["reply-inbox","list","--comments-only","--state","NEW","--limit","20"]}
{"args":["reply-inbox","context","--event-id","实际事件ID"]}
{"args":["reply-inbox","apply","--decision-file","C:/任务目录/decision.json"]}
```

每行示例分别保存为一个 JSON 文件。数量、渠道、人设及规则沿用任务授权；本轮采集后固定
事件集合，逐项读上下文再生成决策。业务正文放在输入文件中。

入口返回短业务输出，完整输出保存在同名 `.output.json`，错误流在 `.stderr.txt`。
`.execution.json` 记录起止时间、退出码和耗时；参数校验失败也保存这些证据。
进程退出码与业务结果分别判断，`RESULT_UNKNOWN` 先核对原动作证据。

浏览器调用时间记录在 `.bridge.jsonl`，包含方法、耗时和导航就绪方式。
runner 保留原生 `events.jsonl`，另存 `event-timing.jsonl` 的事件接收时间，
可按 item_id 对齐命令开始与结束；事件间隔包含调度和模型等待，不能直接当作推理时间。
`runner-context.json` 和 `runner-finished.json` 提供宿主身份、目录与起止边界。

扩展导航按目标路由、文档切换及笔记/主页数据与 DOM 就绪判定。详情读取和评论可复用已经
就绪的同篇笔记；主页与搜索重新导航保持采集时效。发送动作保持单次调用和平台回读。
