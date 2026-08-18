---
name: xhs-auth
description: |
  小红书登录状态、退出、配对、身份核验和半自动换号技能。当用户要求检查登录、
  退出当前账号、在固定槽位中手动登录新账号、核验 UID、处理身份不一致或管理配对时使用。
---

# 小红书认证与半自动换号

只通过本项目的 `python scripts/cli.py` 管理登录身份。

## 版本边界

- V1.0 当前代码保留部分二维码和手机验证码命令，但不再把它们作为产品主流程。
- V1.5 产品入口只保留：检查状态、自动退出、用户手动登录、核验新 UID。
- V1.5 联合启动目标允许扩展未连接时自动打开绑定 Profile；代码完成前仍按真实状态提示用户
  手动打开，不得声称已经自动启动。

## 允许命令

| 子命令 | 用途 |
|---|---|
| `check-login` | 检查当前登录状态 |
| `delete-cookies` | 通过页面退出当前登录并回读核验 |
| `account-identity [--record]` | 读取、比较或记录当前 UID |
| `account-switch-begin --confirm` | 暂停业务并退出当前登录 |
| `account-switch-complete` | 核验新 UID 并恢复业务 |
| `account-switch-cancel --confirm` | 取消未完成的换号流程 |
| `account-switch-history` | 查看本机换号记录 |
| `account-pair-begin --confirm` | 生成通用扩展一次性配对包 |
| `account-pair-status` | 查看扩展配对状态 |
| `account-unpair --confirm` | 撤销扩展实例并轮换连接令牌 |
| `account-start` | 启动账号；V1.0 仅启动 Bridge，V1.5 目标为 Bridge 与绑定 Profile 联合启动 |

不要从本技能引导 `get-qrcode`、`wait-login`、`send-code` 或 `verify-code`。这些历史兼容命令是否
保留，由后续代码检查阶段决定。

## 强制约束

- 多账号命令把 `--account <账号别名>` 放在子命令之前。
- 账号别名是固定浏览器槽位，不等于当前登录的小红书账号。
- 换号前展示槽位、当前 UID 和昵称，并在用户确认后执行退出。
- 所有 Profile 共用当前工作区的通用扩展，但必须分别配对槽位。
- 配对包短时有效且只能使用一次，不跨 Profile 复用。
- 换号期间不执行搜索、互动、发布、私信或资料修改。
- 不从历史对话、记忆或账号名称推测手机号、验证码或登录身份。

## 检查登录状态

```powershell
python scripts/cli.py --account <账号别名> check-login
```

- `logged_in: true`：展示当前昵称和 UID，并与槽位记录核对。
- `logged_in: false`：提示用户在绑定 Profile 中手动登录。
- UID 不一致：停止业务操作，进入身份核验或半自动换号流程。

## 单独退出当前账号

先向用户展示目标槽位和当前身份，再执行：

```powershell
python scripts/cli.py --account <账号别名> delete-cookies
```

只有页面回读到明确未登录状态时才能报告退出成功。该操作不删除 Chrome Profile、扩展、Bridge
或槽位配置。

## 半自动换号

### 1. 读取当前身份

```powershell
python scripts/cli.py --account <账号别名> account-identity
```

### 2. 用户确认后退出并暂停业务

```powershell
python scripts/cli.py --account <账号别名> account-switch-begin `
  --confirm `
  [--target-user-id <新UID>] `
  [--label <用途备注>]
```

### 3. 用户手动登录

用户在该槽位绑定的 Chrome Profile 中自行完成登录。系统不填写手机号、验证码或密码。

### 4. 核验新 UID 并恢复业务

```powershell
python scripts/cli.py --account <账号别名> account-switch-complete `
  [--expected-user-id <新UID>] `
  [--label <用途备注>]
```

只有当前 UID 与预期一致，并且换号状态有效时，才能更新槽位身份和恢复业务。放弃流程时执行：

```powershell
python scripts/cli.py --account <账号别名> account-switch-cancel --confirm
```

如果 Profile 已经登录另一 UID，普通取消被拒绝时，不自行添加强制参数；先向用户说明真实身份。

## V1.5 联合启动目标

```text
启动或复用 Bridge
→ 扩展未连接时自动打开槽位绑定 Profile
→ 等待扩展连接
→ 核对 Profile 声明和扩展实例
→ 检查登录 UID
```

- 正确 Profile 已连接时不重复打开。
- 错误 Profile 返回 `PROFILE_MISMATCH`。
- 只自动打开，不自动关闭 Chrome。
- 联合启动代码未完成前，按 V1.0 真实能力要求用户手动打开 Profile。

## 失败处理

- **退出后仍能读取旧 UID**：报告退出失败，不进入手动登录步骤。
- **扩展未连接**：运行账号联合启动流程，按需打开绑定 Profile，再检查扩展配对。
- **Profile 不一致**：停止换号，展示期望与实际 Profile。
- **换号未完成**：运行 `account-identity`，再完成或经确认取消换号。
- **新 UID 与预期不一致**：保持业务暂停，不覆盖原身份记录。
