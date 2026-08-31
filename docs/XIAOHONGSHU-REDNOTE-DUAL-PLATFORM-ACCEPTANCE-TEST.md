# Xiaohongshu 与 RedNote 双平台验收测试清单

版本：v1.2
更新日期：2026-08-26
关联计划：[Xiaohongshu 与 RedNote 双平台改造实施计划](XIAOHONGSHU-REDNOTE-DUAL-PLATFORM-REFACTOR-PLAN.md)

## 1. 当前结论

双平台基础设施已经完成自动化验证，当前基线为：

- 全量自动化测试：`306 passed`；
- Python 编译检查：通过；
- 扩展和 WebUI JavaScript 语法检查：通过；
- 工作区差异格式检查：通过；
- RedNote `profile-7 / Profile 7` 的连接、UID、关键词搜索、用户主页读取和图文发布已执行；
- RedNote 搜索状态兼容和冷启动等待问题已修复，互动能力未执行；
- 首次发布任务曾错误记录为 `xiaohongshu`，已修复 Agent 发布工作流的平台快照传递，并完成自动化回归。

因此当前可标记为“平台基础设施 `AUTOMATED_TESTED`，RedNote 连接、身份、主页读取和图文发布已真实验证”；
不能把 RedNote 互动或全部能力标记为真实验收通过。

## 2. 状态说明

| 状态 | 含义 |
|---|---|
| `PASSED_AUTOMATED` | 已由自动化测试或静态检查验证 |
| `PASSED_REAL` | 已由真实 RedNote 页面或平台数据回读验证 |
| `PENDING_REAL_READ_ONLY` | 需要真实浏览器，但只读取、不改变平台状态 |
| `PENDING_AUTHORIZATION` | 会改变真实账号或产生外部输出，必须单独授权 |
| `BLOCKED_BY_CAPABILITY` | 当前能力为 `UNKNOWN` 或 `UNSUPPORTED`，应被系统阻止 |
| `FAILED` | 结果与预期不符，保留证据并停止后续阶段 |

真实验收必须记录实际证据；仅看到按钮、页面成功打开或自动化测试通过不能替代结果回读。

## 3. 自动化验收项

以下项目不需要连接真实账号。

| 编号 | 验收项 | 通过标准 | 当前状态 |
|---|---|---|---|
| AUTO-01 | PlatformSpec 注册 | Xiaohongshu、RedNote 均能读取独立域名、创作平台和路由 | `PASSED_AUTOMATED` |
| AUTO-02 | 平台识别与 URL 改写 | hostname 识别正确，详情页和创作平台 URL 可定向改写 | `PASSED_AUTOMATED` |
| AUTO-03 | 新账号平台持久化 | 新槽位保存所选平台并在公开配置中显示平台名称 | `PASSED_AUTOMATED` |
| AUTO-04 | 旧账号兼容 | 缺少 `platform` 的旧配置按 Xiaohongshu 读取，且不被自动改写 | `PASSED_AUTOMATED` |
| AUTO-05 | 身份隔离 | 身份比较同时校验平台和 UID，平台不一致返回独立状态 | `PASSED_AUTOMATED` |
| AUTO-06 | Bridge 平台握手 | 错误平台的扩展握手或 CLI 请求被 Bridge 拒绝 | `PASSED_AUTOMATED` |
| AUTO-07 | Chrome 首屏路由 | RedNote 槽位使用 RedNote 首页，Xiaohongshu 保持原首页 | `PASSED_AUTOMATED` |
| AUTO-08 | 任务平台快照 | 任务保存创建时的平台，任务与槽位平台不一致时被阻止 | `PASSED_AUTOMATED` |
| AUTO-09 | 能力矩阵 | RedNote `UNKNOWN`/`UNSUPPORTED` 能力不能进入业务执行器 | `PASSED_AUTOMATED` |
| AUTO-10 | 运营数据平台字段 | 入站事件、游标、指标、动作草稿、回复规则和操作记录保留平台 | `PASSED_AUTOMATED` |
| AUTO-11 | 扩展站点覆盖 | manifest、后台页和网络拦截器同时登记两个平台 | `PASSED_AUTOMATED` |
| AUTO-12 | Xiaohongshu 回归 | 原账号、任务、发布、互动、私信和 WebUI 自动化用例保持通过 | `PASSED_AUTOMATED` |
| AUTO-13 | 配对平台识别 | 配对包必须显式携带平台；当前页面平台不一致时拒绝；旧后台进程提示重载而不默认成小红书 | `PASSED_AUTOMATED` |
| AUTO-14 | Agent 发布任务平台快照 | 图文、视频和长文工作流显式写入槽位平台；RedNote 图文预览任务的 `platform` 为 `rednote` | `PASSED_AUTOMATED` |
| AUTO-15 | RedNote 搜索状态兼容 | 搜索读取兼容四种页面状态结构，过滤 `hot_query` 和缺失 token 的条目，并为 RedNote 冷启动保留 30 秒单次等待窗口 | `PASSED_AUTOMATED` |

