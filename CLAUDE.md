# xiaohongshu-skills

小红书自动化 Claude Code Skills，使用用户的真实浏览器和账号信息操作小红书。

## Git 工作流

- 所有代码修改必须在分支上进行，禁止直接推送 main 分支
- 分支开发完成后通过 PR 合入 main

## 开发命令

```bash
uv sync                    # 安装依赖
uv run ruff check .        # Lint 检查
uv run ruff format .       # 代码格式化
uv run pytest              # 运行测试
```

## 架构

项目由本地应用服务、Python 自动化引擎、Bridge/扩展、WebUI 和 Skills 组成。

- `scripts/xhs/` — 核心自动化库（模块化，每个功能一个文件）
- `scripts/application_service.py` — CLI、WebUI 和 Agent 共用的任务、确认与身份服务
- `scripts/cli.py` — 统一 CLI 入口，按账号槽位输出 JSON 结构化结果
- `scripts/bridge_server.py` — 本地通信服务（连接 CLI 与浏览器扩展）
- `extension/` — Chrome 扩展，在用户的真实浏览器中执行操作
- `webui/` — 仅监听本机的账号、任务、确认、记录与诊断工作台
- `skills/*/SKILL.md` — 指导 Claude 如何调用 scripts/

### 调用方式

```bash
python scripts/cli.py --account brand-a check-login
python scripts/cli.py --account brand-a search-feeds --keyword "关键词"
python scripts/cli.py --account brand-a fill-publish --title-file t.txt --content-file c.txt --images pic.jpg
```

> 只有 `account-start` 会在扩展未连接时按需打开槽位绑定的 Chrome Profile。普通业务命令不会
> 任意启动、切换或关闭 Chrome。发布必须使用分步填写、真实预览、确认和结果回读；旧版一步发布禁用。

## 代码规范

- 行长度上限 100 字符
- 完整 type hints，使用 `from __future__ import annotations`
- 异常继承 `XHSError`（`xhs/errors.py`）
- CLI exit code：0=成功，1=未登录，2=错误
- 用户可见错误信息使用中文
- JSON 输出 `ensure_ascii=False`

### 安全约束

- 发布类操作必须有用户确认机制
- 文件路径必须使用绝对路径
- 敏感内容通过文件传递，不内联到命令行参数

## CLI 入口

完整命令以 `python scripts/cli.py --help` 为准，产品边界以 `docs/PRD-TRACEABILITY.md` 和
`SKILL.md` 为准。常用分组：

| 分类 | 主要命令 |
|--|--|
| 账号与连接 | `account-onboard`、`account-start`、`account-doctor`、`account-pair-*` |
| 身份与换号 | `check-login`、`account-identity`、`account-switch-*` |
| 浏览与互动 | `list-feeds`、`search-feeds`、`get-feed-detail`、`like-feed`、`favorite-feed` |
| 评论与回复 | `post-comment`、`random-comment`、`reply-comment`、`collect-note-comments`、`generate-reply-draft` |
| 分步发布 | `fill-publish`、`fill-publish-video`、`long-article`、`select-template`、`next-step`、`click-publish`、`save-draft` |
| 私信与主加 | `private-message-context`、`prepare-private-messages`、`send-private-messages`、`follow-user` |
| 数据与诊断 | `collect-operations-metrics`、`diagnose-404`、`check-risk`、`get-netlog`、`risk-report` |

不要使用外部小红书 MCP 或其他自动化项目替代本项目执行链。
