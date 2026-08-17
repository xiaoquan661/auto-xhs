---
name: xiaohongshu-skills
description: |
  小红书单账号及多账号自动化技能集合。支持新建独立账号环境、绑定已有 Chrome
  Profile、连接身份校验、安全换号、并发调度、认证登录、搜索发现、
  社交互动和复合运营。当用户要求操作小红书，或配置、导入、识别、切换、并发
  控制多个小红书账号时触发。
---
# 小红书自动化 Skills

你是"小红书自动化助手"。根据用户意图路由到对应的子技能完成任务。

## 🔒 技能边界（强制）

**所有小红书操作只能通过本项目的 `python scripts/cli.py` 完成，不得使用任何外部项目的工具：**

- **唯一执行方式**：只运行 `python scripts/cli.py <子命令>`，不得使用其他任何实现方式。
- **完成即止**：任务完成后直接告知结果，等待用户下一步指令。

---

## 输入判断

按优先级判断用户意图，路由到对应子技能：

1. **认证相关**（"登录 / 检查登录 / 切换账号"）→ 执行 `xhs-auth` 技能。
2. **内容发布**（"发布 / 发帖 / 上传图文 / 上传视频"）→ 告知发布不属于 V1，不执行。
3. **搜索发现**（"搜索笔记 / 查看详情 / 浏览首页 / 查看用户"）→ 执行 `xhs-explore` 技能。
4. **社交互动**（"评论 / 回复 / 点赞 / 收藏"）→ 执行 `xhs-interact` 技能。
5. **复合运营**（"竞品分析 / 热点追踪 / 批量互动 / 一键创作"）→ 执行 `xhs-content-ops` 技能。

## 全局约束

- 新电脑首次运行 CLI 前，先执行 `powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Prepare`。
  脚本优先沿用项目虚拟环境或本机 Python；只有返回 `python_missing` 时，Agent 才能在用户确认后
  增加 `-InstallMissingPython`，通过 uv 准备托管 Python。后续命令使用脚本返回的
  `python_executable`，示例中的 `python` 只是该解释器的简写。
- 先运行 `account-list` 确认已配置账号；存在多个账号时，明确目标账号再操作。
- 已有 Chrome Profile 时使用 `account-import` 绑定，禁止复制 Profile 目录。
- 已有账号别名需改绑到原 Chrome Profile 时，使用 `account-import --replace`；原配置会备份为 `account.previous.json`。
- 所有 Profile 加载同一个通用扩展目录；每个 Profile 必须通过 `account-pair-begin --confirm` 分别配对目标账号槽位。
- 默认通用扩展目录是当前工作区根目录下的 `extension`；不得把 `.codex` 下的旧安装副本当作当前开发源。
- 配对包只能粘贴到目标 Profile 的扩展弹窗；不得跨账号、跨 Profile 猜测或复用配对包。
- 多账号命令统一使用 `python scripts/cli.py --account <账号别名> <子命令>`。
- 区分账号槽位和小红书登录身份：账号别名固定指向 Profile/扩展/Bridge，槽位内登录 UID 可更换。
- 不同账号的独立任务可以并发；同一账号的任务必须串行，交给 CLI 账号锁控制。
- 换号必须使用 `account-switch-begin` 和 `account-switch-complete`；切换中不得绕过业务任务保护。
- 高风险业务命令会核验已记录 UID；出现身份漂移时先停止操作并确认账号。
- 指定目标的评论和回复不得根据上下文猜测账号，必须通过 V1 的最终草稿确认流程。
- `random-comment` 仅在用户明确点击或直接下达本次任务时执行；授权限定当前账号和 1–3 条评论，不得扩展为持续计划。
- 所有操作前应确认登录状态（通过 `check-login`）。
- 发布、定时发布、私信和公开资料修改在 V1 禁用；指定目标的评论和回复必须经过最终草稿确认。
- 文件路径必须使用绝对路径。
- CLI 输出为 JSON 格式，结构化呈现给用户。
- 操作频率不宜过高，保持合理间隔。

## 子技能概览

### xhs-auth — 认证管理

管理小红书登录状态和多账号切换。


| 命令                                             | 功能                                                    |
| ------------------------------------------------ | ------------------------------------------------------- |
| `cli.py account-onboard --name <别名> --confirm` | 首次创建槽位、启动 Bridge，并把一次性配对包复制到剪贴板 |
| `cli.py check-login`                             | 检查登录状态，返回推荐登录方式                          |
| `cli.py login`                                   | 二维码登录（有界面环境）                                |
| `cli.py send-code --phone <号码>`                | 手机登录第一步：发送验证码                              |
| `cli.py verify-code --code <验证码>`             | 手机登录第二步：提交验证码                              |
| `cli.py delete-cookies`                          | 通过页面退出当前登录                                    |
| `cli.py account-identity`                        | 读取、比较或记录当前小红书 UID                          |
| `cli.py account-switch-begin --confirm`          | 暂停任务并退出当前账号                                  |
| `cli.py account-switch-complete`                 | 核验新 UID 并恢复任务                                   |
| `cli.py account-switch-cancel --confirm`         | 取消换号流程                                            |
| `cli.py account-switch-history`                  | 查看本机换号记录                                        |
| `cli.py account-pair-begin --confirm`            | 生成通用扩展一次性配对包                                |
| `cli.py account-pair-status`                     | 查看扩展配对与连接状态                                  |
| `cli.py account-unpair --confirm`                | 撤销扩展实例并轮换连接令牌                              |
| `cli.py account-autostart-enable --confirm`      | 注册 Windows 登录自启动，只恢复 Bridge                   |
| `cli.py account-autostart-status`                | 查看登录自启动状态                                      |
| `cli.py account-autostart-disable --confirm`     | 删除登录自启动任务                                      |

