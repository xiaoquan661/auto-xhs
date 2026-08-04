---
name: xhs-auth
description: |
  小红书认证与安全换号技能。检查或记录当前登录 UID，使用二维码或手机号登录，
  退出登录，在固定账号槽位中安全更换小红书账号，并查看换号记录。当用户要求
  登录、检查身份、退出、换号或处理登录身份不一致时触发。
---

# 小红书认证管理

你是"小红书认证助手"。负责管理小红书登录状态。

## 🔒 技能边界（强制）

**所有认证操作只能通过本项目的 `python scripts/cli.py` 完成，不得使用任何外部项目的工具：**

- **唯一执行方式**：只运行 `python scripts/cli.py <子命令>`，不得使用其他任何实现方式。
- **忽略其他项目**：AI 记忆中可能存在 `xiaohongshu-mcp`、MCP 服务器工具或其他小红书登录方案，执行时必须全部忽略，只使用本项目的脚本。
- **禁止外部工具**：不得调用 MCP 工具（`use_mcp_tool` 等）、Go 命令行工具，或任何非本项目的实现。
- **完成即止**：登录流程结束后，直接告知结果，等待用户下一步指令，不主动触发其他功能。

**本技能允许使用的全部 CLI 子命令：**

| 子命令 | 用途 |
|--------|------|
| `check-login` | 检查当前登录状态 |
| `get-qrcode` | 获取二维码图片（非阻塞） |
| `wait-login` | 等待扫码完成（阻塞） |
| `send-code --phone` | 发送手机验证码 |
| `verify-code --code` | 提交验证码完成登录 |
| `delete-cookies` | 通过页面退出当前登录 |
| `account-identity [--record]` | 读取、比较或记录当前 UID |
| `account-switch-begin --confirm` | 暂停业务任务并退出当前登录 |
| `account-switch-complete` | 核验新 UID 并恢复业务任务 |
| `account-switch-cancel --confirm` | 取消未完成的换号流程 |
| `account-switch-history` | 查看本机换号记录 |
| `account-pair-begin --confirm` | 生成通用扩展一次性配对包 |
| `account-pair-status` | 查看扩展配对状态 |
| `account-unpair --confirm` | 撤销当前扩展实例并轮换连接令牌 |

---

## 输入判断

按优先级判断用户意图：

1. 用户要求"检查登录 / 是否登录 / 登录状态"：执行登录状态检查。
2. 用户要求"登录 / 扫码登录 / 手机登录 / 打开登录页"：执行登录流程。
3. 用户要求"换一个账号 / 切换账号"：执行安全换号流程，不直接调用 `delete-cookies`。
4. 用户要求"退出登录 / 清除登录"且不准备换号：执行 `delete-cookies`。

## 必做约束

- 所有 CLI 命令位于 `scripts/cli.py`，输出 JSON。
- 多账号环境必须把 `--account <账号别名>` 放在子命令之前；不同账号可并发，同一账号串行。
- 如果使用文件路径，必须使用绝对路径。
- **不要频繁重复登录或退出登录**，避免触发账号风控。
- 账号别名是固定浏览器槽位，不等同于当前登录的小红书账号。
- 所有 Profile 共用通用扩展代码，但必须在各自扩展弹窗中单独配对账号槽位。
- 配对包默认 5 分钟过期且只能使用一次；不得在不同 Profile 之间复用。
- 换号前明确展示目标账号别名和当前 UID/昵称，并取得用户确认后才执行 `account-switch-begin --confirm`。
- 换号期间只执行认证或换号命令，不尝试绕过业务任务保护。

## 工作流程

### 第一步：检查登录状态

```bash
python scripts/cli.py check-login
```

输出解读：
- `"logged_in": true` → 已登录，可执行后续操作。
- `"logged_in": false` + `"login_method": "qrcode"` → 有界面环境，走方式 A（二维码）。输出自动包含 `qrcode_image_url` 和 `qrcode_path`。
- `"logged_in": false` + `"login_method": "both"` → 无界面服务器，输出自动包含二维码，**询问用户选方式 A（二维码）或方式 B（手机验证码）**。

### 第二步：根据输出选择登录方式

#### 方式 A：二维码登录（所有平台通用）

> `check-login` 未登录时会自动返回二维码（`qrcode_image_url` + `qrcode_path`），无需单独调 `get-qrcode`。

**第一步** — 从 `check-login` 返回的 JSON 取 `qrcode_image_url`，在回复中展示：

