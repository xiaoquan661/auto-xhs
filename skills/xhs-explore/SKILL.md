---
name: xhs-explore
description: |
  小红书内容发现与分析技能。搜索笔记、浏览首页、读取笔记详情和评论、获取用户主页。
  当用户要求搜索、查看笔记、浏览推荐内容、读取评论或分析博主时使用。
metadata:
  openclaw:
    requires:
      bins:
        - python
        - uv
    emoji: "\U0001F50D"
---

# 小红书内容发现

只通过本项目的 `python scripts/cli.py` 执行小红书读取操作。当前正式支持 Windows 10/11。

## 允许命令

| 子命令 | 用途 |
|---|---|
| `list-feeds` | 获取首页推荐 Feed |
| `browse-feeds` | 按时间和数量浏览首页笔记 |
| `search-feeds` | 关键词搜索并筛选笔记 |
| `get-feed-detail` | 获取笔记完整内容和评论 |
| `user-profile` | 获取用户主页信息 |

## 强制约束

- 多账号环境把 `--account <账号别名>` 放在子命令之前。
- 读取前确认槽位 READY；不同账号可以并发，同一账号必须串行。
- `feed_id` 和 `xsec_token` 必须来自同一条搜索或首页结果，不能混用。
- 控制连续详情读取数量，避免短时间加载大量笔记或评论。
- 结果使用结构化字段或 Markdown 表格反馈。

## 首页推荐

```powershell
python scripts/cli.py --account <账号别名> list-feeds
```

按时间和数量浏览首页：

```powershell
python scripts/cli.py --account <账号别名> browse-feeds `
  --duration-minutes 10 `
  --count 8
```

反馈实际浏览数量、耗时、结束原因，以及每篇笔记的标题和作者。

## 搜索笔记

```powershell
python scripts/cli.py --account <账号别名> search-feeds `
  --keyword "春招" `
  --sort-by 最多点赞 `
  --note-type 图文 `
  --publish-time 一周内 `
  --search-scope 未看过
```

| 参数 | 可选值 |
|---|---|
| `--sort-by` | 综合、最新、最多点赞、最多评论、最多收藏 |
| `--note-type` | 不限、视频、图文 |
| `--publish-time` | 不限、一天内、一周内、半年内 |
| `--search-scope` | 不限、已看过、未看过、已关注 |
| `--location` | 不限、同城、附近 |

输出包含 `feeds` 和 `count`。展示标题、作者、互动数据、`id` 和对应 `xsec_token`。

## 获取笔记详情和评论

```powershell
python scripts/cli.py --account <账号别名> get-feed-detail `
  --feed-id FEED_ID `
  --xsec-token XSEC_TOKEN `
  --load-all-comments `
  --click-more-replies `
  --max-replies-threshold 10 `
  --max-comment-items 50
```

批量读取时每组不超过 3 篇，组间在 PowerShell 中等待 10–20 秒：

```powershell
Start-Sleep -Seconds (Get-Random -Minimum 10 -Maximum 21)
```

不要把所有详情命令无间隔串联。

## 获取用户主页

```powershell
python scripts/cli.py --account <账号别名> user-profile `
  --user-id USER_ID `
  --xsec-token XSEC_TOKEN
```

反馈用户基本信息、粉丝/关注数和代表笔记。

## 结果呈现

1. 搜索列表展示标题、作者和互动数据。
2. 详情展示正文、图片、互动数据和评论。
3. 用户主页展示基本信息和代表作。
4. 多条结果使用 Markdown 表格对比。

## NetLog 诊断

只在用户要求检查会话风控数据时使用：

```powershell
python scripts/cli.py --account <账号别名> get-netlog --limit 100
python scripts/cli.py --account <账号别名> risk-report
```

扩展必须已经启用对应诊断能力；未启用时报告真实状态，不主动修改扩展设置。

## 失败处理

- **未登录或账号未 READY**：先恢复账号状态。
- **搜索无结果**：建议更换关键词或筛选条件。
- **笔记不可访问**：报告可能已删除、私密或令牌失效。
- **用户主页不可访问**：报告账号可能注销或限制访问。
- **连续读取受限**：停止本组读取，延长间隔，不立即重复请求。
