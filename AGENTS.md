# xiaohongshu-skills

小红书自动化项目，使用用户的真实浏览器和账号信息操作小红书。

## 项目记忆库

- 开始项目任务时，先读 `project-memory/README.md`、`project-memory/03-当前状态.md`，
  然后按任务读取相关主题和源码。
- 完成有持续价值的项目工作后，在 `project-memory/工作记录/` 登记结论、来源、验证结果与下一步，
  并更新该目录索引；阶段变化时更新当前状态，用户确认的新决策归入决策记录。
- 记忆是项目上下文索引；产品规则以 PRD 和追踪矩阵为准，实现以当前源码为准。
- 只读取相关材料。临时资料放入收件箱；推测和已确认事实分开记录，动态状态注明日期。
- 凭据、Cookie、验证码、令牌和私密消息正文保留在既有受控存储中，记忆库记录证据位置和摘要。

## Git 工作流

- 所有代码修改必须在分支上进行。
- 分支开发完成后通过 PR 合入 `main`。

## 开发命令

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```

## 架构

项目由本地应用服务、Python 自动化引擎、Bridge/扩展、WebUI 和 Skills 组成。

- `scripts/xhs/` — 核心自动化库，每个功能独立模块。
- `scripts/application_service.py` — CLI、WebUI 和 Agent 共用的任务、确认与身份服务。
- `scripts/cli.py` — 统一 CLI 入口，按账号槽位输出 JSON 结构化结果。
- `scripts/bridge_server.py` — 连接 CLI 与浏览器扩展的本地通信服务。
- `extension/` — 在用户真实浏览器中执行操作的 Chrome 扩展。
- `webui/` — 仅监听本机的账号、任务、确认、记录与诊断工作台。
- `skills/*/SKILL.md` — 指导 Agent 调用项目脚本。

### 调用方式

```bash
python scripts/cli.py --account brand-a check-login
python scripts/cli.py --account brand-a search-feeds --keyword "关键词"
python scripts/cli.py --account brand-a fill-publish --title-file t.txt --content-file c.txt --images pic.jpg
```

只有 `account-start` 会在扩展未连接时按需打开槽位绑定的 Chrome Profile。普通业务命令保持当前浏览器和账号环境。发布采用生成内容、填写、正式发布和结果回读流程。

## 代码规范

- 行长度上限 100 字符。
- 使用完整 type hints 和 `from __future__ import annotations`。
- 异常继承 `XHSError`（`xhs/errors.py`）。
- CLI exit code：0=成功，1=未登录，2=错误。
- 用户可见错误信息使用中文。
- JSON 输出使用 `ensure_ascii=False`。

### 操作约束

- 视觉点击前视口变化时，使用入口返回的新截图重新识别目标，以新步骤文件继续。
  再次变化时，导航与草稿编辑使用 browser-action 的 target_text 匹配当前 DOM 唯一目标。
  点击已发出或结果未知时先核对原动作；发布、评论等提交使用业务专用入口。
- 小红书与 RedNote 分别使用对应的页面入口和按钮定位；页面布局变化后按当前截图或 DOM
  重新定位，不沿用另一平台或旧视口的点击位置。

- 主动评论先读取目标笔记的截图或 DOM 内容，由 Agent 结合账号人设生成贴合内容的评论；
  只通过本项目评论业务入口提交，不调用 `random-comment` 或随机文案脚本。
- 主动评论以本次提交后平台明确的“评论成功”提示确认发送成功。
  `confirmation_source=platform_success_notice` 时保存 `platform_receipt` 和证据路径，
  逐项回执记录成功；无需再滚动评论区寻找原文或核对评论作者 UID。
  无明确成功提示时保留结果待核对，不重复发送。历史评论归属核对与本次发送结果分别记录。

- 发布前核对创作中心的相同作品与草稿：已有作品只回读，已有草稿进入原草稿编辑并发布。
  新作品按账号人设生成内容后正式提交一次；平台成功提示或笔记管理回读到“审核中”
  即确认已提交。保存作品 ID、链接和证据；链接补查限时进行，结果未知先核对原动作，
  不重复提交或持续刷新等待审核通过。
- 每次修改扩展可执行文件时同步递增 `extension/manifest.json` 的版本号；部署后以目标 Chrome Profile 的 Bridge 回报版本为准，重载并核对后才执行发布。经理机负责下发与回收，正式发布由员工机任务执行并写入 outbox。
- 文件路径使用绝对路径。
- 敏感内容通过文件传递，不内联到命令行参数。
- 小红书操作统一使用本项目执行链。

## 经理机与员工机协作

- 经理机管理配置、账号人设、授权和计划；员工机操作本机账号、执行任务并回传结果。
- 用户要求拟定或审核指标时，先给出账号、目标、素材组和批次时间，按已确认方案下发；
  用户直接要求下发或补齐且授权已明确时，执行投递。
- 下发链路：经理机生成任务 JSON → SSH/SFTP 投递员工机 `inbox` → `wake-task` 唤醒 →
  员工领取并执行 → 写入员工机 `outbox` → 经理机回收、去重导入并更新运营台。
  新员工机必须配置并验证两端一致的 inbox/outbox 路径，路径从机器配置读取。
- 区分任务接收 `TASK_ACK`、Agent 实际领取、业务完成、outbox 写入和经理端导入。
  只要求下发时，收到接收确认后交由后台执行，经理 Agent 结束本轮；用户要求检查进度
  或完成情况时，再核对业务回执与未完成原因。
- 下发命令超时、经理状态锁占用或唤醒结果未知时，先按任务 ID 核对经理记录、员工
  `inbox` 和 `outbox`，确认送达或领取情况后再处理，不重复投递同一发布或评论动作。
- 每日指标拆成批次时，一次写全各批的开始时间与前后顺序，同一员工机上的账号
  按机器队列串行执行。
  每批目标是本批新增量，全天目标用于汇总；首批完成不得抵扣后续批次或代表全天完成。
- 多账号从本机配置读取，任务按指定槽位执行，人设、身份、数据及回复记录按账号隔离。
  执行时复用槽位已绑定 UID；首次绑定、手动换号或主动检查身份时读取页面 UID。同账号串行，占用时顺延，一个账号异常不阻断其他账号。
- 机器例外只适用于指定机器，以当前配置和用户安排为准。PC052 普通业务任务复用已绑定
  槽位身份，不主动检查身份或进入“我的主页”；明确暂停的账号保持暂停。
- 周期巡检约定：员工机每 **20 分钟**采集新增粉丝、收获点赞、新评论和私信，
  按账号规则自动回复评论与入站私信。经理机统一管理策略，员工本地定时触发，
  同一用途只配置一套周期调度。明确暂停的账号保持暂停。
- 每轮固定待处理事件集合，按批次处理；后来新增的留给下一轮。沿用账号人设、知识、
  回复开关，已回复事件去重，由 Agent 结合上下文智能回复。发送后回读确认，
  `RESULT_UNKNOWN` 先核对，不自动重发。粉丝与点赞采集仅回传数据。
- 每轮、每账号均写回执，包括无新增、顺延、阻塞和失败；包含任务/轮次编号、身份、时间、
  业务汇总、逐项结果、增量事件、证据路径及未完成原因。区分累计、本轮和今日新增；
  缺失数据保持为空，首次采集建立基线。
- 复用现有 outbox 协议追加记录、维护索引与结果修订，保留历史。JSON 用标准序列化，
  对象与数组保持原生结构。经理机离线时本地留存，恢复后回收，不重复执行业务动作。
- 巡检间隔、运行时段、账号开关、双渠道回复开关应在系统设置呈现，并显示最近采集、
  下次巡检、待处理数量及回传状态。周期配置是否已生效，以各机器实际验证为准。

## 素材与数据回收

- 经理机本地素材统一从 `C:\小红书Mila文件存档\素材` 读取。按账号和素材系列记录上次
  实际成功发布的组号，接续任务使用下一组；下发前核对图片与文案，传到对应员工机后
  核对文件数量和字节数。原发布结果未知时先对账，再决定是否接续或使用原草稿。
- 数据回收只核对三步：员工机是否实际执行并生成回执；回执是否传输到经理机；
  经理机 `manager-operations.db` 是否导入并更新对应记录及同步时间。分别报告三步状态。
  前端展示读取经理机本地汇总数据，页面显示问题作为单独任务排查，不作为回收验收条件。

## CLI 入口

### 运营数据查询与验收

- 询问运营数据时使用 `manager_cli.py database schema` 发现字段，再使用
  `database query --input-file <绝对路径>` 执行只读 SQL；查询 JSON 包含 sql、parameters、limit。
- 经理查询库在经理状态目录的 `manager-operations.db`；records/fields 为经理原始记录，
  worker_records/worker_fields 为按机器隔离的员工业务表。先查 metadata 的同步时间。
- `manager_cli.py acceptance` 返回每种动作目标、确认实际数、账号与证据；支持 --date 和
  重复 --account 机器:槽位。历史成功与当前轮次分开说明。完整说明见
  `docs/OPERATIONS-ACCEPTANCE-DATABASE.md`。

完整命令以 `python scripts/cli.py --help` 为准，产品边界以 `docs/PRD-TRACEABILITY.md` 和 `SKILL.md` 为准。

| 分类 | 主要命令 |
| --- | --- |
| 账号与连接 | `account-onboard`、`account-start`、`account-doctor`、`account-pair-*` |
| 身份与换号 | `check-login`、`account-identity`、`account-switch-*` |
| 浏览与互动 | `list-feeds`、`search-feeds`、`get-feed-detail`、`like-feed`、`favorite-feed` |
| 评论与回复 | `post-comment`、`reply-comment`、`collect-note-comments`、`generate-reply-draft` |
| 分步发布 | `fill-publish`、`fill-publish-video`、`long-article`、`select-template`、`next-step`、`click-publish`、`save-draft` |
| 私信与主加 | `private-message-context`、`collect-private-messages`、`prepare-private-messages`、`send-private-messages`、`follow-user` |
| 数据与诊断 | `collect-operations-metrics`、`diagnose-404`、`check-risk`、`get-netlog`、`risk-report` |

## 员工故障与修复

- 员工失败回执通过现有 outbox 自动报障，也可用任务固定入口 `report-bug` 补充现象和证据。
- 经理使用 `scripts/manager_cli.py debug list/claim` 读取和领取工单，在项目分支复现、修改
  权威源码、补充回归测试；通过 `diagnose/build` 登记诊断并运行测试构建修复包。
- 使用 `dispatch/apply/reconcile/verify/accept` 完成送达、安装、回执核对、服务验证和验收。
  员工任务运行中顺延安装，安装后保持暂停，按运营安排恢复。
- 原任务结果未知时先核对原执行证据。调试状态、代码部署状态与业务结果分别记录。
- 操作字段和回滚步骤见 `docs/DEBUG-WORKFLOW.md`。

## 功能包打包规范

- 正式包放在项目外的 `../功能包/<业务模块>/<功能编号>/<版本>/`，保留各版本，并同步更新 `../功能包/版本清单.json`。
- 每包包含：`manifest.json`、`README.md`、`runtime/`、`config/`、`tests/`、`CHANGELOG.md`、`ROLLBACK.md`、`RUN_RESULT.json`、`VERIFICATION_REPORT.md`。
- `manifest.json` 写明并保持一致：`function_id`、`task_code`、`function_name`、`package_name`、`applicable_layer`、`execution_mode`、依赖、调用关系、运行入口和证据文件。
- 功能包使用项目真实功能编号和实现；运行入口可执行，测试覆盖固定测试和历史回放。
- 实机证据记录设备、系统、Python、执行动作、结果和结论；自动测试、实机验证、上传、开放及正式安装分别登记状态。
- 一个 ZIP 对应一个功能的一个版本；提交前从 ZIP 内复核功能编号、审核字段、必需文件和运行证据。
