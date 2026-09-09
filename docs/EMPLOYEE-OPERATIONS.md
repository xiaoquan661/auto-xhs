# 员工机运营数据与共享计划

员工机管理面板从经理机缓存读取账号配置、每日计划、执行回执和入站数据。经理 Agent
通过共享计划安排执行，页面保存计划后即可由其他浏览器和 Agent 读取。

## 接口

| 方法 | 路径（前缀 `/api/v1/manager`） | 用途 |
| --- | --- | --- |
| GET | `/dashboard-snapshot` | 总览快照，包含扩展账号字段及 `employee_summary` |
| GET | `/employee-summary` | 员工机账号运营信息、逐日指标和昨日日报 |
| GET | `/daily-plans` | 已保存的每日计划 |
| POST | `/daily-plans` | 保存一个日期的账号计划，递增版本 |
| POST | `/daily-plans/generate` | 根据阶段、压力、健康与采集证据生成草稿 |
| POST | `/daily-plans/preview` | 读取指定日期、版本的账号执行材料 |
| POST | `/daily-plans/submit` | 将已保存版本提交到经理执行队列 |
| POST | `/daily-plans/{date}/prepare` | 兼容入口，使用同一个版本化提交实现 |
| POST | `/operation-tasks/{id}/daily-inputs` | 追加每日任务的发布素材、回复及关注博主目标 |
| PATCH / POST | `/machines/{machine_id}/accounts/{account_slot}/operations` | 更新账号运营配置 |
| PATCH / POST | `/results/{record_id}/attribution` | 保存动作的机器／人工归属 |

写接口沿用 WebUI 的会话令牌机制。账号配置保存在经理机账号记录的 `operations_config`
中；只更新请求中提供的字段。经理机更新员工机连接快照时会保留该配置。

## 账号运营配置

```json
{
  "stage": "稳定运营",
  "pressure": "低压",
  "operating_since": "2026-09-01",
  "publish_audience": "摄影爱好者",
  "comment_range": "3–5",
  "publish_rules": {
    "daily_limit": 2,
    "min_interval_minutes": 180
  },
  "exception_policy": "出现手机验证、人脸验证或安全检查时暂停，通知经理处理"
}
```

`operating_since` 用于计算运营天数；账号阶段和压力优先采用当天计划，随后采用账号配置。
发帖受众优先使用账号配置，其次使用当前绑定人设的 `audience`。
发帖总量、间隔、时间窗口及结构化 `exception_rules` 由经理和员工两端检查。
`exception_policy` 是补充说明；实际按状态码暂停的规则使用 `exception_rules`。
每日任务另有 `daily_policy`：默认北京时间 09:00–22:00、动作间隔 300 秒、替换轮数 4、
主页发帖参考上限 7，默认检查评论入站；私信检查需明确开启。

```json
{
  "daily_policy": {
    "start_time": "09:00",
    "end_time": "22:00",
    "action_spacing_seconds": 300,
    "replacement_round_limit": 4,
    "profile_post_cap": 7,
    "process_inbound_comments": true,
    "process_inbound_messages": false
  }
}
```

每日窗口支持跨午夜，例如 `23:00` 到 `02:00`。账号已有 `time_windows` 同时适用，
请保持两个窗口配置相容。发布规则未配置时，每日材料采用每日 1 篇、间隔 2880 分钟的建议值。

经理 Agent 可通过统一 CLI 更新配置及读取汇总：

```powershell
python C:\Users\EDY\Documents\Codex\2026-07-31\readme-md\xiaohongshu-skills\scripts\manager_cli.py employee configure --machine-id PC001 --account alpha --input-file C:\ops\account-operations.json
python C:\Users\EDY\Documents\Codex\2026-07-31\readme-md\xiaohongshu-skills\scripts\manager_cli.py employee summary
```

`PC001`、`alpha` 和输入文件路径应替换为已登记的机器、账号槽位与实际配置文件。

## 每日计划

每个账号行支持 `participating`、`stage`、`pressure`、`notes` 和 `metrics`。
指标包括 `browse`、`like`、`comment`、`reply`、`publish`、`friend_request`、`new_followers`、
`received_likes`、`received_comments`、`received_private_messages`。

数量为非负整数或 `null`；`null` 表示未设置，`0` 表示明确设置为零。再次保存同一天计划时，
同一账号未提交的指标、阶段和压力沿用已保存值，兼容每日计划页面与员工机面板的不同编辑字段。
提交的账号列表构成该日期的完整计划。

员工机面板的保存按钮调用共享接口。历史浏览器模板在该日期还没有共享计划时作为待保存草稿
载入，用户保存后进入经理机存储。复制模板功能使用当前计划数量。

### 生成、提交和执行

智能建议是草稿，按账号阶段与评论区间选择稳定数量，点赞约为评论的三分之一；账号异常或
处于观察阶段时停止参与。主页总量和发帖日期不足以支持判断时，发帖建议为零。
关注博主的建议数量按已有暖互动对象数量限制。增长数据作为观察指标，不作为必须完成的动作。
手工填写点赞目标时，浏览预算应不少于点赞数量；未填写的指标在执行材料中按零处理。

保存记录历史版本；提交时冻结账号 UID、平台、人设、预算、窗口、规则和当日已有动作基数，
为每个参与账号原子创建一组目标和运营任务。重复提交或调用兼容 `prepare` 返回同一组任务。
已提交日期的新草稿保持可编辑，但执行与日报使用已提交版本；追加目标和素材使用原任务编号。

