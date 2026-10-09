# GitHub 双人协作开发手册

本文适用于 `xiaoquan661/auto-xhs`。仓库所有者为 `xiaoquan661`，当前协作者 `tbry7`
已接受邀请，双方都可以读取仓库并推送任务分支。默认分支为 `main`。

代码协作采用一项任务对应一个 Issue、一个任务分支和一个 Pull Request（PR）的流程。
项目的 [AGENTS.md](../AGENTS.md) 规定：代码修改在分支上完成，通过 PR 合入 `main`。

```text
Issue 分配 → 各自创建任务分支 → 本机开发和测试 → 推送分支
          → PR 审核 → 合入 main → 各自同步 → 按需部署到经理机和员工机
```

## 1. 两个人分别负责什么

| 阶段 | 任务负责人 | 审核人 |
| --- | --- | --- |
| 任务开始 | 领取 Issue，确认范围和验收标准 | 描述目标、边界和优先级 |
| 开发 | 从最新 `main` 建分支，修改代码和测试 | 在另一个分支处理自己的任务 |
| 提交 | 推送分支，发起指向 `main` 的 PR | 检查改动、测试、部署说明并提出意见 |
| 合并 | 根据审核意见继续提交到同一分支 | 确认后合并 PR |
| 运行 | 提供安装步骤和验证结果 | 安排经理机、员工机的安装与实机验收 |

角色由任务决定。`tbry7` 的任务可以由 `xiaoquan661` 审核，反过来也一样。两个人各自在自己的
电脑或独立工作区开发，不共用一个正在修改的 Git 工作目录。

## 2. 同事第一次在电脑上准备项目

### 2.1 登录 GitHub 并克隆

同事在自己的电脑登录接受邀请的 GitHub 账号。在 PowerShell 中执行：

```powershell
git clone https://github.com/xiaoquan661/auto-xhs.git `
  "$env:USERPROFILE\Documents\auto-xhs"
Set-Location "$env:USERPROFILE\Documents\auto-xhs"
git status --short --branch
```

Git 提示登录时使用接受邀请的账号。克隆完成后，应看到当前分支为 `main`。

如本机尚未配置提交者信息，在项目目录设置：

```powershell
git config user.name "tbry7"
git config user.email "GitHub 账户中已验证的邮箱"
```

然后阅读 [AGENTS.md](../AGENTS.md)、[项目记忆库](../project-memory/README.md)和
[需求追踪矩阵](PRD-TRACEABILITY.md)。开发环境按项目命令准备：

```powershell
uv sync
uv run python scripts/cli.py --help
```

`uv` 尚未安装时，可按[第二套系统部署指南](SECOND-SYSTEM-DEPLOYMENT.md)中的 Windows 环境
准备步骤安装项目依赖。

## 3. 用 Issue 分配任务

在仓库 **Issues → New issue** 创建任务，指定负责人。任务描述建议包含：

```text
标题：运营台账号卡片显示最近采集时间

目标：用户进入账号详情时能看到最近一次采集的时间。
范围：经理机数据读取与运营台展示。
验收：有数据、无数据、采集失败三种状态清晰；相关测试通过。
交付：代码 PR、测试结果、需要安装的机器与验证步骤。
```

一个 Issue 由一人负责，一个分支处理该 Issue。若需要共同修改同一模块，先在 Issue 中分清接口、
文件和合并顺序，避免两个人同时改同一段代码。

两个人都使用 Codex 开发时，可以在各自电脑的项目任务中使用同一份交接格式：

```text
请先阅读本仓库 AGENTS.md、project-memory/README.md 和当前状态。
处理 Issue #42：运营台账号卡片显示最近采集时间。
从最新 main 创建 codex/42-account-collection-time 分支。
完成实现与相关测试，说明验证范围；提交并推送分支，发起指向 main 的 PR。
涉及经理机或员工机安装时，在 PR 中列出目标设备与验收步骤。
```

Issue 的目标和范围以实际任务替换。Codex 对话属于各自的电脑和账号，代码、测试与审核结论通过
GitHub 分支、Issue 和 PR 交接。

## 4. 负责人创建任务分支

开始新任务前同步 `main`，再创建分支。以下示例假设 Issue 编号是 `42`：

```powershell
git switch main
git pull --ff-only origin main
git switch -c codex/42-account-collection-time
git status --short --branch
```

分支名采用 `codex/<Issue编号>-<简短主题>`，例如
`codex/42-account-collection-time`。每项任务从当前 `origin/main` 开始，不从另一项任务的旧分支
继续开发。

已有未提交改动的工作目录应继续保存原任务。需要同时启动新任务时，在原仓库新建独立工作区：

```powershell
git fetch origin
git worktree add `
  "$env:USERPROFILE\Documents\auto-xhs-issue-42" `
  -b codex/42-account-collection-time origin/main
