---
name: xhs-content-ops
description: |
  小红书复合内容运营技能。组合搜索、详情、创作、发布和互动能力完成竞品分析、
  热点追踪、内容创作与互动管理。当用户要求多步骤运营工作流时使用。
metadata:
  openclaw:
    requires:
      bins:
        - python
        - uv
    emoji: "\U0001F4CA"
---

# 小红书复合内容运营

只组合本项目 `python scripts/cli.py` 已有能力，不调用其他小红书工具。

## 版本状态

- V1.0 当前读取、点赞、收藏、随机评论和确认式互动按现有服务规则执行。
- V1.5 开放发布、定时发布、私信、评论一次点击直发和规则化自动回复。
- 目标能力未完成执行链时，完成分析或草稿后停止，明确报告待实现，不绕过能力注册表。

## 允许组合的命令

| 子命令 | 用途 |
|---|---|
| `search-feeds` | 搜索笔记 |
| `list-feeds` | 获取首页推荐 |
| `get-feed-detail` | 获取笔记详情和评论 |
| `user-profile` | 获取用户主页 |
| `post-comment` | 对指定笔记评论 |
| 智能批量评论 | Agent通过截图或DOM理解内容、生成1–3条评论，逐条调用 `post-comment` 提交 |
| `home-engagement` | 单次首页会话完成浏览和点赞并输出逐篇记录 |
| `reply-comment` | 单次回复 |
| `like-feed` | 点赞笔记 |
| `favorite-feed` | 收藏笔记 |
| `publish` / `publish-video` / `fill-publish` / `fill-publish-video` | 生成内容后填写并正式发布 |
| `long-article` / `select-template` / `next-step` | 长文排版、填写并正式发布 |
| `click-publish` | 继续已有准备任务并正式发布 |
| `save-draft` | V1.5 用户取消发布时保存草稿 |
| `collect-note-comments` | 自动发现自己最近笔记并回收新评论事件 |
| `collect-private-messages` | 只读回收最近一对一会话中对方最新连续发送的一轮文字 |
| `generate-reply-draft` | 为一个明确的评论或入站私信事件生成待人工确认草稿 |
| `reply-cycle` | 按已启用账号规则处理一轮新评论和新私信 |
| `reply-inbox` | Codex 入站回复收件箱；具体流程路由到 `xhs-reply` |
| `collect-operations-metrics` | 保存账号和自己笔记的运营指标时间快照 |

发布任务按经理目标直接执行。`generate-reply-draft` 只生成草稿；Codex 规则化入站回复使用
`reply-inbox` 并遵循 `xhs-reply`，主动评论和主动私信继续使用各自任务入口。`reply-cycle` 保留为
API 模型兼容入口。

## 强制约束

- 多账号命令把 `--account <账号别名>` 放在子命令之前。
- 每一步都服从对应子技能：读取参考 `xhs-explore`，发布参考 `xhs-publish`，互动参考
  `xhs-interact`，身份参考 `xhs-auth`。
- 不同账号可以并发，同一账号串行。
- 发布生成后直接正式提交并回读；评论在 V1.5 代码完成后由当前任务点击直接授权；自动回复必须先按
  账号启用规则。
- 所有步骤记录真实结果，不把草稿、表单填写或排队描述为发布成功。

## 已发布笔记运营数据

用户下发更新任务时，按机器和账号收集该账号已发布笔记 ID，执行：

```text
python scripts/cli.py --account <槽位> collect-operations-metrics --source creator_manager --note-id <笔记ID>
```

多个作品重复传入 `--note-id`。从创作服务平台的笔记管理读取浏览量、点赞、收藏、评论、
转发累计值，不执行身份检查或进入个人主页。图文和视频使用同一采集链。
将命令完整 `result` 放入员工回执的 `business_result`，保留 source、captured_at、notes
及缺失笔记 ID。经理沿用 outbox 回收，控制台按机器、槽位、笔记 ID 关联发布记录，
每天展示最后一次快照。部分字段或目标笔记未取得时保留 PARTIAL_SUCCESS 和缺失说明。
本功能按用户任务更新；执行结束后停止。界面展示上次采集时间。

## 竞品分析流程

