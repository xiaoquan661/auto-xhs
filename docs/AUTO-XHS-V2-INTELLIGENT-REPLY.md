# AUTO-XHS V2.0 智能回复

状态：第一阶段代码和自动化测试已完成；AI 只生成待确认草稿，模型连通与真实账号验收尚未执行，后台自动发送未开放。

## 1. 目标

V2.0 把现有“采集评论并执行指定文本”升级为“理解上下文、生成账号化回复、人工确认、执行回读”的智能回复链。

核心目标不是通过随机延时或随机表情模拟真人，而是让每条回复能够：

- 回应评论中的具体问题或情绪；
- 结合笔记正文、父评论和账号知识；
- 保持同一账号长期稳定的表达方式；
- 不编造价格、效果、地点、库存和业务承诺；
- 避免近期重复和明显的客服、AI 模板表达；
- 保留人工修改、确认和外发结果。

## 2. 外部参考与引入方式

设计参考 [Yht20927/xiaohongshu-cli](https://github.com/Yht20927/xiaohongshu-cli) 的独立 ReplyEngine、上下文组装、账号人格、提示词和历史语料思路。该项目使用 MIT 许可证。

本项目不引入其 Node Bridge、油猴脚本、账号登录、评论发布或自动浏览实现，也不建立第二套小红书执行链。所有平台操作继续由本项目的 Python CLI、Bridge、通用扩展和绑定 Chrome Profile 完成。

## 3. 第一阶段范围

第一阶段实现评论回复，第二阶段扩展到私信回复：

1. 评论采集时保留笔记标题、正文、标签、父评论及评论者信息；
2. OpenAI-compatible 模型生成结构化回复候选；
3. 可选加载账号风格文件和账号知识文件；
4. 对过长、典型 AI 表达和近期完全重复进行质量标记；
5. 生成结果保存为现有 `reply-comment` 待确认草稿；
6. WebUI 确认中心显示待处理新评论和 AI 草稿；
7. WebUI 可以选择 READY 账号并执行一次只读评论采集；
8. Agent/Python CLI 可以为明确事件生成草稿；
9. 所有 AI 草稿仍需人工核对后才能调用现有回复执行器。
10. 从 `/chat` 的真实一对一会话项读取对方 UID、昵称、时间和摘要；
11. 逐会话读取近期消息，只采集最后一条由对方发送的文字；
12. 私信上下文进入独立提示词，生成 `send-private-messages` 待确认草稿；
13. WebUI 确认后回到对应会话发送，并沿用现有发送后消息气泡回读。

第一阶段不实现：

- 后台无人值守自动发送；
- 自动处理投诉、争议、合作和业务承诺；
- 私信后台无人值守自动回复；
- 用外部项目替换当前浏览器执行层；
- 在 `RESULT_UNKNOWN` 后自动重发。

## 4. 数据流

```text
collect-note-comments
→ comment_monitor 读取笔记和评论线程
→ comment_collector 写入 note_comment 入站事件
→ ReplyIntelligenceService 组装上下文
→ OpenAI-compatible LLM 返回 reply / intent / confidence / reason
→ 质量标记和近期回复查重
→ PassiveReplyService 创建 WAITING_APPROVAL 任务和 DRAFT 草稿
→ WebUI 或 Agent 人工编辑、确认
→ 现有 reply-comment 执行
→ 页面结果回读和任务终态
```

私信链路：

```text
collect-private-messages
→ private_inbox 读取真实会话列表和近期消息
→ private_message_collector 写入 private_message 入站事件
→ ReplyIntelligenceService 使用私信提示词和近期上下文
→ PassiveReplyService 创建 reviewed_reply / WAITING_APPROVAL
→ WebUI 人工核对并确认
→ 现有 send-private-messages 执行器发送并回读
```

## 5. 模型配置

WebUI 服务和 CLI 从环境变量读取独立的智能回复模型配置：

```powershell
$env:XHS_REPLY_LLM_API_KEY='你的 API Key'
$env:XHS_REPLY_LLM_BASE_URL='https://你的兼容服务/v1'
$env:XHS_REPLY_LLM_MODEL='模型名称'
$env:XHS_REPLY_LLM_TIMEOUT_SECONDS='60'
```

三个核心变量缺少任意一个时，服务返回 `LLM_NOT_CONFIGURED`，不会退回固定模板冒充 AI 回复。

也可以在 WebUI“系统诊断 → 智能回复模型”中填写并保存同样的配置。WebUI 配置保存在
`%USERPROFILE%\.xhs\auto-xhs\product-state.json`，不会写入项目仓库；API 和页面只返回是否
已配置、接口地址及模型名称，不会回显完整 API Key。WebUI 保存值优先，环境变量作为缺省后备。
“测试连接”只发起一次最小模型请求，不采集评论，也不触发小红书操作。
当前 WebUI 默认填写 DeepSeek OpenAI-compatible 地址 `https://api.deepseek.com` 和
`deepseek-v4-flash`，仍可手动改成其他兼容服务。

## 6. 账号人格与知识

默认目录：

```text
%USERPROFILE%\.xhs\auto-xhs\reply-profiles\
  brand-a.md
  brand-a.knowledge.md
```

- `<slot>.md`：账号身份、称呼习惯、语气、长度偏好和不使用的表达；
- `<slot>.knowledge.md`：可以公开回答的产品、活动、地点、价格和流程事实；
- 可以用 `XHS_REPLY_PROFILE_DIR` 指向其他本地目录；
- 没有知识依据时，模型必须自然说明信息不足，不得自行补全事实。

同一账号使用固定人格；回复长度和句式可以自然变化，但不随机切换成彼此冲突的人设。

## 7. Agent/CLI 入口

先只读采集新评论：

```powershell
python scripts/cli.py --account brand-a collect-note-comments
```

对一个明确事件生成待确认草稿：

```powershell
python scripts/cli.py --account brand-a generate-reply-draft `
  --event-id EVENT_ID `
  --verified-uid CURRENT_VERIFIED_UID
```

也可以临时指定资料文件：

```powershell
python scripts/cli.py --account brand-a generate-reply-draft `
  --event-id EVENT_ID `
  --verified-uid CURRENT_VERIFIED_UID `
  --account-profile-file "C:\ReplyProfiles\brand-a.md" `
  --knowledge-file "C:\ReplyProfiles\brand-a.knowledge.md"
```

该命令只生成本地草稿，不向小红书发送内容。

## 8. 本地 API 与 WebUI

- `GET /api/v1/reply-intelligence/status`：查看模型是否配置；
- `POST /api/v1/reply-intelligence/settings`：保存本机模型配置；
- `POST /api/v1/reply-intelligence/test`：显式测试一次模型连接；
- `POST /api/v1/inbound-events/{event_id}/intelligent-reply-draft`：生成并保存待确认草稿；
- WebUI“确认中心 → 新评论智能回复”展示尚未创建任务的新评论；
- WebUI 通过 Profile、昵称和 UID 选择 READY 账号，内部自动使用账号槽位标识；
- 点击“采集新评论”复用现有 `collect-note-comments` 任务，并展示扫描、新增、重复和失败数量；
- 点击“生成 AI 回复草稿”只调用生成接口；
- 生成后的草稿继续使用现有确认与执行链。

## 9. 输出结构

模型必须返回一个 JSON 对象：

```json
{
  "reply": "最终回复草稿",
  "intent": "question",
  "confidence": 0.82,
  "reason": "评论在询问笔记中已经说明的报名方式"
}
```

系统额外记录：

- `quality_flags`：过长、典型 AI 表达、近期完全重复；
- `manual_review_required=true`；
- 使用的模型；
- 是否加载账号人格和知识；
- 笔记、评论和父评论的上下文摘要。

模型置信度只用于帮助人工判断。第一阶段不会因为分数较高而自动发送。

## 10. 验收标准

第一阶段完成需要满足：

- 新采集的评论事件保留笔记正文和父评论上下文；
- WebUI 采集入口不要求用户手动填写 `brand-a` 一类内部槽位标识；
- 非 READY 或正在执行其他任务的账号不能开始评论采集；
- 未配置模型时明确失败，不生成固定模板；
- AI 返回结构错误或空文本时不创建草稿；
- 同一事件最多创建一个回复任务和一个草稿；
- 草稿能展示回复、意图、置信度及质量提示；
- 创建草稿不会触发任何小红书写操作；
- 人工确认后仍由现有 `reply-comment` 和结果回读链负责外发；
- 自动化测试覆盖生成、解析、上下文、去重、API 和 CLI 路由；
- 真实账号生成与发送验收必须另行授权。

## 11. 后续阶段

第二阶段计划记录 AI 原稿、人工修改稿和拒绝原因，形成每个账号的历史表达语料与重复控制。

第三阶段才讨论规则化自动回复：用户按账号显式启用，设置生效时间和数量，仅对低风险、高置信度场景自动执行；投诉、争议、合作、售后和信息不足继续人工处理。
