---
name: xiaohongshu-skills
description: |
  小红书单账号及多账号自动化技能集合。通过本项目的 Python CLI、通用 Chrome
  扩展、Bridge 和本地 WebUI 管理账号槽位、半自动换号、内容发现、互动和内容运营。
  当用户要求配置、启动、识别、切换或操作一个或多个小红书账号时使用。
---

# 小红书自动化 Skills

根据用户意图路由到对应子技能，并始终通过本项目执行。

## 规则优先级

按以下顺序解释规则：

```text
docs/PRD.md 的版本规则
→ 本文件的路由和强制边界
→ 子 SKILL.md 的执行流程
→ README.md 和 docs/USER-GUIDE.md
```

下层说明不得覆盖上层规则。

## 版本与执行状态

- **V1.0**：当前实现基线。发布、定时发布、私信和公开资料修改仍由产品入口禁用；
  指定评论和回复仍使用当前确认链；Chrome 由用户手动打开。
- **V1.5**：已批准的下一阶段规则。目标开放发布、定时发布、私信、公开资料修改、
  评论一次点击直发、规则化自动回复，以及 Bridge 与绑定 Chrome Profile 联合启动。
- **执行门槛**：V1.5 规则不等于代码已经可用。在能力注册表、CLI/WebUI 执行链、测试和
  真实设备验收完成前，不得绕过 `CAPABILITY_DISABLED`，不得把不存在的命令描述为已执行。

## 强制边界

所有小红书操作只能通过工作区中的 `python scripts/cli.py <子命令>` 完成。

- 不使用其他小红书 MCP、Go 工具或外部自动化项目。
- 多账号命令统一使用 `python scripts/cli.py --account <账号别名> <子命令>`。
- 先确认目标槽位；不同账号可以并发，同一账号必须由 CLI 账号锁串行执行。
- 高风险业务命令必须核验槽位记录 UID 与当前登录 UID。
- 所有 Profile 使用当前工作区的同一份 `extension`，每个 Profile 独立配对槽位。
- 文件路径使用绝对路径，CLI 输出以 JSON 结构化反馈。
- 完成当前任务后停止，不主动扩展成持续任务或批量操作。

## 意图路由

1. 登录状态、退出、半自动换号、配对 → `xhs-auth`。
2. 图文、视频、长文、草稿、定时发布 → `xhs-publish`。
3. 首页、搜索、详情、评论读取、用户主页 → `xhs-explore`。
4. 评论、随机评论、回复、点赞、收藏 → `xhs-interact`。
5. 竞品分析、热点追踪、内容创作、互动管理 → `xhs-content-ops`。
6. 私信或公开资料修改 → 识别为 V1.5 目标能力；当前没有完整 CLI 执行链时明确报告待实现，
   不猜测命令或改用外部工具。

## 账号与登录规则

V1.5 产品登录只保留半自动流程：

```text
检查登录状态
→ 自动退出当前账号
→ 用户在绑定的 Chrome Profile 中手动登录
→ 读取并核验新 UID
→ 完成换号
```

- 不从 Skill 引导二维码登录或手机验证码登录。
- 换号使用 `account-switch-begin --confirm` 和 `account-switch-complete`。
- 换号期间只允许认证、身份和换号命令，不绕过业务保护。

## V1.5 账号联合启动规则

目标流程是把“启动 Bridge”升级为“启动账号”：

```text
启动或复用 Bridge
→ 检查槽位绑定 Profile 是否已经连接
→ 未连接时自动打开 chrome_user_data_dir + chrome_profile_directory
→ 等待通用扩展连接
→ 核对 Profile 声明、扩展实例和 UID
→ READY
```

- 已连接正确 Profile 时不重复打开。
- 错误 Profile 返回 `PROFILE_MISMATCH`，不得误报 READY。
- Windows 登录自启动只恢复用户明确启用的槽位，顺序为 Bridge → 绑定 Profile。
- 系统可以按上述规则打开 Chrome，但不自动关闭用户的 Chrome。
- 在联合启动代码完成前，继续按 V1.0 真实状态提示用户手动打开 Profile。

## V1.5 对外操作规则

| 操作 | 授权和确认规则 |
|---|---|
| 图文、视频、长文发布 | 必须先填写，在浏览器预览，展示账号、内容和素材并确认后点击发布；禁止一步直发 |
| 定时发布 | 在发布预览中同时确认账号、内容和发布时间 |
| 保存草稿 | 用户取消发布时可以保存草稿，不得把保存草稿描述为已经发布 |
| 指定评论 | 用户点击创建当前任务后直接发送，不增加第二次逐条确认 |
| 随机评论 | 当前点击一次性授权本账号、本任务 1–3 条，每条单独记录结果 |
| 自动回复 | 用户先按账号启用规则；符合规则的回复不逐条确认，但必须受时间、配额、暂停和记录约束 |
| 私信 | 一次点击只授权当前目标和当前文本，不扩展成批量私信 |
| 公开资料修改 | 展示修改前后差异，用户确认一次后执行 |

V1.5 执行链未完成前，上表是目标规则，不授权绕过当前服务层限制。

## 子技能概览

| 子技能 | 职责 |
|---|---|
| `xhs-auth` | 状态检查、退出、配对、身份核验和半自动换号 |
| `xhs-publish` | V1.5 发布预览、确认、草稿和定时发布目标流程 |
| `xhs-explore` | 首页、搜索、详情、评论读取和用户主页 |
| `xhs-interact` | 评论、随机评论、规则化回复、点赞和收藏 |
| `xhs-content-ops` | 组合研究、创作、发布和互动流程 |

## 新电脑准备

首次运行前执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Prepare
```

脚本优先使用项目 `.venv` 或兼容的本机 Python，并检查 CLI 与 WebUI 运行依赖。只有返回
`python_missing` 时，才能在用户同意后增加 `-InstallMissingPython`。后续命令使用返回的
`python_executable`。

## 当前可执行的账号准备

```powershell
# 创建槽位并准备配对
python scripts/cli.py account-onboard --name brand-a --confirm

# 或绑定已有 Profile
python scripts/cli.py account-discover
python scripts/cli.py account-import `
  --name brand-a `
  --user-data-dir "C:\Users\<用户>\AppData\Local\Google\Chrome\User Data" `
  --profile-directory "Profile 2"

# 检查配对与账号状态
python scripts/cli.py --account brand-a account-pair-status
python scripts/cli.py account-doctor --name brand-a --require-ready
```

## 失败处理

- **依赖不可用**：运行 `scripts/bootstrap.ps1 -Prepare`，不要改用未安装依赖的系统 Python。
- **Bridge 未启动**：运行只读诊断并按真实错误修复。
- **扩展未连接**：运行账号联合启动流程，按需打开绑定 Profile；连接后仍须核验 Profile 和扩展实例。
- **Profile 不一致**：停止业务命令，展示期望与实际 Profile，不自动改绑。
- **账号正在换号**：只执行身份或换号命令，登录新账号后运行 `account-switch-complete`。
- **UID 不一致**：停止发布和互动，先完成身份核验。
- **目标能力未实现**：明确报告 V1.5 规格已批准但执行链待实现，不调用外部工具替代。