```

在新目录中处理 Issue 42，原目录的文件保持原任务状态。
这组 `git worktree add` 命令是上方 `git switch -c` 的替代方式，同一个任务只需选择一种。

## 5. 开发、检查和提交

修改完成后先看清改动范围：

```powershell
git status --short
git diff --check
git diff --stat
```

Python 代码使用项目检查命令：

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

修改 WebUI 或扩展时，运行相关的 Node 测试文件。全量 Node 测试可在 PowerShell 中运行：

```powershell
Get-ChildItem .\tests -Filter *.cjs | ForEach-Object {
  node --test $_.FullName
  if ($LASTEXITCODE -ne 0) { throw "Node 测试失败：$($_.Name)" }
}
```

根据任务范围记录自动测试、经理机本地验证和员工机实机验证各自的结果。修改扩展可执行文件时，
同步递增 `extension/manifest.json` 版本；安装后在目标 Chrome Profile 中重载，并核对 Bridge
回报的运行版本。业务写入动作遵循 [AGENTS.md](../AGENTS.md) 的账号、回执与结果核对规则。

只暂存本任务的文件，提交并推送任务分支：

```powershell
git add .\webui\manager-console.js .\tests\test_manager_front_behavior.cjs
git commit -m "feat: 展示账号最近采集时间"
git push -u origin codex/42-account-collection-time
```

上面的文件名和提交说明是示例，实际提交时换成本任务改动。每次提交前用 `git status --short`
核对暂存内容。账号凭据、Cookie、Chrome Profile、经理数据库、素材和员工机收发目录保留在各机器
原有存储中。

## 6. 发起 PR 并请另一人审核

首次推送后，在 GitHub 仓库页面点击 **Compare & pull request**，核对：

- **base**：`main`；
- **compare**：本次任务分支；
- **Reviewers**：另一位协作者；
- **Assignees**：任务负责人。

PR 描述可直接使用：

```text
关联 Issue：#42

改动内容：
- 账号卡片显示最近采集时间。
- 补充有数据、空数据和失败状态的测试。

验证：
- Ruff：通过。
- Pytest：通过。
- Node 相关测试：通过。
- 实机：待安装后验收。

部署：
- 经理机：需要更新 WebUI。
- 员工机：本次不涉及。
```

开发还在进行时可以先建 **Draft PR** 讨论；准备审核后改为 **Ready for review**。审核人查看
**Files changed** 和测试说明，可以逐行评论、要求修改或批准。负责人继续向同一分支推送提交，
PR 会自动更新。[GitHub PR 使用说明](https://docs.github.com/en/pull-requests/get-started/about-pull-requests)

本仓库现有 `.github/workflows/release.yml` 在 `main` 更新时制作 Release，PR 阶段的 Ruff、
Pytest 和 Node 结果由提交人在 PR 中记录。未来增加 PR 自动检查后，可把检查结果纳入合并条件。

## 7. 同步 `main` 与处理冲突

审核期间若 `main` 已有其他人的新提交，负责人在自己的任务分支执行：

```powershell
git fetch origin
git switch codex/42-account-collection-time
git merge origin/main
```

Git 提示冲突时，按文件核对两边改动，编辑冲突文件，再运行：

```powershell
git status
git add 已解决的文件路径
git commit
uv run ruff check .
uv run pytest
git push
```

`git add 已解决的文件路径` 表示逐个添加已经核对的文件。若 Git 已自动完成合并提交，直接运行
检查并推送。遇到同一业务规则的不同实现，由任务负责人和审核人在 PR 中确定最终口径。

## 8. 合并后的日常操作

审核通过后，在 PR 页面合并到 `main`。两个人开始下一项任务前分别更新自己的本地仓库：

```powershell
git switch main
git pull --ff-only origin main
git fetch origin --prune
```

已合并且不再被工作区使用的本地任务分支可执行 `git branch -d 分支名`。GitHub 仓库可在
**Settings → General → Pull Requests** 启用 **Automatically delete head branches**，让合并后的
远程任务分支自动整理。[GitHub 分支整理说明](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-the-automatic-deletion-of-branches)

合并到 GitHub `main` 后，由负责运行环境的人安排代码安装。经理机、员工机、Chrome 扩展、
Bridge 和账号槽位各自有运行版本；实际完成以目标机器的服务健康、扩展版本和业务回执为准。
新机器部署见[第二套系统部署指南](SECOND-SYSTEM-DEPLOYMENT.md)。

## 9. 仓库设置建议

截至 2026-10-09，本仓库为私有仓库，`main` 尚未启用分支保护，合并后自动删除任务分支的
设置尚未开启。所有者可以按当前 GitHub 套餐提供的功能设置：

1. **Settings → Branches**：为 `main` 设置通过 PR 合并和至少一位审核人批准。
2. **Settings → General → Pull Requests**：启用合并后自动删除任务分支。
3. 若以后新增 PR 自动测试，再将对应检查设为合并条件。

GitHub 对私有仓库的分支保护功能有套餐要求，以设置页面实际可用选项为准。
[GitHub 分支保护说明](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches)

## 10. 第一轮协作验收

建议先选一个范围清楚的小任务，完整走一次流程：

1. 仓库所有者创建 Issue，分配给 `tbry7`。
2. `tbry7` 从最新 `main` 建任务分支，修改并推送。
3. `tbry7` 提交 PR，写明测试结果，请 `xiaoquan661` 审核。
4. `xiaoquan661` 留下审核意见；负责人更新同一 PR。
5. 审核通过后合并，双方拉取新 `main`。
6. 若任务涉及经理机或员工机，再进行对应设备的安装与验收。

完成这轮后，后续任务重复同一套步骤。GitHub 负责源码、讨论和审核；运营数据、账号环境与
任务执行继续由经理机和员工机各自维护。
