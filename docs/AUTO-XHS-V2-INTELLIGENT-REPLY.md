# AUTO-XHS V2.0 智能回复周期

状态：评论和入站私信的统一回复周期已完成代码与自动化测试；真实账号自动回复周期仍需单独授权验收。

## 1. 能力边界

| 能力 | 触发来源 | 执行方式 |
|---|---|---|
| 主动评论 | 用户指定笔记或运营任务 | Codex 在本次任务范围内直接执行并回读 |
| 主动私信 | 用户指定目标或运营任务 | 最终文本可直接执行；Codex 生成或改写的批次先整批确认 |
| 评论回复 | 自己笔记出现新评论 | `reply-cycle` 按账号规则自动回复或进入待处理 |
| 私信回复 | 对方发来新私信 | `reply-cycle` 按账号规则自动回复或进入待处理 |

主动外发任务和入站回复使用独立的触发入口，共用现有浏览器执行、身份核验、任务终态和页面回读能力。

## 2. 账号规则

每个账号规则包含：

- 总开关，以及评论回复、私信回复两个独立开关；
- 生效时段、每小时上限、每天上限；
- 评论范围，默认只处理自己笔记下的评论；
- 回复风格、账号人设和可确认知识；
- 自动回复类别和待处理类别。

默认自动回复类别：普通问题、感谢或夸赞、一般讨论、知识中已有明确答案的咨询。

默认待处理类别：投诉或争议、合作、报价、库存、退款、联系方式、线下见面、业务承诺和上下文或知识不足。

## 3. 有界回复周期

`reply-cycle` 每次只处理指定上限内的新事件：

```text
采集新评论和新私信
→ 按平台事件 ID 去重
→ 补充笔记、评论线程或近期会话上下文
→ 模型生成 reply / intent / facts_used / missing_information
→ 账号规则判定 reply / pending / skip
→ 只发送 reply
→ 页面回读
→ 写入周期、决策、任务和回复记录
```

周期可以由 Codex/计划任务调用，也可以在 WebUI 中手动运行。单次调用结束后退出，不在进程内无限轮询。

## 4. 评论回复

- 只处理明确标记为“评论了你的笔记”的通知事件；回复自己评论、提及和其他笔记事件会跳过；
- 生成上下文包含笔记标题、正文、标签、当前评论、父评论和评论者；
- 同一平台事件只处理一次；已有人工接管任务的事件不会进入自动周期；
- 回复通过通知卡片发送，并以页面确认状态作为成功证据；
- 缺少页面回读时终态为 `RESULT_UNKNOWN`。

## 5. 私信回复

- 只采集一对一文字会话；
- 对方最新连续发送的多条消息合并为一次回复单位；
- 生成上下文保留近期双方消息和最新对方轮次；
- 发送前重新读取会话。最新对方轮次与生成时不一致时，本次不发送，并进入待处理；
- 发送后必须读取到新的己方消息气泡才记为 `SUCCESS`；
- `RESULT_UNKNOWN` 不会自动重发。

## 6. 模型输出

模型返回结构：

```json
{
  "reply": "最终回复候选",
  "intent": "question",
  "confidence": 0.9,
  "reason": "使用了笔记中的明确报名说明",
  "facts_used": ["报名步骤见笔记正文"],
  "missing_information": []
}
```

过长、明显 AI 表达和近期完全重复会形成质量标记。重复回复直接跳过，其他质量问题进入待处理。

## 7. CLI、API 与 WebUI

CLI：

```powershell
python scripts/cli.py --account brand-a reply-cycle --max-events 20

python scripts/cli.py --account brand-a reply-cycle `
  --comments-only --max-events 10

python scripts/cli.py --account brand-a reply-cycle `
  --private-messages-only --max-events 10
```

本地 API：

- `GET /api/v1/reply-rules`
- `POST /api/v1/reply-rules`
- `PATCH /api/v1/reply-rules/{rule_id}`
- `POST /api/v1/reply-rules/{rule_id}/enabled`
- `POST /api/v1/reply-cycle`
- `GET /api/v1/reply-cycles`
- `GET /api/v1/reply-decisions`
- `GET /api/v1/data-recovery/reply-records`

WebUI“账号回复规则”提供规则编辑、立即运行一轮、待处理内容和自动回复记录。原有评论与私信草稿入口保留为单条人工接管流程。

## 8. 结果状态

- `SUCCESS`：页面已回读到明确发送结果；
- `FAILED`：页面明确失败或发送前检查失败；
- `RESULT_UNKNOWN`：已经触发发送，但未取得确定回读；
- `pending`：需要业务判断、缺少知识、超过时段或配额、质量检查未通过，或私信会话已变化；
- `skip`：不在规则范围、渠道关闭或重复回复。

外发结果不确定时先检查平台当前状态，不自动重发。

## 9. 验收状态

- 数据库迁移、规则服务、周期与决策记录：`AUTOMATED_TESTED`；
- 评论采集、上下文生成、规则判定、发送回读记录：`AUTOMATED_TESTED`；
- 私信连续轮次、发送前版本校验、气泡回读和不重发：`AUTOMATED_TESTED`；
- CLI、API 和 WebUI 路由：`AUTOMATED_TESTED`；
- 真实账号自动回复周期：待单独授权验收。
