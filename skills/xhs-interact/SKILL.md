---
name: xhs-interact
description: |
  小红书主动私信、主加、主动评论、随机评论、点赞和收藏技能。当用户要求发送首次私信、
  继续已有私信会话、主动关注博主、评论、点赞或收藏时使用。新评论和入站私信回复使用 xhs-reply。
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
- V1.5 指定评论由当前任务点击后直接发送；自动回复按账号规则运行且不逐条确认。
- V2.0 入站评论和私信由 `xhs-reply` 使用 Codex 回复收件箱处理。

## 允许命令

| 子命令 | 用途 |
|---|---|
| `post-comment` | 对指定笔记发送一条评论 |
| `random-comment` | 从首页推荐随机选择 1–3 篇并直接评论 |
| `reply-comment` | 回复指定评论或用户 |
| `like-feed` | 点赞或取消点赞 |
| `favorite-feed` | 收藏或取消收藏 |
| `follow-user-preview` | 可选的只读目标状态诊断，不执行关注 |
| `follow-user` | 在当前 Agent 任务内预览、关注一次并回读状态 |
| `private-message-context` | 只读检查首次私信入口或读取已有会话近期文本 |
| `collect-private-messages` | 只读回收最近一对一会话中对方最新连续发送的一轮文字 |
| `send-private-messages` | 直接发送 Codex 生成的个性化文本，或恢复批次可重试项 |
| `collect-note-comments` | 只读回收自己最近笔记的新评论事件 |
| `generate-reply-draft` | 为一个明确事件生成待人工确认的智能回复草稿 |
| `reply-cycle` | 按已启用账号规则处理一轮新评论和新私信 |
| `reply-inbox` | 供 Codex 采集、读取上下文并应用一个结构化回复决策 |

Codex 入站回复路由到 `xhs-reply`。`reply-cycle` 作为 API 模型兼容入口，每次只运行一个有界批次。

## 强制约束

- 多账号环境把 `--account <账号别名>` 放在子命令之前，互动直接使用槽位已绑定 UID。首次绑定或手动换号时按 xhs-auth 核验。
- 评论任务的授权只覆盖当前账号、当前任务、当前目标和当前文本。
- 随机评论一次授权数量只能是 1–3 条，每条分别记录目标、文本和结果。
- 评论文本不能为空，不能把一次点击扩展成持续评论计划。
- 点赞和收藏遵守配额、去重、间隔和熔断规则。
- 遇到 `RESULT_UNKNOWN` 时先人工检查平台实际状态，不直接重发。
- 主加属于单向操作，默认直接执行、不需要审批；只有用户特别说明时才在执行前等待确认。
- 主加只由 Agent/Python CLI 下发；WebUI 只展示任务和结果，不创建、确认或执行主加任务。
- 主动私信批次只由 Agent/Python CLI 下发；WebUI 只展示批次与逐人结果。入站私信回复由已启用的
  账号规则周期处理，也可以在 WebUI 单条人工接管。
- 私信单批 1–10 人，多人批次每人文本必须不同；某人失败不阻断其余收件人。
- 用户指定账号、联系人和联系目的后，Codex 必须先读取每人的首次/续发状态和上下文，再生成不同
  文本并直接发送。
- 私信点击后未回读成功记为 `RESULT_UNKNOWN`，不自动重发；恢复批次也不重发成功或结果未知项。

## 私信

先读取上下文以判断是首次私信还是已有会话，并用于个性化写作：

```powershell
python scripts/cli.py --account <账号别名> private-message-context `
  --user-id USER_ID --xsec-token XSEC_TOKEN --limit 10
```

Codex 根据读取结果准备 UTF-8 JSON 数组并直接发送：

```json
[
  {"user_id":"USER_A","nickname":"甲","xsec_token":"TOKEN_A","contact_purpose":"首次沟通合作","conversation_type":"first_message","expected_peer_turn":[],"content":"给甲的最终文本"},
  {"user_id":"USER_B","nickname":"乙","contact_purpose":"延续已有合作沟通","conversation_type":"existing","expected_peer_turn":["对方最近一条消息"],"content":"给乙的不同文本"}
]
```

```powershell
python scripts/cli.py --account <账号别名> send-private-messages `
  --recipients-file "C:\Temp\private-messages.json"
```

