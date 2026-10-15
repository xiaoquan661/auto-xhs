# xiaohongshu-skills

支持本机多账号和经理机、员工机协作的小红书运营系统。经理机维护账号策略、运营计划、任务投递
和结果汇总；员工机使用本机 Chrome Profile、Bridge、Codex 与 Python CLI 执行任务并回传结果。
源码采用 NexaFlow V0.20 的职责目录：经理维护源版本，员工安装对应的执行版本。

支持 [OpenClaw](https://github.com/openclaw/openclaw) 及所有兼容 `SKILL.md` 格式的 AI Agent 平台。

> **⚠️ 使用建议**：虽然本项目使用真实的用户浏览器和账号环境，但仍建议**控制使用频率**，避免短时间内大量操作。频繁的自动化行为可能触发小红书的风控机制，导致账号受限。

## 版本与架构

| 项目 | 当前说明 |
| --- | --- |
| 程序包版本 | `0.1.0`，来自 `pyproject.toml` |
| 职责目录架构 | NexaFlow V0.20 |
| 获取源码 | 使用 GitHub `main`；Release 为对应发布时点的源码快照 |
| 业务规格 | V1.0、V1.5、V2.0 等功能阶段沿各业务文档维护，与程序包、目录架构分别登记 |

### 当前适配内容

| 层面 | 已采用的实现 |
| --- | --- |
| 源码与资源 | 实现文件归入经理业务包、员工业务包、执行支撑和共用组件；技能、扩展、提示词及界面随职责版本管理 |
| 代码职责 | 抽出共用的本机 HTTP 接口、经理运营汇总及经理知识注入；业务功能主体继续由现有服务模块实现 |
| 调用与交付 | 根 `scripts/` 提供固定入口，模块、静态资源、构建和修复安装沿布局映射定位真实实现 |
| 设备运行 | PC023 维护经理与员工源版本；PC052、PC057、PC065、PC071、PC073 已安装 V0.20 员工执行布局 |
| 跨机通信 | 使用 SSH/SFTP、任务 inbox、员工领取、结果 outbox、经理回收与数据库导入 |

`manager_service.py`、`application_service.py` 等服务保留现有主体业务逻辑，职责交叉处通过上述
接口和模块分开维护。NexaFlow Hub 接口对接与新机器统一一键接入属于后续工作。

2026-10-15 五台员工机的启动、接单、Agent 领取、本地探针回传和经理入库验收均已通过，
账号、登录环境、历史数据与暂停设置沿各机配置保留。验收范围及逐机证据见
[员工机安装与验收](docs/V020-WORKER-DEPLOYMENT.md)；职责与调用关系见
[经理与员工目录说明](docs/V020-ROLE-LAYOUT.md)。

## 功能概览

| 技能                | 说明     | 核心能力                                 |
| ------------------- | -------- | ---------------------------------------- |
| **xhs-auth**        | 认证管理 | 登录检查                                 |
| **xhs-publish**     | 内容发布 | 图文 / 视频 / 长文 / 草稿 / 定时发布     |
| **xhs-explore**     | 内容发现 | 关键词搜索、笔记详情、用户主页、首页推荐 |
| **xhs-interact**    | 社交互动 | 私信、主加、评论、回复、点赞、收藏       |
| **xhs-reply**       | 入站回复 | Codex 评论与私信上下文回复               |
| **xhs-content-ops** | 复合运营 | 竞品分析、热点追踪、批量互动、内容创作   |

各业务的实现、自动化测试和真实账号验收范围见
[需求追踪矩阵](docs/PRD-TRACEABILITY.md)及对应功能记录；文档导航见
[文档索引](docs/README.md)。五机部署的本地探针用于验证安装与任务链，平台业务按具体账号和动作验收。

## 选择部署方式

| 场景 | 部署流程 | 说明入口 |
| --- | --- | --- |
| 本机使用或开发 | 获取完整源码，准备依赖，启动本机 WebUI 并配置账号 | 下方安装步骤 |
| 新经理机 | 获取 `main`，准备依赖，配置经理身份、独立状态目录及员工 SSH 连接 | [第二套系统部署](docs/SECOND-SYSTEM-DEPLOYMENT.md) |
| 新员工机 | 登记岗位与程序实例，准备运行环境及协议目录，再构建和安装本机 V0.20 员工包 | 下方新增机器流程及[员工部署说明](docs/V020-WORKER-DEPLOYMENT.md) |
| 已接入员工机升级 | 经理构建对应员工包，空闲时备份安装，重载服务并验收任务链 | [员工机安装与验收](docs/V020-WORKER-DEPLOYMENT.md) |

### 新增机器

1. 确定经理、员工的唯一编号、Windows 用户、地址及实际运行路径。当前岗位模板为 PC023
   与五台已登记员工；新编号需要同步配置岗位、manifest 和程序实例。
2. 准备 Python 与依赖、Chrome、Codex 登录和 OpenSSH。新员工配置独立的协议状态、inbox/outbox，
   并在经理端登记相同路径；基础环境与连接配置参照第二套系统部署指南。
3. 经理为新员工补齐 `WorkerStation/Workers/AgentWorkers/<机器编号>/`、
   `WorkerStation/Workers/ProgramWorkers/instances/<机器编号>/` 以及 `source-layout.json` 的员工登记，
   再用 `deploy/build-worker-layout.py` 构建对应执行包。
4. 员工已有协议配置与执行服务后，按 `deploy/install-worker-layout.py` 安装 V0.20 布局，
   重载服务并核对实际加载位置。构建、传输、备份及安装命令见员工部署说明。
5. 在员工机绑定账号槽位与 Chrome Profile，按账号输出的扩展路径加载并完成 Bridge 配对和平台登录。
6. 验证接单、Agent 领取、回传及经理入库；再按账号要求完成业务验收和调度配置。

`deploy/employee-bootstrap/onboard.ps1` 保留基础接入入口，使用配套源码 ZIP 和离线依赖目录。
接入包需按当前源码与 `pyproject.toml` 的依赖重新准备；V0.20 构建、安装及新编号登记目前按上述
阶段执行。现有五机已完成已有环境上的布局迁移与任务链验收；统一的一键入口及新编号从零安装
的整体验收属于后续工作。

经理状态、员工账号、Chrome Profile、登录会话和业务历史由各机本地存储维护；项目源码由 GitHub
同步，员工运行版本由经理构建交付。

## 普通用户快速开始

Windows 用户可以直接双击仓库根目录的 `start-auto-xhs.cmd`。启动器会检查环境、复用已经运行的
本地服务并打开 `http://127.0.0.1:8765`。首次进入后按
“添加账号 → 手动打开目标 Profile → 配对扩展 → 检查并确认 UID”的顺序完成账号准备。

更新 Python 后端代码后，可点击 WebUI 顶部的“重启服务”，也可以双击 `restart-auto-xhs.cmd`。
安全重启会先检查执行中任务；服务恢复后，WebUI 通过新的运行实例标识确认后台已经完成更新。

“启动账号”先启动或复用 Bridge，扩展未连接时按需打开绑定 Profile，并核对扩展实例和
Profile。各机浏览器连接与账号可用状态以本机实际回读为准。

WebUI 当前作为本地控制面板，包含账号槽位、Bridge 启停、已有直接任务、评论采集、评论/回复草稿确认、
执行记录、全局暂停、L1 配额、并发设置和脱敏诊断导出。后续新增运营能力默认由 Agent/Python CLI
下发，除非另行明确要求，不再为其增加 WebUI 任务入口。详细步骤见 [普通用户操作手册](docs/USER-GUIDE.md)，常见故障见
[故障恢复指南](docs/TROUBLESHOOTING.md)。

支持**连贯操作** — 你可以用自然语言下达复合指令，Agent 会自动串联多个技能完成任务。例如：

> "搜索刺客信条最火的图文帖子，收藏它，然后告诉我讲了什么"

Agent 会自动执行：搜索 → 筛选图文 → 按点赞排序 → 收藏 → 获取详情 → 总结内容。

## 安装

以下步骤用于完整源码的本机或经理环境。员工执行版本按照上方部署流程由经理构建交付。

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

```powershell
git clone --branch main https://github.com/xiaoquan661/auto-xhs.git C:\auto-xhs\xiaohongshu-skills
Set-Location C:\auto-xhs\xiaohongshu-skills
```

2. 在解压或克隆后的项目根目录中，检测 Python 并准备项目依赖：

```powershell
$projectRoot = (Resolve-Path .).Path
powershell -ExecutionPolicy Bypass -File "$projectRoot\scripts\bootstrap.ps1" -Prepare
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

1. 先按下方说明创建或导入至少一个账号槽位，执行
   `python scripts/cli.py --account brand-a account-sync`，读取返回的 `extension_dir`。
2. 在目标 Chrome Profile 打开 `chrome://extensions/` 并开启**开发者模式**。
3. 点击**加载已解压的扩展程序**，选择 CLI 输出的同一个通用扩展目录。
4. 运行 `account-pair-begin --confirm`，把一次性配对包粘贴到扩展弹窗。

扩展源码随员工版本的 `worker/extension/` 交付，实际加载目录以账号配置中的 `extension_dir`
为准。已部署机器继续使用该账号登记的本机扩展位置。

安装完成后即可使用 — 所有操作都发生在你自己的浏览器里，使用你的真实账号和浏览器环境。

### 多账号并发配置

多账号模式为每个账号保留独立 Chrome Profile、Bridge 端口和任务锁，但所有 Profile
加载同一个通用扩展目录。Profile 的 Cookie 与配对存储仍然互相隔离。

账号名称只是本机别名，不要填写手机号、密码或验证码：

```bash
# 依次创建槽位、启动 Bridge，并把一次性配对包放入剪贴板
python scripts/cli.py account-onboard --name brand-a --confirm
python scripts/cli.py account-onboard --name brand-b --confirm

# 在各账号绑定的 Chrome Profile 中手动登录小红书并完成扩展配对
# 随后读取各槽位连接状态
python scripts/cli.py --account brand-a account-pair-status
python scripts/cli.py --account brand-b account-pair-status

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

首次确认 UID 后，日常任务直接使用槽位绑定身份，绑定在服务重启后继续有效。
手动更换登录账号后完成新 UID 绑定；“检查身份”用于主动读取当前页面身份。

`brand-a`、`account-1` 等名称是本机浏览器槽位，不是固定的小红书账号。槽位的 Profile、
扩展和 Bridge 端口保持不变，当前登录的小红书账号可以更换：

```bash
# 首次使用时确认并记录小红书 UID，日常业务持续复用此绑定
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
旧版账号专属扩展，先禁用或移除旧副本。`account-start` 启动或复用 Bridge，并在扩展离线时按需
打开绑定 Profile；成功状态结合已配对扩展的实际连接回报确认。

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

> "为 brand-a 生成并正式发布这篇图文"

图文与视频通过 Agent/Python CLI 生成、填写并正式发布，长文完成排版后正式发布；
平台回读确认后计入发帖数，WebUI 展示结果并可继续既有准备任务。主动私信仍由 Agent/CLI 下发并由 WebUI 展示结果；收到的新私信和自己笔记下
的新评论由 Codex 通过 `reply-inbox` 读取上下文、生成决策、发送并回读，需要业务判断的内容进入待处理。详见
[`docs/AUTO-XHS-V2-INTELLIGENT-REPLY.md`](docs/AUTO-XHS-V2-INTELLIGENT-REPLY.md)。

**个性化私信（Codex 流程）：**

> “使用 brand-a 给这 3 位博主分别写一条合作私信，目的是沟通内容合作。”

Codex 先识别首次私信或已有会话，结合用户给出的联系目的和真实上下文为每位联系人生成不同文本，
随后直接发送、回读并汇总。单批最多 10 人；逐人记录 `SUCCESS`、`FAILED` 或
`RESULT_UNKNOWN`，单人失败不阻断其余人，恢复批次不重发成功或结果未知项。

**入站回复（Codex 流程）：**

在 WebUI“账号回复规则”中配置评论/私信开关、人设和知识，并显式启用。
每次手动或计划运行 `reply-inbox` 都只处理一批新事件。Codex 读取笔记、评论线程或近期私信会话，
结合账号人设、知识和规则生成 `reply` 或 `skip`；Python/Bridge 负责发送前核验和页面回读。

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
> 或关闭 Chrome。进入 READY 以 Profile、扩展实例和槽位已绑定 UID 为准。

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
| `post-comment`              | Agent理解内容并生成评论后，逐条提交及回读             |
| `home-engagement`           | 单次首页会话完成浏览和点赞并输出逐篇记录               |
| `reply-comment`             | 单次回复执行器；规则周期通过专用操作调用并要求回读     |
| `like-feed`                 | 点赞 / 取消点赞                                       |
| `favorite-feed`             | 收藏 / 取消收藏                                       |
| `publish` / `publish-video` | 旧版一步发布命令；已明确禁用                           |
| `fill-publish`              | Agent 填写图文并正式发布                     |
| `fill-publish-video`        | Agent 填写视频并正式发布                     |
| `click-publish`             | 携带任务 ID 继续已有准备任务并正式发布                |
| `save-draft`                | 携带任务 ID；用户取消发布时保存草稿                    |
| `long-article`              | Agent 填写长文、创建任务并返回排版模板                 |
| `select-template`           | 携带任务 ID 选择长文模板                               |
| `next-step`                 | 携带任务 ID 填写长文描述并正式发布                        |
| `diagnose-404`              | 读取扩展拦截器捕获的 404 诊断信息                     |
| `check-risk`                | 汇总当前页面与接口的风险状态                          |
| `get-netlog` / `risk-report` | 读取已启用的 NetLog 并生成诊断报告                   |

退出码：`0` 成功 · `1` 未登录 · `2` 错误

业务验收按设备、账号、动作、时间及平台回读证据登记。发布、互动、采集和回复的具体记录见
[项目当前状态](project-memory/03-当前状态.md)及[工作记录](project-memory/工作记录/README.md)。

## 项目结构

本项目使用 PC023 经理工作区维护经理业务实现和员工源版本。PC052、PC057、PC065、PC071、
PC073 各有员工规则与程序实例定义。源码职责与真实文件位置见
[经理与员工目录说明](docs/V020-ROLE-LAYOUT.md)和根 `source-layout.json`。

```text
xiaohongshu-skills/
├── ManagerStation/Managers/
│   ├── AGENTS.md
│   └── PC023/
│       ├── AGENTS.md、manager_manifest.json
│       ├── function_packages/xiaohongshu-skills/0.1.0/manager/
│       │   └── runtime/
│       │       ├── scripts/                 # 计划、投递、回收、汇总、经理存储
│       │       └── webui/                   # 经理工作台与远端员工管理面板
│       ├── worker_packages/xiaohongshu-skills/0.1.0/worker/
│       │   ├── runtime/
│       │   │   ├── scripts/                 # 本机业务服务、CLI、xhs自动化库
│       │   │   ├── execution/               # 员工任务、账号环境、Bridge、进程
│       │   │   ├── shared/                  # 共用数据与本机应用HTTP接口
│       │   │   └── webui/                   # 员工本机界面与共用图标
│       │   ├── extension/                   # 浏览器扩展
│       │   ├── skills/                      # 员工业务技能与任务入口指引
│       │   └── prompts/                     # 评论与私信回复提示词
│       ├── package_relations/                # 既有功能版本与职责源码配套关系
│       ├── migration/                        # 迁移、部署与验收记录
│       └── scripts/development/             # 准备、构建与代码修复工具
├── WorkerStation/Workers/
│   ├── AgentWorkers/
│   │   ├── AGENTS.md
│   │   └── PC052、PC057、PC065、PC071、PC073/ # AGENTS、Personal、Role、manifest
│   └── ProgramWorkers/instances/<PC编号>/    # 程序实例和部署路径定义
├── scripts/                                 # 稳定调用入口和源码加载器
├── source-layout.json                       # Python模块、角色和资源目录映射
├── webui-layout.json                        # HTTP静态资源URL与源码位置映射
├── docs/、tests/、project-memory/             # 开发、验证和交接基准
├── package_templates/                       # 正式功能包构建模板
├── deploy/                                  # 员工接入、V0.20构建与安装工具
├── AGENTS.md、SKILL.md
└── pyproject.toml、uv.lock
```

`xiaohongshu-skills/0.1.0` 标识项目源版本；正式业务功能包按既有功能编号在经理 manifest 的 `paths.formal_function_packages` 指向的项目外功能包目录 维护。员工实例定义引用经理维护的同一份员工源代码和本机部署根。
固定命令入口继续使用 `scripts/cli.py`、`scripts/manager_cli.py`、`scripts/manager_worker.py`。
模块导入与资源定位由路径映射解析到对应角色实现。

员工机安装后，执行版本按本机编号绑定：

```text
C:\auto-xhs\xiaohongshu-skills\
├── WorkerStation/Workers/
│   ├── AgentWorkers/<本机编号>/              # 本机岗位规则
│   └── ProgramWorkers/
│       ├── packages/xiaohongshu-skills/0.1.0/worker/
│       │                                    # 业务、执行支撑、共用组件、技能和扩展源
│       ├── instances/<本机编号>/             # 程序版本与运行路径
│       └── environments/existing-python/    # 本机Python环境引用
├── scripts/                                 # 员工固定调用入口
├── source-layout.json                       # 绑定本机WorkerStation实现
└── webui-layout.json                        # 员工界面资源映射
```

主要运行数据位置：

| 内容 | 路径 |
| --- | --- |
| 经理配置与数据库 | 当前 PC023 为 `C:\auto-xhs\manager-state`，由 `XHS_MANAGER_HOME` 指定 |
| 员工任务收件箱 | `C:\auto-xhs\manager-task-inbox` |
| 员工结果发件箱 | `C:\auto-xhs\manager-task-outbox` |
| 员工协议状态与执行记录 | `%USERPROFILE%\.xhs\auto-xhs\manager-worker` |
| 账号与浏览器环境 | 由各机账号配置引用的 Chrome Profile、配对及扩展目录 |

## 跨电脑验收

多机部署以[选择部署方式](#选择部署方式)和[员工机安装与验收](docs/V020-WORKER-DEPLOYMENT.md)
为入口，环境与 SSH 配置参照[第二套系统部署](docs/SECOND-SYSTEM-DEPLOYMENT.md)。账号、Profile
和扩展配对参考[普通用户操作手册](docs/USER-GUIDE.md)；业务回收与入库核对参考
[运营验收查询](docs/OPERATIONS-ACCEPTANCE-DATABASE.md)。执行路径使用本机布局映射和账号实际配置。

## 开发

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```
