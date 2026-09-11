# 运营指标的时间与数量口径

按机器、账号槽位、北京时间自然日和指标保存统计事实。原始事件、员工回执继续保留；
经理查询库的 `daily_metric_facts` 可重复重建，网页与查询使用同一计算函数。

| 字段 | 含义 |
| --- | --- |
| actual | 完整日范围内确认的事件数量；覆盖未完成时为空 |
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

网页展示“至少 N”代表已确认下界，“待确认”代表全天总量尚不确定。
鼠标悬停查看当前总量、采样净变化及首次入库量。今日、历史及总览共用展示逻辑。

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
