---
name: xhs-interact
description: |
  小红书评论、随机评论、回复、点赞和收藏技能。当用户要求对指定笔记评论、
  从首页随机评论、回复评论、启用自动回复规则、点赞或收藏时使用。
metadata:
  openclaw:
    requires:
      bins:
        - python
        - uv
    emoji: "\U0001F4AC"
---

# 小红书社交互动

只通过本项目的 `python scripts/cli.py` 执行互动。

## 版本状态

- V1.0 当前指定评论和回复仍使用现有确认链；随机评论已经支持当前点击授权 1–3 条。
- V1.5 目标改为：指定评论由当前任务点击后直接发送，自动回复按账号规则运行且不逐条确认。
- V1.5 评论和自动回复链完成前，不绕过当前服务层确认，也不把单次 `reply-comment` 描述为
  已经具备后台自动回复。

## 允许命令

| 子命令 | 用途 |
|---|---|
| `post-comment` | 对指定笔记发送一条评论 |
| `random-comment` | 从首页推荐随机选择 1–3 篇并直接评论 |
| `reply-comment` | 回复指定评论或用户 |
| `like-feed` | 点赞或取消点赞 |
| `favorite-feed` | 收藏或取消收藏 |

自动回复的规则管理、轮询或事件入口尚未实现；不得猜测命令。

## 强制约束

- 多账号环境把 `--account <账号别名>` 放在子命令之前，并在互动前核验 UID。
- 评论任务的授权只覆盖当前账号、当前任务、当前目标和当前文本。
- 随机评论一次授权数量只能是 1–3 条，每条分别记录目标、文本和结果。
- 评论文本不能为空，不能把一次点击扩展成持续评论计划。
- 点赞和收藏遵守配额、去重、间隔和熔断规则。
- 遇到 `RESULT_UNKNOWN` 时先人工检查平台实际状态，不直接重发。

## V1.5 指定评论

用户在 WebUI 或 Codex 中完成账号、目标和文本填写并点击创建任务后，该点击授权当前评论直接发送，
不再增加第二次逐条确认：

```powershell
python scripts/cli.py --account <账号别名> post-comment `
  --feed-id FEED_ID `
  --xsec-token XSEC_TOKEN `
  --content "评论内容"
```

代码阶段完成前，继续服从 V1.0 当前确认链。

## 随机评论

当前点击一次性授权本账号、本任务 1–3 条：

```powershell
python scripts/cli.py --account <账号别名> random-comment `
  --count 3 `
  --candidate-pool-size 20 `
  --collect-minutes 2 `
  --style natural
```

任务结束后逐条反馈笔记、实际评论文本和成功、失败或结果未知状态。

## 单次回复

```powershell
python scripts/cli.py --account <账号别名> reply-comment `
  --feed-id FEED_ID `
  --xsec-token XSEC_TOKEN `
  --comment-id COMMENT_ID `
  --content "回复内容"
```

单次回复命令不等于自动回复系统。

## V1.5 自动回复目标

用户先按账号启用自动回复规则。规则至少包含：

- 适用账号和内容范围；
- 回复风格或模板；
- 生效时间范围；
- 每小时和每日数量；
- 暂停与恢复开关。

启用后，符合规则的回复不逐条确认；每条必须保存原评论、回复文本、账号、时间和结果。代码、WebUI、
审计和实机验收完成前，只能说明这是 V1.5 目标，不执行后台自动回复。

## 点赞与收藏

```powershell
# 点赞
python scripts/cli.py --account <账号别名> like-feed `
  --feed-id FEED_ID --xsec-token XSEC_TOKEN

# 取消点赞
python scripts/cli.py --account <账号别名> like-feed `
  --feed-id FEED_ID --xsec-token XSEC_TOKEN --unlike

# 收藏
python scripts/cli.py --account <账号别名> favorite-feed `
  --feed-id FEED_ID --xsec-token XSEC_TOKEN

# 取消收藏
python scripts/cli.py --account <账号别名> favorite-feed `
  --feed-id FEED_ID --xsec-token XSEC_TOKEN --unfavorite
```

## 失败处理

- **账号未就绪或 UID 不一致**：停止互动，先恢复账号身份。
- **笔记或评论不可访问**：报告真实状态，不更换目标重试。
- **评论发送失败**：展示平台或页面错误，不自动修改文本重发。
- **结果未知**：人工检查后再决定，不直接重复发送。
- **自动回复未实现**：明确报告 V1.5 规格已批准、执行链待实现。
