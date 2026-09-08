# xiaohongshu-skills

面向个人运营者的本地多账号小红书运营系统。系统通过 Chrome Profile、WebUI、Codex 和统一
Python CLI 完成配置、任务、确认和结果反馈。V1.0 是冻结基线；V1.5 能力正在分批交付，其中
账号联合启动、分步发布、主加和私信已具备自动化测试证据；V2.0 已接入由 Codex 生成决策的评论和
入站私信回复收件箱。
凡涉及真实浏览器写操作的能力，仍按各自文档标注区分“已测试”和“已实机验收”。

支持 [OpenClaw](https://github.com/openclaw/openclaw) 及所有兼容 `SKILL.md` 格式的 AI Agent 平台。

> **⚠️ 使用建议**：虽然本项目使用真实的用户浏览器和账号环境，但仍建议**控制使用频率**，避免短时间内大量操作。频繁的自动化行为可能触发小红书的风控机制，导致账号受限。

## 功能概览

| 技能                | 说明     | 核心能力                                 |
| ------------------- | -------- | ---------------------------------------- |
| **xhs-auth**        | 认证管理 | 登录检查                                 |
| **xhs-publish**     | V1.5目标 | 图文 / 视频 / 长文 / 草稿 / 定时发布     |
| **xhs-explore**     | 内容发现 | 关键词搜索、笔记详情、用户主页、首页推荐 |
| **xhs-interact**    | 社交互动 | 私信、主加、评论、回复、点赞、收藏       |
| **xhs-reply**       | 入站回复 | Codex 评论与私信上下文回复               |
| **xhs-content-ops** | 复合运营 | 竞品分析、热点追踪、批量互动、内容创作   |

### 版本与开放范围

| 范围 | 冻结基线 / 产品规则 | 当前交付状态 |
|---|---|---|
| 登录 | 检查、退出、用户手动登录和新 UID 核验 | 半自动换号链已测试；历史二维码和手机登录命令仅兼容保留 |
| Chrome | 绑定固定 Profile，不自动关闭或切换账号 | 联合启动已测试：扩展离线时可打开绑定 Profile；真实 Windows 待验收 |
| 发布 | 禁止一步直发，必须填写、真实预览、单任务确认和结果回读 | Agent/CLI 填写及 WebUI/Agent 受控确认已测试；真实发布待验收 |
| 评论 | 指定评论/回复保留最终文本确认；随机评论一次授权 1–3 条 | 当前确认链可用；“所有指定评论点击即直发”仍是 V1.5 规格 |
| Codex 回复 | Codex 读取入站上下文并提交回复决策；业务判断类内容进入待处理 | `reply-inbox`、规则复核、发送回读和专用 Skill 已通过自动化测试；真实账号周期待验收 |
| 主加 | 单向关注，Agent/CLI 内部预览后执行并回读 | 已完成自动化测试和测试账号实机回读 |
| 私信 | 主动私信沿用 Agent 任务；入站私信按账号规则回复或进入待处理 | 最新连续对方消息作为一次回复单位，发送前校验会话未变化 |
| 资料修改 | 展示前后差异并确认后执行 | 尚无完整执行链 |

实现状态以 [需求追踪矩阵](docs/PRD-TRACEABILITY.md) 为准；文档导航和历史/现行边界见
[文档索引](docs/README.md)。规格批准、自动化测试和真实设备验收不是同一状态。

## 普通用户快速开始

Windows 用户可以直接双击仓库根目录的 `start-auto-xhs.cmd`。启动器会检查环境、复用已经运行的
本地服务并打开 `http://127.0.0.1:8765`。首次进入后按
“添加账号 → 手动打开目标 Profile → 配对扩展 → 检查并确认 UID”的顺序完成账号准备。

更新 Python 后端代码后，可点击 WebUI 顶部的“重启服务”，也可以双击 `restart-auto-xhs.cmd`。
安全重启会先检查执行中任务；服务恢复后，WebUI 通过新的运行实例标识确认后台已经完成更新。

V1.5 已把“启动 Bridge”升级为“启动账号”：先启动 Bridge，扩展未连接时再自动打开绑定
Profile，并在核对扩展实例和 Profile 后恢复连接。该链已通过自动化测试，真实 Windows 环境仍待验收。

WebUI 当前作为本地控制面板，包含账号槽位、Bridge 启停、已有直接任务、评论采集、评论/回复草稿确认、
执行记录、全局暂停、L1 配额、并发设置和脱敏诊断导出。后续新增运营能力默认由 Agent/Python CLI
下发，除非另行明确要求，不再为其增加 WebUI 任务入口。详细步骤见 [普通用户操作手册](docs/USER-GUIDE.md)，常见故障见
[故障恢复指南](docs/TROUBLESHOOTING.md)。

支持**连贯操作** — 你可以用自然语言下达复合指令，Agent 会自动串联多个技能完成任务。例如：

> "搜索刺客信条最火的图文帖子，收藏它，然后告诉我讲了什么"

Agent 会自动执行：搜索 → 筛选图文 → 按点赞排序 → 收藏 → 获取详情 → 总结内容。

## 安装

### 前置条件

- Windows PowerShell
- Python >= 3.11（可选；缺少时由首次运行引导器在确认后准备）
- Google Chrome 浏览器

### 第一步：安装项目

**方法一：下载 ZIP（推荐）**

1. 在 GitHub 仓库页面点击 **Code → Download ZIP**，下载并解压到你的 Agent skills 目录：

```
# OpenClaw 示例
<openclaw-project>/skills/xiaohongshu-skills/

# Claude Code 示例
<your-project>/.claude/skills/xiaohongshu-skills/
```

**方法二：Git Clone**

```bash
cd <your-project-directory>
git clone https://github.com/xiaoquan661/auto-xhs.git
cd auto-xhs
```

2. 检测 Python 并准备项目依赖：

```powershell
cd xiaohongshu-skills
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Prepare
```

脚本会优先使用项目 `.venv` 或本机 Python。若输出 `python_missing`，先向用户确认；得到同意后
再执行 `scripts/bootstrap.ps1 -Prepare -InstallMissingPython`。此时才会通过官方 uv 为项目准备
托管 Python。脚本输出的 `python_executable` 是后续 CLI 应使用的解释器；下文的 `python` 是其简写。

本地 Web 控制台和 Bridge 不仅需要 Python，还需要项目运行依赖。其中 `websockets` 用于 WebUI、
CLI 与 Bridge 的连接，`requests` 用于页面和资源请求。这些依赖已声明在 `pyproject.toml` 中，正常
情况下会由上述 `bootstrap.ps1 -Prepare` 自动安装。若从 GitHub 更新代码后出现
`ModuleNotFoundError: No module named 'websockets'`，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

然后重新运行 `start-auto-xhs.cmd`。不要只用系统 Python 单独启动 `web_server.py`，否则可能绕过
项目虚拟环境中已经安装的依赖。

### 第二步：安装浏览器扩展

扩展让 AI 能够在你的浏览器中以你的身份操作小红书，使用的是你真实的登录状态和账号信息。
当前版本使用一套通用扩展；不同 Chrome Profile 通过各自的 `chrome.storage.local` 保存配对，
不再为每个账号复制扩展代码或在扩展磁盘文件中写入长期令牌。

1. 先按下方说明创建或导入至少一个账号槽位；通用扩展就是当前项目根目录中的
   `extension` 文件夹，不使用 `.codex` 下的旧安装副本。
2. 在目标 Chrome Profile 打开 `chrome://extensions/` 并开启**开发者模式**。
3. 点击**加载已解压的扩展程序**，选择 CLI 输出的同一个通用扩展目录。
4. 运行 `account-pair-begin --confirm`，把一次性配对包粘贴到扩展弹窗。

安装完成后即可使用 — 所有操作都发生在你自己的浏览器里，使用你的真实账号和浏览器环境。

### 多账号并发配置

多账号模式为每个账号保留独立 Chrome Profile、Bridge 端口和任务锁，但所有 Profile
加载同一个通用扩展目录。Profile 的 Cookie 与配对存储仍然互相隔离。

账号名称只是本机别名，不要填写手机号、密码或验证码：

```bash
# 依次创建槽位、启动 Bridge，并把一次性配对包放入剪贴板
python scripts/cli.py account-onboard --name brand-a --confirm
python scripts/cli.py account-onboard --name brand-b --confirm

# 用户手动打开各自对应的 Chrome Profile，并保持小红书页面开启
# 然后在各自 Chrome 中完成一次登录
python scripts/cli.py --account brand-a login
python scripts/cli.py --account brand-b login

# 查看配置与连接状态
python scripts/cli.py account-list
python scripts/cli.py --account brand-a account-status

# 一次诊断全部账号；只读，不会操作小红书内容
python scripts/cli.py account-doctor

# 检查单个账号，并要求 Bridge 和扩展均已连接
python scripts/cli.py account-doctor --name brand-a --require-ready
```

每次 `account-onboard` 执行后，请用户手动打开对应 Chrome Profile，再打开 XHS Bridge 弹窗，
粘贴剪贴板内容并确认。这是浏览器侧的安全确认，不能跨 Profile 自动代替。剪贴板不可用时，
命令会在 JSON 中回退显示短期配对包。

Bridge 默认以独立后台进程启动，关闭 Agent 或 PowerShell 不应终止它。需要恢复账号运行环境时，
使用 `account-start` 启动或复用 Bridge，并按需打开绑定的 Chrome Profile。

之后给所有业务命令加上目标账号：

```bash
python scripts/cli.py --account brand-a search-feeds --keyword "咖啡"
python scripts/cli.py --account brand-b search-feeds --keyword "露营"
python scripts/cli.py --account brand-a random-comment --count 1 --style natural

# 单次首页会话：浏览 6 篇，其中点赞 2 篇
python scripts/cli.py --account brand-a home-engagement --browse-count 6 --like-count 2
```

`home-engagement` 只执行浏览和点赞。需要评论时，Agent 从结果中选定笔记，读取标题、正文、标签和
可用图片文字后生成具体评论，再通过 `post-comment` 发送并回读平台结果。

不同账号的命令可由两个终端或 Agent 并发执行。同一账号的命令由账号锁串行执行，避免两个
任务同时抢占同一个页面。V1.0 指定评论仍走当前确认链；随机评论命令或 WebUI 的直接发送按钮
代表对当前账号和本次 1–3 条评论的明确授权。V1.5 目标将指定评论也改为当前任务点击后直发，
自动回复则必须先按账号启用规则。

项目扩展代码升级后，运行下面的命令部署或刷新通用扩展。旧账号执行后会自动迁移为
`extension_mode=universal`：

```bash
python scripts/cli.py --account brand-a account-sync
```

所有目标 Profile 加载或重新加载同一个通用扩展目录。然后为每个 Profile 分别生成一次性
配对包；配对包默认 5 分钟过期且只能使用一次：

```bash
python scripts/cli.py --account brand-a account-pair-begin --confirm
# 在 brand-a 对应 Profile 的扩展弹窗中粘贴 pairing_bundle
python scripts/cli.py --account brand-a account-pair-status
python scripts/cli.py account-doctor --name brand-a --require-ready
```

配对完成后，Bridge 会拒绝错误账号凭据、未登记的扩展实例和未认证 CLI。长期连接令牌只保存
在本机账号配置与对应 Profile 的扩展本地存储中，不写入共享扩展目录，也不会出现在普通状态
输出里。需要撤销时运行 `account-unpair --confirm`；重新配对会轮换长期令牌。

#### 在账号槽位中安全更换小红书账号

`brand-a`、`account-1` 等名称是本机浏览器槽位，不是固定的小红书账号。槽位的 Profile、
扩展和 Bridge 端口保持不变，当前登录的小红书账号可以更换：

```bash
# 可选但推荐：首次使用时记录当前小红书 UID 作为身份基准
python scripts/cli.py --account account-1 account-identity --record --label "主账号"

# 1. 暂停 account-1 的业务任务并退出当前登录
python scripts/cli.py --account account-1 account-switch-begin --confirm --label "新账号"

# 2. 获取二维码并完成新账号登录
python scripts/cli.py --account account-1 check-login
python scripts/cli.py --account account-1 wait-login

# 3. 核验新 UID、写入换号记录并恢复业务任务
python scripts/cli.py --account account-1 account-switch-complete --label "新账号"

# 4. 查看当前身份或换号历史
python scripts/cli.py --account account-1 account-identity
python scripts/cli.py --account account-1 account-switch-history
```

从 `account-switch-begin` 到 `account-switch-complete` 之间，同一槽位的搜索、互动和发布等业务
命令都会被拒绝，登录命令仍然可用。如果指定了 `--target-user-id`，完成时必须登录对应 UID。
如果旧账号以后还要快速使用，不要覆盖原槽位；为它保留独立 Profile，并新建另一个账号槽位。

退出操作会优先由扩展结束当前登录会话，网页中的“更多 → 退出登录”只用于兼容旧版扩展。
服务重新加载首页并回读登录状态后才会报告成功；仍检测到 UID 时会明确返回失败。

不再使用某个槽位时，可在 WebUI 账号卡片的“更多”中选择“删除槽位”，或运行：

```powershell
python scripts/cli.py --account account-1 account-remove --confirm --confirm-name account-1
```

该操作会停止受管 Bridge、撤销配对，并把槽位目录移入本机
`.xhs\accounts\.archive`。它不会删除 Chrome Profile、小红书登录数据或共享通用扩展。

#### 绑定已有 Chrome Profile

已有小红书登录状态时，不要复制 Profile。直接把 `User Data` 根目录和具体 Profile
目录绑定到账号别名：

```powershell
# 先列出已有 Profile 的目录名和显示名称
python scripts/cli.py account-discover

python scripts/cli.py account-import `
  --name brand-existing `
  --user-data-dir "C:\Users\EDY\AppData\Local\Google\Chrome\User Data" `
  --profile-directory "Profile 2"

# 用户手动打开 Profile 2 并保持小红书页面开启，然后检查 Bridge/扩展连接
python scripts/cli.py --account brand-existing account-start
```

如果账号别名已经存在，但需要改为绑定某个已有 Profile，使用 `--replace`。工具会沿用原
Bridge 端口，不删除原 Chrome 数据，并将上一份配置保存为 `account.previous.json`：

```powershell
python scripts/cli.py account-import `
  --name account-1 `
  --user-data-dir "C:\Users\EDY\AppData\Local\Google\Chrome\User Data" `
  --profile-directory "Default" `
  --replace
```

导入操作不会修改或复制原 Profile，原 Cookie 和登录状态会继续使用。每个 Profile 只能绑定
一个账号别名。用户首次手动打开后，在该 Profile 的 `chrome://extensions` 中加载通用扩展目录并完成
一次性配对。多个 Profile 应共用这一目录，但每个 Profile 只能保留一个 XHS Bridge 实例；若已加载
旧版账号专属扩展，先禁用或移除旧副本。V1.0 的 `account-start` 不会启动 Chrome；只有 Bridge 服务和
已配对扩展都连接时才返回 `success: true`。

#### 本地 WebUI 开发预览

当前工作区提供完整的本地 WebUI 工作台，用于账号配置、任务下发、确认处理、执行记录和系统诊断：

```powershell
$env:PYTHONPATH = "scripts"
python scripts/web_server.py
```

然后在浏览器访问 `http://127.0.0.1:8765`。服务默认支持局域网访问，其他电脑打开
`http://<经理机局域网 IP>:8765/`，Windows 防火墙需放行来自 `LocalSubnet` 的 TCP 8765。
监听方式和重启命令见 [用户指南](docs/USER-GUIDE.md#4-第一次启动)。页面可新增或导入账号槽位、
完成配对和身份核验；“启动账号”只会按槽位启动 Bridge，并在扩展未连接时打开已绑定 Profile，
不会自动关闭 Chrome，也不会代替用户登录或切换小红书账号。

## 使用方式

### 作为 AI Agent 技能使用（推荐）

安装到 skills 目录后，直接用自然语言与 Agent 对话即可。Agent 会根据你的意图自动路由到对应技能。

**认证登录：**

> "检查登录状态" / "退出当前账号，我手动登录后再核验新 UID"

**搜索浏览：**

> "搜索关于露营的笔记" / "查看这条笔记的详情"

**发布内容（V1.5 Agent 流程）：**

> "为 brand-a 填写这篇图文，打开浏览器预览，等我确认后再发布"

图文、视频、长文、草稿和定时发布必须通过 Agent/Python CLI 分步填写并形成真实预览。进入
`preview_ready` 后，用户可在 Agent 对话或 WebUI 中逐任务核对并确认发布或保存草稿；WebUI 不创建
或填写发布内容。主动私信仍由 Agent/CLI 下发并由 WebUI 展示结果；收到的新私信和自己笔记下
的新评论由 Codex 通过 `reply-inbox` 读取上下文、生成决策、发送并回读，需要业务判断的内容进入待处理。详见
[`docs/AUTO-XHS-V2-INTELLIGENT-REPLY.md`](docs/AUTO-XHS-V2-INTELLIGENT-REPLY.md)。

**个性化私信（Codex 流程）：**

> “使用 brand-a 给这 3 位博主分别写一条合作私信，目的是沟通内容合作。”

Codex 先识别首次私信或已有会话，结合用户给出的联系目的和真实上下文为每位联系人生成不同文本，
随后直接发送、回读并汇总。单批最多 10 人；逐人记录 `SUCCESS`、`FAILED` 或
`RESULT_UNKNOWN`，单人失败不阻断其余人，恢复批次不重发成功或结果未知项。

**入站回复（Codex 流程）：**

在 WebUI“账号回复规则”中配置评论/私信开关、生效时段、数量上限、人设和知识，并显式启用。
每次手动或计划运行 `reply-inbox` 都只处理一批新事件。Codex 读取笔记、评论线程或近期私信会话，
结合账号人设、知识和规则生成 `reply`、`pending` 或 `skip`；Python/Bridge 负责发送前核验和页面回读。

**主动关注博主（Agent 流程）：**

> "使用 brand-a 关注这个博主，先给我看目标资料，不要直接点击"

主加属于单向操作，Agent 默认直接读取目标主页和当前关注状态、关注一次并回读按钮状态，不需要
审批；只有用户特别说明时才暂停等待确认。WebUI 只展示统一任务与结果，不创建或执行该任务。

**社交互动：**

> "给这条笔记点赞" / "收藏这条帖子" / "评论：写得太好了"

**复合操作：**

> "搜索竞品账号最近的爆款笔记，分析他们的选题方向"

### 作为 CLI 工具使用

所有功能也可以通过命令行直接调用，输出 JSON 格式，便于脚本集成。

```powershell
# 检查登录状态
python scripts/cli.py --account brand-a check-login

# 搜索笔记
python scripts/cli.py --account brand-a search-feeds --keyword "关键词"

# 带筛选条件
python scripts/cli.py --account brand-a search-feeds `
  --keyword "关键词" `
  --sort-by "最多点赞" `
  --note-type "图文"

# 查看笔记详情
python scripts/cli.py --account brand-a get-feed-detail `
  --feed-id FEED_ID --xsec-token XSEC_TOKEN

# V1.5 Agent 分步发布流程
python scripts/cli.py --account brand-a fill-publish `
  --title-file "C:\Temp\title.txt" `
  --content-file "C:\Temp\content.txt" `
  --images "C:\Media\pic1.jpg"
# 命令返回 TASK_ID 和 PREVIEW_REVISION；用户核对浏览器真实预览并确认后：
python scripts/cli.py --account brand-a click-publish `
  --task-id TASK_ID --preview-revision PREVIEW_REVISION --confirm

# 点赞 / 收藏 / 评论
python scripts/cli.py --account brand-a like-feed --feed-id FEED_ID --xsec-token XSEC_TOKEN
python scripts/cli.py --account brand-a favorite-feed --feed-id FEED_ID --xsec-token XSEC_TOKEN
python scripts/cli.py --account brand-a post-comment --feed-id FEED_ID --xsec-token XSEC_TOKEN --content "评论内容"

# 主加：默认无需审批，内部先预览再关注并回读
python scripts/cli.py --account brand-a follow-user `
  --user-id USER_ID --xsec-token XSEC_TOKEN

# 私信：先读取会话，再将联系目的、会话类型、上下文快照和最终文本写入 recipients.json
python scripts/cli.py --account brand-a send-private-messages `
  --recipients-file "C:\Temp\recipients.json"

# 恢复一个部分完成的批次，只重试 FAILED/BLOCKED 项
python scripts/cli.py --account brand-a send-private-messages `
  --resume-task-id TASK_ID

# 只读采集最近一对一会话中由对方发送的最新文字
python scripts/cli.py --account brand-a collect-private-messages `
  --max-conversations 20
```

> `account-start` 会在扩展未连接时按需打开槽位绑定 Profile；普通业务命令不会任意启动、切换
> 或关闭 Chrome。进入 READY 仍以 Profile、扩展实例和 UID 回读为准。

## CLI 命令参考

| 子命令                      | 说明                                                  |
| --------------------------- | ----------------------------------------------------- |
| `account-onboard`           | 创建槽位、启动 Bridge 并生成一次性配对包              |
| `account-add`               | 创建独立 Chrome Profile、Bridge 与账号槽位            |
| `account-discover`          | 列出已有 Chrome Profile 及绑定状态                    |
| `account-import`            | 绑定已有 Chrome Profile，保留登录状态                 |
| `account-list`              | 列出所有已配置账号                                    |
| `account-remove`            | 将槽位移入本机归档，保留 Profile、登录数据与共享扩展  |
| `account-start`             | 启动或复用 Bridge；扩展离线时按需打开绑定 Profile     |
| `account-status`            | 检查目标账号的服务和扩展连接状态                      |
| `account-sync`              | 让账号槽位指向当前项目中所有 Profile 共用的扩展目录   |
| `account-doctor`            | 只读诊断账号配置、Profile、扩展路由、端口和连接状态   |
| `collect-note-comments`     | 检查自己最近笔记的新评论并写入本地事件收件箱         |
| `collect-operations-metrics` | 回收账号和自己笔记的运营指标时间快照                 |
| `account-pair-begin`        | 生成目标账号的一次性 Profile 配对包                   |
| `account-pair-status`       | 查看 Profile 配对与扩展连接状态                       |
| `account-unpair`            | 撤销 Profile 配对并轮换长期连接令牌                   |
| `account-connection-enroll` | 仅供旧版账号专属扩展迁移使用                          |
| `account-identity`          | 读取当前小红书 UID，并与账号槽位的身份记录比较        |
| `account-switch-begin`      | 暂停业务任务并退出当前登录，开始安全换号              |
| `account-switch-complete`   | 核验并绑定新登录身份，恢复业务任务                    |
| `account-switch-cancel`     | 取消尚未完成的换号流程                                |
| `account-switch-history`    | 查看本机保存的换号记录                                |
| `check-login`               | 检查登录状态，返回用户昵称和小红书号                  |
| `login`                     | 历史兼容命令；V1.5 产品流程不再引导使用               |
| `get-qrcode` / `wait-login` | 历史兼容的分步扫码登录命令                            |
| `phone-login` / `send-code` / `verify-code` | 历史兼容的手机号登录命令；产品流程不再引导 |
| `delete-cookies`            | 结束当前登录会话并回读页面核验退出结果                |
| `list-feeds`                | 获取首页推荐 Feed                                     |
| `browse-feeds`              | 按时间和数量自动滚动首页并点开笔记                    |
| `search-feeds`              | 关键词搜索笔记（支持排序/类型/时间/范围/位置筛选）    |
| `keyword-engagement`        | 按关键词收集候选并随机点赞或收藏                      |
| `get-feed-detail`           | 获取笔记完整内容和评论                                |
| `user-profile`              | 获取用户主页信息和帖子列表                            |
| `follow-user-preview`       | Agent 可选只读目标主页和当前关注状态                  |
| `follow-user`               | Agent 无需审批地预览、关注一个目标并回读结果          |
| `collect-private-messages`  | 只读采集最近一对一会话中由对方发送的最新文字          |
| `private-message-context`   | 只读识别首次私信入口或读取已有会话近期文本            |
| `send-private-messages`     | 直接发送 Codex 个性化文本，或恢复批次可重试项         |
| `generate-reply-draft`      | 为新评论或入站私信事件生成智能回复待确认草稿          |
| `reply-cycle`               | 按账号规则处理一轮新评论和新私信，发送后回读并记录    |
| `reply-inbox`               | Codex 采集、读取上下文并应用一个结构化回复决策        |
| `post-comment`              | V1.0 使用当前确认链；V1.5 目标为当前任务点击后直发    |
| `random-comment`            | 当前任务点击一次性授权 1–3 条随机评论                 |
| `home-engagement`           | 单次首页会话完成浏览和点赞并输出逐篇记录               |
| `reply-comment`             | 单次回复执行器；规则周期通过专用操作调用并要求回读     |
| `like-feed`                 | 点赞 / 取消点赞                                       |
| `favorite-feed`             | 收藏 / 取消收藏                                       |
| `publish` / `publish-video` | 旧版一步发布命令；已明确禁用                           |
| `fill-publish`              | Agent 填写图文并创建待确认发布任务                     |
| `fill-publish-video`        | Agent 填写视频并创建待确认发布任务                     |
| `click-publish`             | 携带任务 ID；用户确认真实预览后执行发布                |
| `save-draft`                | 携带任务 ID；用户取消发布时保存草稿                    |
| `long-article`              | Agent 填写长文、创建任务并返回排版模板                 |
| `select-template`           | 携带任务 ID 选择长文模板                               |
| `next-step`                 | 携带任务 ID进入长文最终发布预览                        |
| `diagnose-404`              | 读取扩展拦截器捕获的 404 诊断信息                     |
| `check-risk`                | 汇总当前页面与接口的风险状态                          |
| `get-netlog` / `risk-report` | 读取已启用的 NetLog 并生成诊断报告                   |

退出码：`0` 成功 · `1` 未登录 · `2` 错误

主加已完成 Agent/CLI 自动化链和真实关注验收。主动私信执行器已完成自动化测试；入站私信收件箱
只读采集和模型草稿已有真实页面记录。评论和私信规则周期、上下文判断、发送前会话校验、页面
回读和待处理记录已完成自动化测试，真实账号周期仍需单独授权验收。

## 项目结构

```
xiaohongshu-skills/
├── extension/                      # Chrome 扩展
│   ├── manifest.json
│   ├── background.js
│   └── content.js
├── scripts/                        # Python 自动化引擎
│   ├── account_manager.py          # 多账号配置、端口与通用扩展部署管理
│   ├── account_pairing.py          # 一次性配对、撤销与连接令牌轮换
│   ├── account_doctor.py           # 多账号配置和运行状态只读诊断
│   ├── account_identity.py         # 登录身份记录、安全换号和任务保护
│   ├── application_service.py      # CLI、WebUI 和 Agent 共用的应用服务层
│   ├── reply_intelligence_service.py # API 模型兼容生成与确定性质量标记
│   ├── reply_llm_client.py         # API 模型兼容客户端
│   ├── private_message_collector.py # 入站私信事件去重与持久化
│   ├── operations_db.py            # 任务、事件和指标本地存储
│   ├── web_server.py               # 仅监听本机的 WebUI/API 服务
│   ├── run_lock.py                 # 每账号独立的任务锁
│   ├── xhs/                        # 核心自动化包
│   │   ├── bridge.py               # 扩展通信客户端
│   │   ├── selectors.py            # CSS 选择器（集中管理）
│   │   ├── login.py                # 登录 + 用户信息获取
│   │   ├── feeds.py                # 首页 Feed
│   │   ├── search.py               # 搜索 + 筛选
│   │   ├── feed_detail.py          # 笔记详情 + 评论加载
│   │   ├── user_profile.py         # 用户主页
│   │   ├── comment.py              # 评论、回复
│   │   ├── direct_message.py       # 私信上下文、发送和结果回读
│   │   ├── private_inbox.py        # 一对一私信收件箱只读采集
│   │   ├── like_favorite.py        # 点赞、收藏
│   │   ├── publish.py              # 图文发布
│   │   ├── publish_video.py        # 视频发布
│   │   ├── publish_long_article.py # 长文发布
│   │   ├── types.py                # 数据类型
│   │   ├── errors.py               # 异常体系
│   │   ├── urls.py                 # URL 常量
│   │   ├── cookies.py              # Cookie 持久化
│   │   └── human.py                # 行为模拟
│   ├── cli.py                      # 统一 CLI 入口
│   ├── bridge_server.py            # 本地通信服务
│   ├── image_downloader.py         # 媒体下载与本地缓存
│   ├── title_utils.py              # UTF-16 标题长度计算
│   └── run_lock.py                 # 单实例锁
├── webui/                          # 本地 WebUI 静态页面
│   ├── index.html
│   ├── styles.css
│   ├── workspace.css
│   ├── api-client.js
│   ├── task-catalog.js
│   ├── workspace.js
│   └── app.js
├── docs/                            # PRD、用户指南、架构、专题和验收文档
├── prompts/                         # 评论与私信智能回复提示词
├── skills/                         # Claude Code Skills 定义
│   ├── xhs-auth/SKILL.md
│   ├── xhs-publish/SKILL.md
│   ├── xhs-explore/SKILL.md
│   ├── xhs-interact/SKILL.md
│   ├── xhs-reply/SKILL.md
│   └── xhs-content-ops/SKILL.md
├── SKILL.md                        # 技能统一入口（路由到子技能）
├── CLAUDE.md                       # 项目开发指南
├── pyproject.toml
└── README.md
```

## 跨电脑验收

需要把 Private 仓库交给另一台电脑的 Codex 安装和测试时，使用
[另一台电脑部署、使用与验收手册](docs/OTHER-COMPUTER-ACCEPTANCE-TEST.md)。文档包含双 Profile
通用扩展配对、Skill 路由、只读实测、验收标准和结果报告模板。

## 开发

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```