自动化复验命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q scripts
```

涉及前端或扩展修改时，还需对本次修改的 JavaScript 文件执行语法检查。

## 4. 真实浏览器验收前置条件

开始真实验收前必须满足：

- 用户明确指定用于验收的 RedNote 槽位和 Chrome Profile；
- 不复用正在执行其他任务的 Xiaohongshu 槽位；
- 用户手动重载本次修改后的通用扩展，或明确授权协助重载；
- 第一轮只授权连接和只读检查，不包含退出登录、换号、点赞、收藏、评论或发布；
- 任何平台、Profile、扩展实例或 UID 不匹配都立即停止；
- 每个步骤记录页面回读、结构化结果和最终状态。

## 5. 连接与身份真实验收

| 编号 | 操作 | 通过标准 | 当前状态 |
|---|---|---|---|
| REAL-CONN-01 | 创建或导入 RedNote 槽位 | WebUI 显示 `RedNote`，槽位绑定指定 Profile | `PASSED_REAL` |
| REAL-CONN-02 | 启动 Bridge 和绑定 Profile | Bridge 状态中的账号、平台、Profile 与槽位一致 | `PASSED_REAL` |
| REAL-CONN-03 | 完成扩展配对 | 扩展 v1.3.1 弹窗显示目标槽位与 `RedNote`，当前页面为 `rednote.com`，连接状态正常 | `PASSED_REAL` |
| REAL-CONN-03A | 旧后台进程提示 | 未重载的旧 Service Worker 被明确识别并提示重载，不再报告 `actual='xiaohongshu'` | `PENDING_REAL_READ_ONLY` |
| REAL-CONN-04 | 错平台页面隔离 | RedNote 槽位不会选择 Xiaohongshu 标签页或读取其 Cookie | `PENDING_REAL_READ_ONLY` |
| REAL-ID-01 | 读取未登录状态 | 未登录时明确返回 `LOGIN_REQUIRED`，不进入 `READY` | `PENDING_REAL_READ_ONLY` |
| REAL-ID-02 | 用户手动登录后读取身份 | 能读取 RedNote UID、昵称和主页地址 | `PASSED_REAL` |
| REAL-ID-03 | 首次记录身份 | 用户确认后才记录平台与 UID | `PASSED_REAL` |
| REAL-ID-04 | 再次核验身份 | 平台、Profile、扩展实例和 UID 全部一致后进入 `READY` | `PASSED_REAL` |
| REAL-ID-05 | 平台或 UID 不一致 | 返回对应不一致状态，任务保持阻止 | `PENDING_REAL_READ_ONLY` |

## 6. RedNote 只读能力验收

按顺序执行；当前一项失败时停止，不继续互动或发布验收。

| 编号 | 能力 | 通过标准 | 必留证据 | 当前状态 |
|---|---|---|---|---|
| REAL-READ-01 | 首页内容列表 | 返回非伪造的规范化内容；空页面能正确报告空结果 | 页面地址、数量、首条内容 ID | `PENDING_REAL_READ_ONLY` |
| REAL-READ-02 | 关键词搜索 | 搜索词、返回内容和 RedNote 页面一致 | 搜索词、结果数、首条内容 ID | `PASSED_REAL` |
| REAL-READ-03 | 笔记详情 | 标题、作者、正文和来源平台可读取 | 内容 ID、作者 ID、结构化结果 | `PENDING_REAL_READ_ONLY` |
| REAL-READ-04 | 评论读取 | 有评论和无评论场景均不误报 | 内容 ID、评论数、游标状态 | `PENDING_REAL_READ_ONLY` |
| REAL-READ-05 | 用户主页 | UID、昵称和基础资料与页面一致 | 用户 ID、主页地址、结构化结果 | `PASSED_REAL` |
| REAL-READ-06 | 运营记录 | 任务、事件或指标记录中的 `platform` 为 `rednote` | 对应本地记录 | `FAILED` |
| REAL-READ-07 | 错平台任务 | 构造的平台不一致任务在执行前返回 `PLATFORM_MISMATCH` | 任务终态与错误码 | `PENDING_REAL_READ_ONLY` |
| REAL-READ-08 | 未开放能力 | RedNote 点赞等 `UNKNOWN` 能力在 WebUI 禁用，后端返回能力不可用 | UI 状态与任务错误码 | `PENDING_REAL_READ_ONLY` |

RedNote 只读阶段的通过条件是 REAL-CONN、REAL-ID 和 REAL-READ 全部通过。完成前不得把只读能力从
`EXPERIMENTAL` 改为 `SUPPORTED`。

## 7. 双平台并行隔离验收

此阶段需要一个 Xiaohongshu 槽位和一个 RedNote 槽位同时连接，但只执行只读任务。

| 编号 | 验收项 | 通过标准 | 当前状态 |
|---|---|---|---|
| REAL-ISO-01 | 双 Bridge 并存 | 两个槽位使用不同端口，各自只连接自己的扩展实例 | `PENDING_REAL_READ_ONLY` |
| REAL-ISO-02 | 同时读取 | 两个平台可并行执行只读任务，结果分别记录正确平台 | `PENDING_REAL_READ_ONLY` |
| REAL-ISO-03 | 标签页隔离 | Xiaohongshu 任务不进入 RedNote 页面，反向同样成立 | `PENDING_REAL_READ_ONLY` |
| REAL-ISO-04 | Cookie 隔离 | 各槽位只返回所属平台 Cookie，不发生跨域读取或删除 | `PENDING_REAL_READ_ONLY` |
| REAL-ISO-05 | 身份漂移阻止 | 任一槽位 UID 改变后，该槽位停止 READY，另一槽位不受影响 | `PENDING_REAL_READ_ONLY` |

## 8. 互动能力验收

RedNote 点赞、收藏和评论当前状态为 `UNKNOWN`。当前版本的验收目标是确认这些入口被正确阻止，
不是执行真实动作。

后续只有在用户单独授权相应真实动作并完成专属适配后，才增加以下执行验收：

- 操作前页面状态；
- 明确的目标内容和目标账号；
- 操作后页面或接口回读；
- 重复操作的幂等表现；
- 无法回读时返回 `RESULT_UNKNOWN` 且不自动重试。

在此之前，互动能力状态保持 `BLOCKED_BY_CAPABILITY`。

## 9. 发布能力验收

发布验收必须与只读和互动验收分开授权。未经授权不得点击最终发布。

| 编号 | 验收项 | 通过标准 | 当前状态 |
|---|---|---|---|
| REAL-PUB-01 | 填写图文 | 标题、正文和素材进入 RedNote 创作页，但不发布 | `PASSED_REAL` |
| REAL-PUB-02 | 浏览器预览 | 用户能在真实页面核对平台、账号、UID、内容和素材 | `PASSED_REAL` |
| REAL-PUB-03 | 确认失效 | 内容、账号或 UID 变化后旧确认不能继续使用 | `PENDING_AUTHORIZATION` |
| REAL-PUB-04 | 最终发布 | 只有用户确认后才允许执行一次发布 | `PASSED_REAL` |
| REAL-PUB-05 | 发布回读 | 取得成功页面、作品地址或平台结果证据后才返回 `SUCCESS` | `PASSED_REAL` |
| REAL-PUB-06 | 结果不确定 | 无法确认是否发布时返回 `RESULT_UNKNOWN`，不得盲目重发 | `PASSED_REAL` |

视频、长文、私信、直播和商业能力不纳入本轮发布验收。

## 10. 验收记录模板

每次真实验收复制一行填写：

| 日期 | 测试编号 | 槽位 | 平台 | Profile | 记录 UID | 当前 UID | 结果 | 证据摘要 | 后续动作 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-08-26 | REAL-CONN-02 | profile-7 | rednote | Profile 7 | 6a6c…b801 | 6a6c…b801 | PASSED | `account-doctor`：`healthy=true`、`ready=true`，扩展平台为 `rednote` | 保持当前配对 |
| 2026-08-26 | REAL-ID-04 | profile-7 | rednote | Profile 7 | 6a6c…b801 | 6a6c…b801 | PASSED | `account-identity`：昵称“凌琳”，`comparison=match` | 允许进入发布预览 |
| 2026-08-26 | REAL-READ-02 | profile-7 | rednote | Profile 7 | 6a6c…b801 | 6a6c…b801 | PASSED | 修复后按“最多点赞 / 图文 / 半年内”搜索“英语口语”返回 20 篇笔记；首条 ID `69a1a7f1000000001d025115` | 已完成 |
| 2026-08-26 | REAL-READ-05 | profile-7 | rednote | Profile 7 | 6a6c…b801 | 6a6c…b801 | PASSED | 主页回读昵称、RedNote ID 和最近 3 篇笔记成功 | 保留为发布回读手段 |
| 2026-08-26 | REAL-READ-06 | profile-7 | rednote | Profile 7 | 6a6c…b801 | 6a6c…b801 | FAILED | 首次 `fill-publish` 任务误存 `platform=xiaohongshu`；代码已修复，`20 passed` 定向回归、`304 passed` 全量回归 | 下次 RedNote 发布预览复验任务快照 |
| 2026-08-26 | REAL-PUB-01 | profile-7 | rednote | Profile 7 | 6a6c…b801 | 6a6c…b801 | PASSED | 13 字标题、360 字正文、3 张 1086×1448 图片进入 RedNote 创作页 | 已完成 |
| 2026-08-26 | REAL-PUB-05 | profile-7 | rednote | Profile 7 | 6a6c…b801 | 6a6c…b801 | PASSED | 初次动作结果为 `RESULT_UNKNOWN`；随后主页和详情页均回读到同标题，笔记 ID `6a8e53970000000037034483`，图片数为 3 | 不重复发布 |
| 2026-08-26 | REAL-PUB-06 | profile-7 | rednote | Profile 7 | 6a6c…b801 | 6a6c…b801 | PASSED | 结果不确定后停止重填并先执行主页回读，仅存在一篇目标笔记 | 已完成 |

结果只使用 `PASSED`、`FAILED`、`BLOCKED` 或 `RESULT_UNKNOWN`。证据中不记录 Cookie、Bridge 令牌或
其他登录凭据。

## 11. 阶段签署标准

- 基础设施自动化验收：AUTO-01 至 AUTO-15 全部通过；当前已满足。
- RedNote 只读验收：REAL-CONN、REAL-ID、REAL-READ 和 REAL-ISO 全部通过。
- RedNote 互动验收：每种动作分别授权、执行和回读，不合并判定。
- RedNote 发布验收：填写、预览、确认、发布和回读形成完整闭环。
- 双平台正式可用：只声明已经完成对应真实验收的能力，其他能力继续显示实际状态。
