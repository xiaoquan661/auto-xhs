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
- V1.5 目标开放发布、定时发布、私信、资料修改、评论一次点击直发和规则化自动回复。
- 目标能力未完成执行链时，完成分析或草稿后停止，明确报告待实现，不绕过能力注册表。

## 允许组合的命令

| 子命令 | 用途 |
|---|---|
| `search-feeds` | 搜索笔记 |
| `list-feeds` | 获取首页推荐 |
| `get-feed-detail` | 获取笔记详情和评论 |
| `user-profile` | 获取用户主页 |
| `post-comment` | 对指定笔记评论 |
| `random-comment` | 当前点击授权 1–3 条随机评论 |
| `home-engagement` | 单次首页会话完成浏览、点赞、评论并输出逐篇记录 |
| `reply-comment` | 单次回复 |
| `like-feed` | 点赞笔记 |
| `favorite-feed` | 收藏笔记 |
| `fill-publish` / `fill-publish-video` | V1.5 填写发布预览 |
| `long-article` / `select-template` / `next-step` | V1.5 长文预览流程 |
| `click-publish` | V1.5 用户确认真实预览后发布 |
| `save-draft` | V1.5 用户取消发布时保存草稿 |
| `collect-note-comments` | 自动发现自己最近笔记并回收新评论事件 |
| `collect-operations-metrics` | 保存账号和自己笔记的运营指标时间快照 |

一步发布命令不属于 V1.5 产品流程。私信、资料修改和自动回复尚无完整命令时不得猜测。

## 强制约束

- 多账号命令把 `--account <账号别名>` 放在子命令之前。
- 每一步都服从对应子技能：读取参考 `xhs-explore`，发布参考 `xhs-publish`，互动参考
  `xhs-interact`，身份参考 `xhs-auth`。
- 不同账号可以并发，同一账号串行。
- 发布必须浏览器预览确认；评论在 V1.5 代码完成后由当前任务点击直接授权；自动回复必须先按
  账号启用规则。
- 所有步骤记录真实结果，不把草稿、表单填写或排队描述为发布成功。

## 竞品分析

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
→ 用户在浏览器检查真实预览
→ 展示账号、内容、素材、可见范围和定时时间
→ 用户确认
→ 点击发布或保存草稿
```

禁止从研究步骤直接调用一步发布命令。代码阶段完成前，只生成草稿并报告 V1.5 发布链待启用。

## 互动管理

明确要求“首页点开若干篇，其中点赞若干篇并评论若干篇”时，使用一次复合命令：

```powershell
python scripts/cli.py --account <账号别名> home-engagement `
  --browse-count 6 `
  --like-count 2 `
  --comment-count 1 `
  --duration-minutes 3 `
  --min-read-seconds 8 `
  --max-read-seconds 15
```

该流程只打开首页一次，互动对象必须来自本次成功点开的笔记；卡片临时消失时记录跳过并继续，
不会重新搜集一批目标。结果按篇保留阅读秒数、点赞状态、评论文本与失败原因。

```text
确认账号和互动目标
→ 搜索并读取笔记
→ 生成与内容相关的评论
→ 用户点击创建当前任务
→ V1.5 直接发送当前评论
→ 可选点赞或收藏
```

随机评论仍限定当前账号和本次 1–3 条。自动回复必须先启用账号规则，并受时间、配额、暂停和
执行记录约束；代码完成前不执行后台自动回复。

## 数据回收

`collect-note-comments` 只读取当前账号自己笔记的评论，使用平台评论 ID 去重，并把新增评论保存为
被动事件。事件可以生成唯一的回复任务和草稿，确认后复用该任务执行单次回复。

`collect-operations-metrics` 保存账号及自己笔记的指标快照。历史和相邻快照增量保存在本地
SQLite；缺失指标保持为空。真实页面采集尚未验收时，必须明确标记为待验收。

## 失败处理

- **账号未 READY**：停止工作流，先恢复账号。
- **搜索或详情失败**：保留已经取得的结果并报告失败步骤。
- **发布目标未启用**：保留草稿，不调用一步发布或外部工具。
- **评论规则仍为 V1.0**：服从当前服务确认，不能仅凭 V1.5 文档绕过。
- **结果未知**：先人工检查平台实际状态，不重复执行对外操作。
