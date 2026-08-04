# xiaohongshu-skills

小红书自动化 Skills，直接使用你已登录的浏览器和真实账号，以普通用户的方式操作小红书。

支持 [OpenClaw](https://github.com/anthropics/openclaw) 及所有兼容 `SKILL.md` 格式的 AI Agent 平台（如 Claude Code）。

> **⚠️ 使用建议**：虽然本项目使用真实的用户浏览器和账号环境，但仍建议**控制使用频率**，避免短时间内大量操作。频繁的自动化行为可能触发小红书的风控机制，导致账号受限。

## 功能概览


| 技能                | 说明     | 核心能力                                   |
| ------------------- | -------- | ------------------------------------------ |
| **xhs-auth**        | 认证管理 | 登录检查、扫码登录、手机验证码登录         |
| **xhs-publish**     | 内容发布 | 图文 / 视频 / 长文发布、定时发布、分步预览 |
| **xhs-explore**     | 内容发现 | 关键词搜索、笔记详情、用户主页、首页推荐   |
| **xhs-interact**    | 社交互动 | 评论、回复、点赞、收藏                     |
| **xhs-content-ops** | 复合运营 | 竞品分析、热点追踪、批量互动、内容创作     |

支持**连贯操作** — 你可以用自然语言下达复合指令，Agent 会自动串联多个技能完成任务。例如：

> "搜索刺客信条最火的图文帖子，收藏它，然后告诉我讲了什么"

Agent 会自动执行：搜索 → 筛选图文 → 按点赞排序 → 收藏 → 获取详情 → 总结内容。

## 安装

### 前置条件

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/) 包管理器
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
cd <your-agent-project>/skills/
git clone https://github.com/autoclaw-cc/xiaohongshu-skills.git
```

2. 安装 Python 依赖：

```bash
cd xiaohongshu-skills
uv sync
```

### 第二步：安装浏览器扩展

扩展让 AI 能够在你的浏览器中以你的身份操作小红书，使用的是你真实的登录状态和账号信息。
当前版本使用一套通用扩展；不同 Chrome Profile 通过各自的 `chrome.storage.local` 保存配对，
不再为每个账号复制扩展代码或在扩展磁盘文件中写入长期令牌。

1. 先按下方说明创建或导入至少一个账号槽位；CLI 会把通用扩展部署到
   `~/.xhs/universal-extension`。
2. 在目标 Chrome Profile 打开 `chrome://extensions/` 并开启**开发者模式**。
3. 点击**加载已解压的扩展程序**，选择 CLI 输出的同一个通用扩展目录。
4. 运行 `account-pair-begin --confirm`，把一次性配对包粘贴到扩展弹窗。

安装完成后即可使用 — 所有操作都发生在你自己的浏览器里，使用你的真实账号和浏览器环境。

### 多账号并发配置

多账号模式为每个账号保留独立 Chrome Profile、Bridge 端口和任务锁，但所有 Profile
加载同一个通用扩展目录。Profile 的 Cookie 与配对存储仍然互相隔离。

账号名称只是本机别名，不要填写手机号、密码或验证码：

```bash
# 创建两个账号环境；端口会自动分配
python scripts/cli.py account-add --name brand-a
python scripts/cli.py account-add --name brand-b

# 分别启动独立 Chrome
python scripts/cli.py --account brand-a account-start
python scripts/cli.py --account brand-b account-start

# 首次启动尚未配对时会报告未就绪；分别生成配对包并粘贴到对应 Profile 的扩展弹窗
python scripts/cli.py --account brand-a account-pair-begin --confirm
python scripts/cli.py --account brand-b account-pair-begin --confirm

# 在各自 Chrome 中完成一次登录
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

之后给所有业务命令加上目标账号：

```bash
python scripts/cli.py --account brand-a search-feeds --keyword "咖啡"
python scripts/cli.py --account brand-b search-feeds --keyword "露营"
```

不同账号的命令可由两个终端或 Agent 并发执行。同一账号的命令由账号锁串行执行，避免两个
任务同时抢占同一个页面。发布、评论、点赞等操作仍应逐个确认，并保持合理频率。

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
一个账号别名。首次启动后，在该 Profile 的 `chrome://extensions` 中加载通用扩展目录并完成
一次性配对。多个 Profile 应共用这一目录，但每个 Profile 只能保留一个 XHS Bridge 实例；若已加载
旧版账号专属扩展，先禁用或移除旧副本。`account-start` 只有 Bridge 服务和已配对扩展都连接时
才返回 `success: true`。

## 使用方式

### 作为 AI Agent 技能使用（推荐）

安装到 skills 目录后，直接用自然语言与 Agent 对话即可。Agent 会根据你的意图自动路由到对应技能。

**认证登录：**

> "登录小红书" / "检查登录状态"

**搜索浏览：**

> "搜索关于露营的笔记" / "查看这条笔记的详情"

**发布内容：**

> "帮我发一条图文笔记，标题是…，配图是…"

**社交互动：**

> "给这条笔记点赞" / "收藏这条帖子" / "评论：写得太好了"

**复合操作：**

> "搜索竞品账号最近的爆款笔记，分析他们的选题方向"

### 作为 CLI 工具使用