```text
确认关键词或竞品账号
→ 搜索相关笔记
→ 选择 3–5 篇代表内容
→ 获取详情
→ 对比标题、封面、正文结构、标签和互动数据
```

```powershell
python scripts/cli.py --account <账号别名> search-feeds `
  --keyword "目标关键词" `
  --sort-by 最多点赞
```

输出 Markdown 表格，并总结共性、差异和可执行建议。

## 热点追踪

```powershell
# 观察近期内容
python scripts/cli.py --account <账号别名> search-feeds `
  --keyword "关键词" `
  --sort-by 最新 `
  --publish-time 一周内

# 观察高互动内容
python scripts/cli.py --account <账号别名> search-feeds `
  --keyword "关键词" `
  --sort-by 最多点赞
```

反馈关键词热度、代表内容、内容模式和选题建议。

## V1.5 内容创作与发布目标

```text
研究话题
→ 生成标题、正文、素材和标签草稿
→ 填写发布表单
→ 正式发布
→ 平台回读与 Outbox 回执
```

按经理下发的正式发帖目标执行，成功回读后计数。

## 互动管理

明确要求“首页点开若干篇，其中点赞若干篇”时，使用一次复合命令：

```powershell
python scripts/cli.py --account <账号别名> home-engagement `
  --browse-count 6 `
  --like-count 2 `
  --duration-minutes 12 `
  --min-read-seconds 15 `
  --max-read-seconds 45
```

该流程只打开首页一次，互动对象必须来自本次成功点开的笔记；卡片临时消失时记录跳过并继续，
不会重新搜集一批目标。结果按篇保留 Feed ID、XSEC Token、阅读秒数、点赞状态与失败原因。

运营默认阅读时间为每篇随机 15–45 秒。每次点赞后随机冷却 90–180 秒，且再次点赞前
至少完成 2 篇不点赞的浏览。同账号下一批任务继承间隔状态。任务时限应覆盖浏览与等待；
详见 `docs/EMPLOYEE-EXECUTION-RULES.md`。

点赞点击后等待平台回读确认，结果未知时停止该批次并保留已完成数量。
时间或可用笔记不足时按实际数量返回部分完成。

经理 Agent 下发完成并收到接收确认后结束本轮操作，由员工机独立执行和回传数据。
控制台通过已有回收服务汇总；需要主动检查时由用户提出，异常和结果未知按回执处理。

`home-engagement` 不生成或发送评论。需要评论时，Agent 从已浏览结果中选定目标，先调用
`get-feed-detail` 读取标题、正文、标签和可用图片文字，再生成与上下文一致的最终文本并调用
`post-comment`。评论发送后必须回读平台结果，无法确认时记录 `RESULT_UNKNOWN`，不重复发送。

```text
确认账号和互动目标
→ 搜索并读取笔记
→ 生成与内容相关的评论
→ 用户点击创建当前任务
→ V1.5 直接发送当前评论
→ 可选点赞或收藏
```

随机评论仍限定当前账号和本次 1–3 条。自动回复必须先启用账号规则，并受渠道开关、
暂停和执行记录约束。

## 数据回收

`collect-note-comments` 只读取当前账号自己笔记的评论，使用平台评论 ID 去重，并把新增评论保存为
被动事件。`collect-private-messages` 只读取最近一对一会话，保留对方最新连续发送的一轮文字，并跳过
自己最后发言和非文字内容。两类事件都按平台标识去重。

需要单条回复候选内容时，对一个明确事件运行 `generate-reply-draft`。评论使用笔记和评论线程上下文，私信
使用独立提示词和近期会话上下文；两者都可加载账号人格和知识文件。模型未配置或返回无效结构时
不创建草稿，也不会退回固定模板发送。

`collect-operations-metrics` 保存账号及自己笔记的指标快照。历史和相邻快照增量保存在本地
SQLite；缺失指标保持为空。真实页面采集尚未验收时，必须明确标记为待验收。

## 失败处理

- **账号未 READY**：停止工作流，先恢复账号。
- **搜索或详情失败**：保留已经取得的结果并报告失败步骤。
- **发布目标未启用**：保留草稿，不调用一步发布或外部工具。
- **评论任务范围不明确**：先确定账号、目标和文本，不把一次任务扩展成持续互动。
- **结果未知**：先人工检查平台实际状态，不重复执行对外操作。
