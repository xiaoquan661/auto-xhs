# xiaohongshu-skills

小红书自动化项目，使用用户的真实浏览器和账号信息操作小红书。

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

只有 `account-start` 会在扩展未连接时按需打开槽位绑定的 Chrome Profile。普通业务命令保持当前浏览器和账号环境。发布采用分步填写、真实预览、用户确认和结果回读流程。

## 代码规范

- 行长度上限 100 字符。
- 使用完整 type hints 和 `from __future__ import annotations`。
- 异常继承 `XHSError`（`xhs/errors.py`）。
- CLI exit code：0=成功，1=未登录，2=错误。
- 用户可见错误信息使用中文。
- JSON 输出使用 `ensure_ascii=False`。

### 操作约束

- 发布类操作必须经过用户确认。
- 文件路径使用绝对路径。
- 敏感内容通过文件传递，不内联到命令行参数。
- 小红书操作统一使用本项目执行链。

## CLI 入口

完整命令以 `python scripts/cli.py --help` 为准，产品边界以 `docs/PRD-TRACEABILITY.md` 和 `SKILL.md` 为准。

| 分类 | 主要命令 |
| --- | --- |
| 账号与连接 | `account-onboard`、`account-start`、`account-doctor`、`account-pair-*` |
| 身份与换号 | `check-login`、`account-identity`、`account-switch-*` |
| 浏览与互动 | `list-feeds`、`search-feeds`、`get-feed-detail`、`like-feed`、`favorite-feed` |
| 评论与回复 | `post-comment`、`random-comment`、`reply-comment`、`collect-note-comments`、`generate-reply-draft` |
| 分步发布 | `fill-publish`、`fill-publish-video`、`long-article`、`select-template`、`next-step`、`click-publish`、`save-draft` |
| 私信与主加 | `private-message-context`、`collect-private-messages`、`prepare-private-messages`、`send-private-messages`、`follow-user` |
| 数据与诊断 | `collect-operations-metrics`、`diagnose-404`、`check-risk`、`get-netlog`、`risk-report` |

## 功能包打包规范

- 正式包放在项目外的 `../功能包/<业务模块>/<功能编号>/<版本>/`，保留各版本，并同步更新 `../功能包/版本清单.json`。
- 每包包含：`manifest.json`、`README.md`、`runtime/`、`config/`、`tests/`、`CHANGELOG.md`、`ROLLBACK.md`、`RUN_RESULT.json`、`VERIFICATION_REPORT.md`。
- `manifest.json` 写明并保持一致：`function_id`、`task_code`、`function_name`、`package_name`、`applicable_layer`、`execution_mode`、依赖、调用关系、运行入口和证据文件。
- 功能包使用项目真实功能编号和实现；运行入口可执行，测试覆盖固定测试和历史回放。
- 实机证据记录设备、系统、Python、执行动作、结果和结论；自动测试、实机验证、上传、开放及正式安装分别登记状态。
- 一个 ZIP 对应一个功能的一个版本；提交前从 ZIP 内复核功能编号、审核字段、必需文件和运行证据。