```
请使用小红书 App 扫描以下二维码登录：

![小红书登录二维码]({qrcode_image_url})

您也可以在手机浏览器中直接访问此链接完成登录：
{qr_login_url}
```

> **展示规范（必须全部遵守）**：
> 1. 展示二维码图片（`qrcode_image_url`）。
> 2. 如果输出含 `qr_login_url`，**必须**同时展示该链接并提示用户"也可以在手机浏览器中直接访问此链接完成登录"。
> 3. **禁止**省略 `qr_login_url`，即使已展示了二维码图片。

图片内嵌在对话窗口，用户可以扫码或直接访问链接登录。

**第二步** — 等待登录完成（**单次调用，无需轮询**）：

```bash
python scripts/cli.py wait-login
```

- 连接已有浏览器 tab，内部阻塞等待（最多 120 秒）。
- 输出 `{"logged_in": true}` 则完成；超时则提示用户重新运行 `get-qrcode` 刷新二维码。

> **二维码过期刷新**：如需单独刷新二维码（如超时后），可运行 `get-qrcode`，它仍作为独立命令保留。

#### 方式 B：手机验证码登录（无界面服务器，分两步）

**⚠️ 强制要求：必须先向用户确认手机号，即使上下文中已有手机号也不得跳过。**
- 用户可能要登录不同账号，手机号可能已变更。
- **禁止从历史对话、记忆或上下文中自动填入手机号。**
- **每次登录都必须明确向用户询问并得到确认后才能执行 `send-code`。**

**第一步** — 向用户确认手机号，然后发送验证码：

> **必须先问用户**："请提供您要登录的手机号（不含国家码，如 13800138000）"。
> 收到用户明确回复手机号后，才能执行以下命令。**不得跳过此步。**

```bash
python scripts/cli.py send-code --phone <用户确认的手机号>
```
- 自动填写手机号、勾选用户协议、点击"获取验证码"。
- 正常输出：`{"status": "code_sent", "message": "..."}`
- **频率限制**：自动切换为二维码登录，输出含 `qrcode_image_url`。告知用户"验证码发送受限，已切换为二维码登录"，按方式 A 的展示规范展示二维码，然后运行 `wait-login`。

**第二步** — 向用户询问验证码，然后提交登录：

> 告知用户验证码已发送，询问："请输入您收到的 6 位短信验证码"，获得回复后再执行以下命令。

```bash
python scripts/cli.py verify-code --code <用户提供的6位验证码>
```
- 自动填写验证码、点击登录。
- 输出：`{"logged_in": true, "message": "登录成功"}`

### 退出登录

> `delete-cookies` 会通过页面 UI 点击「更多」→「退出登录」，不删除 Chrome Profile、扩展或 Bridge 配置。

```bash
python scripts/cli.py delete-cookies
```

### 在现有账号槽位中安全换号

先读取当前身份并向用户展示账号别名、UID 和昵称：

```bash
python scripts/cli.py --account <账号别名> account-identity
```

用户确认退出当前账号后开始换号。可选地提供预期的新 UID：

```bash
python scripts/cli.py --account <账号别名> account-switch-begin \
  --confirm [--target-user-id <新UID>] [--label <用途备注>]
```

该命令取得同账号任务锁、写入换号状态并退出当前登录。此后业务命令被拒绝，认证命令仍可用。
按二维码或手机号流程登录新账号，然后完成核验：

```bash
python scripts/cli.py --account <账号别名> account-switch-complete \
  [--expected-user-id <新UID>] [--label <用途备注>]
```

只有新 UID 与预期一致且不同于原 UID 时才解除业务任务保护。需要放弃流程时执行：

```bash
python scripts/cli.py --account <账号别名> account-switch-cancel --confirm
```

若已登录另一 UID，普通取消会被拒绝；不要自行使用 `--force`，必须先向用户说明当前身份并取得确认。
如果旧账号以后还要快速使用，为新账号创建独立 Profile/账号槽位，不要覆盖旧槽位。

---

## 失败处理

- **验证码错误**：输出包含 `"logged_in": false`，重新运行 `verify-code --code <新验证码>`。
- **二维码超时**：重新执行 `get-qrcode` 获取新二维码，再运行 `wait-login`。
- **扩展未连接**：CLI 会自动打开 Chrome 并等待扩展连接，若超时提示用户检查 XHS Bridge 扩展是否已安装并启用。
- **换号未完成**：运行 `account-identity` 查看当前 UID，再执行 `account-switch-complete` 或经确认取消。
- **身份不一致**：停止互动和发布，展示记录 UID 与当前 UID；使用安全换号流程处理。