部分完成的批次使用原任务 ID 恢复，只会重试 `FAILED` 或 `BLOCKED` 项：

```powershell
python scripts/cli.py --account <账号别名> send-private-messages `
  --resume-task-id TASK_ID
```

首次私信入口是否可用取决于目标主页是否向当前账号展示“私信/发消息”入口。不可用时报告真实状态，
不改用评论等其他联系方式。当前页面适配器已完成只读结构检查，真实发送仍需用户明确授权后验收。

收到的新私信可以由账号规则周期处理，也可以只读采集后人工接管：

```powershell
python scripts/cli.py --account <账号别名> collect-private-messages --max-conversations 20

python scripts/cli.py --account <账号别名> generate-reply-draft `
  --event-id EVENT_ID --verified-uid CURRENT_VERIFIED_UID
```

采集器把最近一对一会话中对方最新连续发送的一轮文字作为一个事件，跳过自己最后发言和非文字
内容。规则周期发送前重新校验会话轮次；人工草稿继续使用既有确认链。无法回读时使用
`RESULT_UNKNOWN`，不自动重发。

## 主加（主动关注博主）

Agent 对每个目标读取主页和当前状态、关注一次并回读结果：

```powershell
python scripts/cli.py --account <账号别名> follow-user `
  --user-id USER_ID --xsec-token XSEC_TOKEN
```

需要单独排查目标状态时，可以使用只读命令：

```powershell
python scripts/cli.py --account <账号别名> follow-user-preview `
  --user-id USER_ID --xsec-token XSEC_TOKEN
```

如果检查时已经关注，执行阶段不会重复点击。点击后只有回读为“已关注”或“互相关注”才记为成功；
无法确认实际结果时返回 `RESULT_UNKNOWN`，不得自动重试。取消关注是独立能力，不在本流程中执行。

## V1.5 指定评论

用户在 WebUI 或 Codex 中完成账号、目标和文本填写并点击创建任务后，该点击授权当前评论直接发送，
不再增加第二次逐条确认：

```powershell
python scripts/cli.py --account <账号别名> post-comment `
  --feed-id FEED_ID `
  --xsec-token XSEC_TOKEN `
  --content "评论内容"
```

Codex 只在用户指定的当前账号、目标和任务范围内执行，不扩展成持续评论计划。

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

## 入站评论和私信回复

用户先按账号启用自动回复规则。规则至少包含：

- 适用账号和内容范围；
- 评论和私信独立开关；
- 回复风格、账号人设和知识；
- 生效时间范围；
- 每小时和每日数量；
- 暂停与恢复开关。

启用后，符合规则的回复不逐条确认；每条保存原内容、回复文本、账号、时间、决策和页面回读结果。

Codex 模式使用回复收件箱：

```powershell
python scripts/cli.py --account <账号别名> reply-inbox collect --limit 20
python scripts/cli.py --account <账号别名> reply-inbox list --state NEW --limit 20
```

后续的逐事件上下文读取、决策文件结构和结果处理遵循 `xhs-reply`。普通问题、感谢、一般讨论和知识
可回答咨询可以自动回复；需要业务判断的内容进入待处理。私信发送前重新读取最新连续对方轮次。

V2.0 评论可以先只读采集，再为一个明确事件生成待确认草稿：

```powershell
python scripts/cli.py --account <账号别名> generate-reply-draft `
  --event-id EVENT_ID --verified-uid CURRENT_VERIFIED_UID
```

该命令不会发送回复；草稿仍需人工核对并进入既有回复确认链。

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
- **规则判断为待处理**：保留候选和原因，由用户在 WebUI 查看后决定后续处理。
