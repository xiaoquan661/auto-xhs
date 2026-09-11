# 运营动作验收与经理数据库

## 验收口径

四个账号合计每种动作至少一条成功证据。浏览、搜索、点赞、收藏、评论、评论回复、关注博主、
图文发布、视频发布、长文发布、私信发送、入站私信回复、运营指标采集、评论采集和私信采集
分别登记目标、已确认实际数、来源账号与回执。评论加图片、拉群不在本轮范围。

任务接收、排队、跳过和结果未知不计成功。互动需要平台回读，点赞、收藏和关注需要实际变化；
发布需要完成确认流程且回读成功。成功的空采集算完成一次采集，但不表示新增事件。
历史累计与指定日期分别查看；同一执行只统计最新修订，不累计重复导入。
私信任务通过 parameters.acceptance_action 区分 private_message 与 private_reply。

```powershell
.venv/Scripts/python.exe scripts/manager_cli.py --manager-home C:/auto-xhs/manager-state acceptance
.venv/Scripts/python.exe scripts/manager_cli.py --manager-home C:/auto-xhs/manager-state acceptance --date 2026-09-10 --account PC057:ceshi3 --account PC057:tesssst
```

这是证据验收清单，target=1 是每类动作的合计验收门槛，并非修改账号每日运营配额。
不足项保留 PENDING。真实发布素材、发送对象及发布预览确认通过原业务流程准备。

## 数据范围与同步

经理目录内的 `manager-operations.db` 是 SQLite 查询库。`records` 保留经理状态全部集合和
原始 JSON 字段；`worker_records` 保留每台员工机 `operations.db` 全部业务表、表结构和
`product-state.json`。账号浏览器配置、Cookie 和认证存储仍在员工本机。

员工独立入口 `scripts/worker_data_export.py` 通过 SQLite 只读事务取得一致快照。
经理按机器事务替换当前业务快照；失败保留上次成功数据。业务表内历史及经理原始回执继续保留。
`metadata` 显示每台机器导出、导入时间。员工 SQL 表中的 JSON 字符串字段可再次使用
`json_extract` 读取；空表字段保存在 `_schema` 记录内。

部署导出入口并验证后，在机器记录启用 `business_data_sync_enabled`，随既有经理结果回收
周期同步，无需增加独立周期任务。运行中的经理进程需重载新代码。

```powershell
.venv/Scripts/python.exe scripts/manager_cli.py --manager-home C:/auto-xhs/manager-state database collect --machine-id PC057
.venv/Scripts/python.exe scripts/manager_cli.py --manager-home C:/auto-xhs/manager-state database sync
.venv/Scripts/python.exe scripts/manager_cli.py --manager-home C:/auto-xhs/manager-state database schema
.venv/Scripts/python.exe scripts/manager_cli.py --manager-home C:/auto-xhs/manager-state database query --input-file C:/absolute/query.json
```

## AI 查询

按日新增及累计指标优先查询 `daily_metric_facts`，字段含义与时间约定见
[运营指标的时间与数量口径](METRIC-TEMPORALITY.md)。

AI 先查字段目录，随后通过参数化 SQL 查询任意已入库字段。所有查询入口均为只读。
每次查询刷新经理状态投影；员工数据以 metadata 的最近成功快照时间为准。
响应携带 truncated，默认 200 行、上限 10000 行；使用 SQL 的 LIMIT/OFFSET 继续读取。

查询文件示例：

```json
{"sql":"SELECT machine_id, collection, count(*) AS rows FROM worker_records GROUP BY machine_id, collection","parameters":[],"limit":200}
```

常用 SQL：

```sql
SELECT * FROM metadata;
SELECT path, type, value FROM fields WHERE collection='results' AND record_key=?;
SELECT json_extract(payload, '$.handling_state') AS handling_state, count(*) AS total
FROM worker_records WHERE collection='inbound_events' AND machine_id=? AND account_slot=?
GROUP BY handling_state;
SELECT path, type, value FROM worker_fields WHERE machine_id=? AND collection='_schema';
```

HTTP 使用既有经理服务：GET `/api/v1/manager/database/schema`、
POST `/api/v1/manager/database/query`（同查询 JSON）；GET/POST `/api/v1/manager/acceptance`。
POST 沿用现有会话头。验收 POST 支持 date 与 accounts 筛选。

查询库覆盖已回传的业务数据，不把快照、缺失字段或待下载证据描述为完整实机验收。
