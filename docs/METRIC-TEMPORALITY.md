# 运营指标的时间与数量口径

按机器、账号槽位、北京时间自然日和指标保存统计事实。原始事件、员工回执继续保留；
经理查询库的 `daily_metric_facts` 可重复重建，网页与查询使用同一计算函数。
`metadata.metric_semantics_version=3`；消费者通过 `payload.day_finalized` 区分今日过程值
和历史完整日结果，累计快照仍保存在既有业务记录中，零点不删除任何记录。

| 字段 | 含义 |
| --- | --- |
| actual | 已确认采集范围的当日事件数；今日截至最近采集，历史按完整日；未确认时为空 |
| payload.daily_new | 按发生日期去重的每日新增计数，新一天从 0 开始 |
| payload.as_of | 该日统计截至时间 |
| payload.day_finalized | 已结束日期的全天范围是否完整确认 |
| observed_new | 平台发生日期属于该日、去重后已观察到的事件数；是下界 |
| current_total | 截至该日最后采集的账号存量，例如当前粉丝数 |
| sampled_net_change | 同一 UID 相邻日期快照之差；原始采样起止时间保留在 payload |
| newly_discovered | 该日首次入库的有效记录数，可能包含历史事件或会话轮次 |
| coverage | complete、partial、unknown；主动业务动作为 execution |
| payload | 统计区间、时区、计数依据、采集时间、缺失和排除记录数 |

粉丝数会因关注和取关增减，所以快照差值是净变化；首次快照建立存量基线。
新增按事件发生时间统计，采集时间用于追踪数据到达。已回复、待处理等状态独立于新增计数。
晚到事件归到其发生日。重复导入与同一评论的通知重复不增加计数。

采集事件保留 `time_scope`、`event_occurred_at`、`captured_at/observed_at` 和计数单位。
粗略相对时间和缺少平台时间的观测不据采集日期推断发生日。会话轮次可包含多条私信，
只有逐条平台消息标识和发生时间齐备时才能形成按日消息数。
增长通知采用单事件容器，收藏与点赞分别识别；内容型标识不包含变化的相对日期及链接追踪参数。
内容型标识只支持观察下界，完整日计数需要明确的完整采集区间、事件单位和无未定日期记录。

今日四项被动指标显示每日新增计数，按北京时间零点切换新日期；历史日报及累计快照保留。
尚未首次采集显示 0 并标记待首次采集，actual 保持空；采集失败保留已确认新增并显示异常。
今日范围截至最近采集，不要求覆盖未来时间。后续完整批次覆盖的早期错误不再作为当前原因。
历史网页展示“至少 N”代表已确认下界，“采集缺口”代表已结束日期尚未采齐。
鼠标悬停查看缺口原因、最近采集时间、当前总量、采样净变化及首次入库量。
今日、历史及总览共用展示逻辑。

```sql
SELECT business_date, metric, actual, observed_new, current_total,
       sampled_net_change, newly_discovered, coverage
FROM daily_metric_facts
WHERE machine_id = ? AND account_slot = ? AND business_date = ?;
```

GitHub 参考：

- [OpenTelemetry #20770](https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/20770)：首次采样与基线会影响累计转增量结果。
- [OpenTelemetry 数据模型](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/metrics/data-model.md)：累计/增量的时间区间与数据流身份。
- [Prometheus #11466](https://github.com/prometheus/prometheus/discussions/11466)：指定区间的真实计数增加与采样查询存在区别。

这里采用精确事件计数与独立快照净变化，结合现有 JSON 回执实现。


## 采集批次与日范围

三类采集命令通过 `collection_batches` 返回各指标的采集记录，应用服务将其存为
`collection_observation`，随现有员工业务表及 outbox 回传。字段包括 metric、unit、
period_start、period_end、coverage、undated_count、observed_count、source、reason。
逐条私信使用 `private_message_item` 和 `unit=message`，回复轮次另行保存。

当前历史采集从昨日北京时间零点至本轮开始时间申报范围；只有明确到达源历史末尾、
事件日期及标识可用于统计时才标记 complete。数量上限、翻页中断、日期或标识缺失均记录
partial 和原因。页面空结果需要明确空状态才能确认零。

经理端按账号合并全部有效采集区间，跨日回传可以补全发生日，完整区间中的无事件日期也生成
日报记录。`collection_progress` 保存批次、缺失区间和最近采集时间；完整覆盖后 actual 为去重
事件数。后续失败批次保留原有成功区间，重复观测保留已确认的发生日期。

历史缺口通过 `backfill_required` 标识。该标识是补采需求，不代表已启动补采或补采完成。
真实完整日计数以员工采集证据及经理端导入后的查询结果共同验收。

## 平台事件与完整事件索引

粉丝及点赞读取当前登录页面收到的通知接口响应，保留平台事件 ID 和时间，并验证分页游标
连续及 `has_more=false`。点赞按 `liked/item`、`liked/comment` 统计，收藏独立识别。
评论使用平台评论标识及发生时间。页面采集作为补充证据，无法确认范围时保留缺口原因。

私信通过当前登录页面已初始化的 IM 客户端读取一对一会话和逐条历史。会话列表核对平台
`total` 与 `nextTs`；消息历史按 `storeId` 向前分页。消息保留独立 ID、毫秒时间和收发方向，
统计收到的文本及卡片等实际消息。平台提示、撤回提示和自己发送的消息分别识别。
回复仍使用最新对话上下文，统计历史单独保存。会话存在历史而本轮返回空数组时记录采集缺口。

完整批次附带 `event_index`，每项为事件 ID 与北京时间发生日期。经理合并完整覆盖区间的
索引并按事件 ID 去重，得到该自然日确认数；该索引也能确认零值。历史页面观测和回复轮次
继续留存，由完整平台事件索引提供最终计数。采集异常及数量上限仍体现在批次完整性中。
