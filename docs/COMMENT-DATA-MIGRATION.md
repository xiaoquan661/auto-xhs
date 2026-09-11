# 评论采集与回复数据迁移

## 第一阶段：关联字段与双写

数据库版本 12，沿用现有事件、决策、回复和执行记录。原有字段保持兼容，
新字段由业务服务写入，版本 10 迁移使用同一投影函数回填历史记录。
每个版本的结构变更、回填及版本号在同一事务提交。

| 对象 | 字段与关系 |
| --- | --- |
| inbound_events | comment_id、note_id、parent_comment_id、author_name、content、parent_content、target_url、last_seen_at、detail_state |
| comment_collection_batches | 入库批次、账号、任务、入库开始/结束时间、采集范围、coverage、数量、结果与错误 |
| comment_batch_events | 批次与入站事件的多对多关系、该批次原始 observation_json |
| reply_decisions | 复用 inbound_event_id、decision、reason、cycle_id 和 rule_id/rule_version |
| reply_records | 新增 send_state、platform_reply_id、confirmed_at、logical_action_id、persona_version；复用原评论、正文、草稿、任务及执行记录关联 |
| operation_events | 新增 logical_action_id、attempt_number；复用时间、错误与回读证据 |

notification_id 是通知身份，comment_id 是评论身份，二者分别保留。
父评论关系按账号、平台和 parent_comment_id 查询。未知字段保留 NULL。
重复事件会补充非空详情并更新 last_seen_at，保持已处理状态。
采集批次保留每次原始观察，原有回执和历史业务记录继续保留。

detail_state 为 AVAILABLE、PARTIAL 或 MISSING，表示评论定位和正文的可用情况。
它独立于处理状态和发送状态。

## 发送状态

| send_state | 含义 |
| --- | --- |
| NOT_SENT | 阻塞、取消或等待发送的任务 |
| AWAITING_RECONCILIATION | 结果未知，或历史成功记录缺少明确回读证据 |
| CONFIRMED | 成功任务且具备明确回读标志或平台回复标识 |
| FAILED | 任务明确失败 |
| UNKNOWN | 现有信息无法映射 |

confirmed_at 是数据库确认时间。旧 sent_at 和 readback_state 保留原值供兼容和审计；
新查询使用 send_state。关闭编辑器的回执不构成发送确认。结果未知的任务继续走既有核对流程。
首次采集、空批次及采集覆盖范围独立记录；UNKNOWN coverage 不表示覆盖全天。

## 经理查询

员工全表导出协议自动携带新增字段及批次表。经理初始化同步后提供
comment_reply_details 视图，按机器、账号和平台关联原评论、决策与所有发送记录。
一条评论可以对应多条发送尝试，查询结果行数不是成功动作数。

```sql
SELECT machine_id, account_slot, content, parent_content,
       decision, decision_reason, reply_text, send_state, platform_reply_id
FROM comment_reply_details
WHERE machine_id = 'PC057' AND account_slot = 'tesssst';
```

## 分阶段切换

1. 本阶段交付结构迁移、历史回填、服务双写和经理查询视图。
2. 员工机部署相同版本后初始化迁移，再通过既有全表回收导入经理。
   部署前用 SQLite backup 备份，停止旧版本服务，迁移后检查版本和完整性。
3. 调度器统一生成 logical_action_id，并为重试沿用它、增加 attempt_number。
   当前服务接收调用方提供的关联值；历史缺失值保持 NULL。
4. 管理台切换到新查询，按逻辑目标聚合实际与状态，继续保留逐次执行历史。

当前批次表示进入评论入库流程的一批观察；浏览器读取开始前的失败仍在任务执行记录中。
下一阶段将采集批次起点前移到浏览器调用前，使导航失败也能直接关联批次。
新结构本身不改变浏览器速度或账号队列。

## 巡检回执与指标接入

员工固定入口可调用 `reply-cycle --inspection`：同轮采集运营指标、评论和私信，
按账号规则决定是否回复。回复关闭时保留采集，账号或全局暂停仍然阻止执行。
此选项应接入既有巡检调度，不另外创建重复周期任务。

周期返回 incremental_data，包含原始增量事件、各渠道 collection_observation 和
outbound_reply 记录；沿用现有员工结果标准化、Outbox、经理回收及入库链路。
collection_observation 的观察数量属于本次采集，不能直接冒充今日新增。
outbound_reply 按平台回复 ID 或回复任务 ID 去重，CONFIRMED 才计成功。

网页优先使用经理端指标结果，展示员工巡检来源及最近采集时间。四项被动指标目标为空，
回复目标来自计划；重复巡检不生成目标。已达成目标的指标保留完成状态，失败尝试仍留在历史。
版本 12 回填既有 reply_present 回读标志的确认状态。

部署验收需核对员工实际版本、既有周期入口选项及一轮真实 Outbox 回收；
本地测试不代表周期配置已经在员工机生效。

回滚先停止服务并保留升级后的库，再恢复部署前备份与对应代码。
备份之后产生的数据需另行保留并核对导入，不能直接覆盖丢弃。