所有功能也可以通过命令行直接调用，输出 JSON 格式，便于脚本集成。

```bash
# 检查登录状态
python scripts/cli.py check-login

# 扫码登录
python scripts/cli.py login

# 搜索笔记
python scripts/cli.py search-feeds --keyword "关键词"

# 带筛选条件
python scripts/cli.py search-feeds \
  --keyword "关键词" \
  --sort-by "最多点赞" \
  --note-type "图文"

# 查看笔记详情
python scripts/cli.py get-feed-detail \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN

# 图文发布（分步：填写 → 预览 → 确认）
python scripts/cli.py fill-publish \
  --title-file title.txt \
  --content-file content.txt \
  --images "/abs/path/pic1.jpg" "/abs/path/pic2.jpg"
python scripts/cli.py click-publish

# 一步发布图文
python scripts/cli.py publish \
  --title-file title.txt \
  --content-file content.txt \
  --images "/abs/path/pic1.jpg" \
  --tags "标签1" "标签2"

# 视频发布
python scripts/cli.py publish-video \
  --title-file title.txt \
  --content-file content.txt \
  --video "/abs/path/video.mp4"

# 点赞 / 收藏 / 评论
python scripts/cli.py like-feed --feed-id FEED_ID --xsec-token XSEC_TOKEN
python scripts/cli.py favorite-feed --feed-id FEED_ID --xsec-token XSEC_TOKEN
python scripts/cli.py post-comment --feed-id FEED_ID --xsec-token XSEC_TOKEN --content "评论内容"
```

> 第一次运行时，若 Chrome 未打开，CLI 会自动启动它。

## CLI 命令参考


| 子命令                      | 说明                                                |
| --------------------------- | --------------------------------------------------- |
| `account-add`               | 创建独立 Chrome Profile、Bridge 与账号槽位          |
| `account-discover`          | 列出已有 Chrome Profile 及绑定状态                  |
| `account-import`            | 绑定已有 Chrome Profile，保留登录状态               |
| `account-list`              | 列出所有已配置账号                                  |
| `account-start`             | 启动目标账号的 Bridge 和 Chrome                     |
| `account-status`            | 检查目标账号的服务和扩展连接状态                    |
| `account-sync`              | 将最新代码同步到所有 Profile 共用的通用扩展目录     |
| `account-doctor`            | 只读诊断账号配置、Profile、扩展路由、端口和连接状态 |
| `account-pair-begin`        | 生成目标账号的一次性 Profile 配对包                 |
| `account-pair-status`       | 查看 Profile 配对与扩展连接状态                     |
| `account-unpair`            | 撤销 Profile 配对并轮换长期连接令牌                 |
| `account-connection-enroll` | 仅供旧版账号专属扩展迁移使用                        |
| `account-identity`          | 读取当前小红书 UID，并与账号槽位的身份记录比较      |
| `account-switch-begin`      | 暂停业务任务并退出当前登录，开始安全换号            |
| `account-switch-complete`   | 核验并绑定新登录身份，恢复业务任务                  |
| `account-switch-cancel`     | 取消尚未完成的换号流程                              |
| `account-switch-history`    | 查看本机保存的换号记录                              |
| `check-login`               | 检查登录状态，返回用户昵称和小红书号                |
| `login`                     | 获取登录二维码，等待扫码，登录后返回用户信息        |
| `delete-cookies`            | 通过页面退出当前登录                                |
| `list-feeds`                | 获取首页推荐 Feed                                   |
| `search-feeds`              | 关键词搜索笔记（支持排序/类型/时间/范围/位置筛选）  |
| `get-feed-detail`           | 获取笔记完整内容和评论                              |
| `user-profile`              | 获取用户主页信息和帖子列表                          |
| `post-comment`              | 对笔记发表评论                                      |
| `reply-comment`             | 回复指定评论                                        |
| `like-feed`                 | 点赞 / 取消点赞                                     |
| `favorite-feed`             | 收藏 / 取消收藏                                     |
| `publish`                   | 一步发布图文                                        |
| `publish-video`             | 一步发布视频                                        |
| `fill-publish`              | 填写图文表单（不发布，供预览）                      |
| `fill-publish-video`        | 填写视频表单（不发布，供预览）                      |
| `click-publish`             | 确认发布（点击发布按钮）                            |
| `save-draft`                | 保存为草稿                                          |
| `long-article`              | 长文模式：填写 + 一键排版                           |
| `select-template`           | 选择长文排版模板                                    |
| `next-step`                 | 长文下一步 + 填写描述                               |

退出码：`0` 成功 · `1` 未登录 · `2` 错误

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
│   ├── image_downloader.py         # 媒体下载（SHA256 缓存）
│   ├── title_utils.py              # UTF-16 标题长度计算
│   └── run_lock.py                 # 单实例锁
├── skills/                         # Claude Code Skills 定义
│   ├── xhs-auth/SKILL.md
│   ├── xhs-publish/SKILL.md
│   ├── xhs-explore/SKILL.md
│   ├── xhs-interact/SKILL.md
│   └── xhs-content-ops/SKILL.md
├── SKILL.md                        # 技能统一入口（路由到子技能）
├── CLAUDE.md                       # 项目开发指南
├── pyproject.toml
└── README.md
```

## 开发

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```