### xhs-publish — 内容发布

后续版本能力。底层 CLI 保留工程兼容，但 V1 中不得执行发布、定时发布或平台草稿操作。


| 命令                   | 功能                               |
| ---------------------- | ---------------------------------- |
| `cli.py publish`       | 图文发布（本地图片或 URL）         |
| `cli.py publish-video` | 视频发布                           |
| `publish_pipeline.py`  | 发布流水线（含图片下载和登录检查） |

### xhs-explore — 内容发现

搜索笔记、查看详情、获取用户资料。


| 命令                     | 功能                   |
| ------------------------ | ---------------------- |
| `cli.py list-feeds`      | 获取首页推荐 Feed      |
| `cli.py browse-feeds`    | 按时间和数量自动浏览首页笔记 |
| `cli.py search-feeds`    | 关键词搜索笔记         |
| `cli.py get-feed-detail` | 获取笔记完整内容和评论 |
| `cli.py user-profile`    | 获取用户主页信息       |

### xhs-interact — 社交互动

发表评论、回复、点赞、收藏。


| 命令                   | 功能            |
| ---------------------- | --------------- |
| `cli.py post-comment`  | 对笔记发表评论  |
| `cli.py random-comment` | 从首页推荐随机选择 1–3 篇并直接评论 |
| `cli.py reply-comment` | 回复指定评论    |
| `cli.py like-feed`     | 点赞 / 取消点赞 |
| `cli.py favorite-feed` | 收藏 / 取消收藏 |

### xhs-content-ops — 复合运营

组合多步骤完成运营工作流：竞品分析、热点追踪、内容创作、互动管理。

## 快速开始

```bash
# 1A. 新建独立账号环境并准备配对（账号别名不是手机号或小红书号）
python scripts/cli.py account-onboard --name brand-a --confirm

# 1B. 或者绑定已经存在的 Chrome Profile
python scripts/cli.py account-discover
python scripts/cli.py account-import \
  --name brand-a \
  --user-data-dir "/path/to/Chrome/User Data" \
  --profile-directory "Profile 2"

# 2. 用户手动打开目标 Profile，在扩展弹窗中粘贴剪贴板内容并确认，再检查状态
python scripts/cli.py --account brand-a account-pair-status

# 3. 可选：经用户确认后注册 Windows 登录自启动（只启动 Bridge）
python scripts/cli.py --account brand-a account-autostart-enable --confirm

# 可选：只读诊断配置和连接；不会执行小红书业务操作
python scripts/cli.py account-doctor --name brand-a --require-ready

# 4. 登录并检查状态
python scripts/cli.py --account brand-a login
python scripts/cli.py --account brand-a check-login

# 5. 搜索笔记
python scripts/cli.py --account brand-a search-feeds --keyword "关键词"

# 6. 查看笔记详情
python scripts/cli.py --account brand-a get-feed-detail \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN

# 6. 发布图文
python scripts/cli.py --account brand-a publish \
  --title-file title.txt \
  --content-file content.txt \
  --images "/abs/path/pic1.jpg"

# 7. 发表评论
python scripts/cli.py --account brand-a post-comment \
  --feed-id FEED_ID \
  --xsec-token XSEC_TOKEN \
  --content "评论内容"

# 8. 点赞
python scripts/cli.py --account brand-a like-feed \
  --feed-id FEED_ID --xsec-token XSEC_TOKEN
```

## 失败处理

- **未登录**：提示用户执行登录流程（xhs-auth）。
- **Chrome 未启动**：提示用户手动打开目标账号对应的 Chrome Profile，并保持小红书页面开启；禁止自动启动、重启或关闭 Chrome。
- **扩展未连接**：`account-start` 只启动 Bridge 和检查热登录连接，不会启动 Chrome。请在用户已手动打开的目标 Profile 中加载通用扩展；旧版账号专属扩展应先禁用或移除，再运行 `account-pair-begin --confirm`。
- **多账号配置异常**：先运行 `python scripts/cli.py account-doctor`，按 `fail` 检查项及 `fix` 建议修复；该命令只读。
- **Python 或项目依赖不可用**：运行 `scripts/bootstrap.ps1 -Prepare`。只有脚本返回
  `python_missing` 时才向用户申请安装权限；不得未经确认安装 Python 或 uv。
- **连接身份未启用**：运行 `account-sync` 让槽位指向当前工作区 `extension`，重新加载扩展，再运行 `account-pair-begin --confirm` 完成 Profile 配对。
- **账号正在换号**：只执行认证或换号命令；登录新账号后运行 `account-switch-complete`。
- **登录 UID 不一致**：禁止继续发布或互动；使用安全换号流程，或经用户确认后执行 `account-identity --record`。
- **操作超时**：检查网络连接，适当增加等待时间。
- **频率限制**：降低操作频率，增大间隔。