```json
{"plan_date": "2026-09-09", "version": 1}
```

上面的输入用于 `daily-plan preview --input-file <绝对路径>` 和
`daily-plan submit --input-file <绝对路径>`；前者读取材料，后者会进入实际执行队列。
`daily-plan generate --input-file <绝对路径>` 只需 `plan_date`，可附带账号阶段与压力草稿。

提交时可附带 `action_inputs[机器编号:账号槽位]`，其 `publish`、`reply`、`friend_request`
分别为素材或目标对象数组。也可通过 `daily-plan inputs --operation-task-id <编号>
--input-file <绝对路径>` 补齐输入，此时文件顶层的 `action_inputs` 直接为上述三个数组。
追加时提交该指标的完整数组，已有项保持相同，新项放在末尾，总量不超过冻结预算。

- 发布项：`title`、`content`、`assets`，可选 `kind=image|video`。素材路径是员工机可访问的路径。
- 回复项：`content` 及真实笔记评论目标，或通知上下文；沿用现有回复参数。
- 关注博主项：`user_id`、`xsec_token`，且目标必须出现在该账号冻结的暖互动证据中。

经理按回执扣减预算，逐次派发浏览、点赞与评论，入站和指标采集单独记账。已提供的发布、回复、
关注博主输入复用普通经理规划器；缺少输入时保留待办，不阻止其他已具备条件的指标执行。
发布准备进入真实预览确认，最终发布仍由用户明确确认；预览、草稿不算完成发帖。
人设快照进入执行上下文；内置随机评论仍使用现有正文模板生成器，并不等同于员工模型自主创作。

结果未知的动作预留对应剩余预算，已确认部分单独计入；已尝试笔记从后续候选中排除。
员工执行前重新检查窗口和实时 UID，最终确认发布也受窗口约束。跨日回执按原计划日期核销。
任务详情展示会话状态、计划版本、剩余预算、未知结果预留和输入待办。

## 数据来源与统计口径

所有日期按 `Asia/Shanghai` 归类。执行数量使用每次执行的最新修订，事件按机器、账号槽位、
事件类型和平台事件标识去重。账号和笔记快照、评论及私信采集批次在员工机结果链路中展开，
经理机也兼容历史批次结构。

| 面板内容 | 数据来源与含义 |
| --- | --- |
| 发帖、点赞、评论 | 成功动作回执；发布准备与草稿回执不计为发布，完整发布流程采用已核验的发布证据 |
| 关注博主 | 小红书 `follow-user` 的新增关注动作，只有 `changed=true` 计数；已经关注的回读仍可在明细查看 |
| 新增粉丝、收获点赞 | 相邻日期、同一平台账号的最新快照之差，标注“采样净增”与采样起止时间；允许净减少 |
| 收到新评论、收到私信 | 当日已采集的唯一入站事件数量，标注“已采集”，代表已观测数据 |
| 已采集主页帖子 | 最新账号指标快照中的 `notes`，即主页采集样本数量 |
| 上次发帖日期 | 当前缓存中最近成功发布回执的日期 |
| 暖互动线索 | 跨日评论、回复、私信及成功外发评论中的去重用户，排除已确认关注的对象 |
| 今日已采集会话 | 当日已采集私信事件中的去重会话数 |
| 评论回复提醒 | 入站评论正文、父评论、用户、处理状态及笔记位置 |
| 关注博主明细 | 关注回读，以及已回传的关注者／好友事件 |
| 私聊明细 | 入站私信正文、对方、处理状态和已回传的会话链接 |

采集不到的值为 `null`，页面显示“—”。采样净增和入站事件数量附带来源信息，不能据此认定
平台全天数据已完整收齐。私信只有回执提供了可用会话链接时才展示会话跳转。

## 昨日日报

日报对比已保存计划与最新执行回执，提供逐账号目标／实际、外部动作、完成率、异常数量及分组对比。
已提交计划优先采用冻结的计划版本。浏览、发帖、点赞、评论、回复为硬指标，完成率按每项
`min(实际, 目标)` 加总后除以目标总数；关注博主和增长指标单列。单项超额不会抵消另一项缺口。
“外部动作”统计回执明确标记 `execution_source=human` 的发帖、点赞、评论与关注动作。
执行者下拉框通过归属接口提交 `item_index` 和 `execution_source`（`machine` 或 `human`）。
标注保存在独立的 `action_attributions` 集合，关联指定回执修订与动作序号，记录修改时间和
上一归属。页面显示“归属标注”，日报采用该归属；原始回执及历史修订保持原记录。

压力字段为“高压”或“高压组”时进入高压组，为“低压”或“低压组”时进入低压组；其他值保留在
账号行中。完成率只统计有明确目标、对应动作回执完整且目标合计大于零的账号；平均完成率
为这些账号完成率的算术平均值。异常率为分组内异常回执数除以已回收回执数。
分组同时显示纳入对比的账号数，只有两组都有完成率时生成数值对比结论。

## 验证

`tests/test_employee_operations.py` 覆盖计划与配置持久化、采集批次展开、跨机器账号隔离、
重复事件、北京时间归日、快照净增、关注变更计数、回执修订和分组日报。
`tests/test_employee_panel.cjs` 覆盖共享计划保存及页面回显；经理机现有回归覆盖路由、回收和展示。
真实账号数据验收与部署分别记录。
