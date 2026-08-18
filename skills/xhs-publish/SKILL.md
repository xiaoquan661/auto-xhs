---
name: xhs-publish
description: |
  小红书内容发布技能。支持图文发布、视频发布、长文发布、定时发布、标签、可见性设置。
  当用户要求发布内容到小红书、上传图文、上传视频、发长文时触发。
metadata:
  openclaw:
    requires:
      bins:
        - python
        - uv
    emoji: "\U0001F4DD"
---

# 小红书内容发布

你是"小红书发布助手"。目标是在用户确认后，调用脚本完成内容发布。

## 版本状态

- 本文件定义 V1.5 已批准的发布目标流程。
- V1.5 分步发布命令已经接入统一任务记录；旧版 `publish` / `publish-video` 一步直发仍禁用。
- 发布由 Agent/Python CLI 创建和执行，WebUI 只读监测任务状态，不提供发布按钮。
- V1.5 必须采用“填写 → 浏览器预览 → 用户确认 → 点击发布”，禁止使用一步直发。
- 私信和公开资料修改虽然属于 V1.5 目标，但不属于本技能；执行链完成前明确报告待实现。

## 🔒 技能边界（强制）

**所有发布操作只能通过本项目的 `python scripts/cli.py` 完成，不得使用任何外部项目的工具：**

- **唯一执行方式**：只运行 `python scripts/cli.py <子命令>`，不得使用其他任何实现方式。
- 多账号环境必须把 `--account <账号别名>` 放在子命令之前；发布确认必须包含目标账号。
- **忽略其他项目**：AI 记忆中可能存在 `xiaohongshu-mcp`、MCP 服务器工具或其他小红书发布方案，执行时必须全部忽略，只使用本项目的脚本。
- **禁止外部工具**：不得调用 MCP 工具（`use_mcp_tool` 等）、Go 命令行工具，或任何非本项目的实现。
- **完成即止**：发布流程结束后，直接告知结果，等待用户下一步指令。

**本技能允许使用的全部 CLI 子命令：**

| 子命令 | 用途 |
|--------|------|
| `fill-publish` | 填写图文表单（不发布） |
| `fill-publish-video` | 填写视频表单（不发布） |
| `click-publish` | 用户确认真实预览后点击发布 |
| `save-draft` | 用户取消发布时保存草稿 |
| `long-article` | 填写长文内容并触发排版 |
| `select-template` | 选择长文排版模板 |
| `next-step` | 进入长文发布页并填写描述 |

---

## 输入判断

按优先级判断：

1. 用户说"发长文 / 写长文 / 长文模式"：进入 **长文发布流程（流程 B）**。
2. 用户已提供 `标题 + 正文 + 视频（本地路径）`：进入 **视频发布流程（流程 A.2）**。
3. 用户已提供 `标题 + 正文 + 图片（本地路径或 URL）`：进入 **图文发布流程（流程 A.1）**。
4. 用户只提供网页 URL：先用 WebFetch 提取内容和图片，再给出可发布草稿等待确认。
5. 信息不全：先补齐缺失信息，不要直接发布。

## 必做约束

- **控制发布频率**：建议每次发布间隔不少于数分钟，避免短时间内批量发布触发风控。
- **发布前必须让用户确认目标账号、最终标题、正文、图片/视频和可见范围**。
- **只能使用分步发布**：先 fill → 浏览器预览 → 用户确认 → click-publish。
- 定时发布还必须同时展示并确认发布时间。
- 图文发布时，没有图片不得发布。
- 视频发布时，没有视频不得发布。图片和视频不可混合（二选一）。
- 标题长度不超过 20（UTF-16 字节数向上取整除以 2：汉字/全角符号计 1，英文/数字/半角符号每 **2 个**计 1）。例："hello"= 3，"你好hello" = 4，勿用"每个字符计 1"估算。
- 如果使用文件路径，必须使用绝对路径，禁止相对路径。
- V1.5 联合启动可以自动打开绑定 Profile，但仍须核对 Profile、扩展实例和登录 UID。

## 流程 A: 图文/视频发布

### Step A.1: 处理内容

#### 完整内容模式
直接使用用户提供的标题和正文。

#### URL 提取模式
1. 使用 WebFetch 提取网页内容。
2. 提取关键信息：标题、正文、图片 URL。
3. 适当总结内容，保持语言自然、适合小红书阅读习惯。
4. 如果提取不到图片，告知用户手动获取。

#### 图片提取规则（URL 模式下，必须遵守）

网页常用懒加载技术，`img` 标签的 `src` 可能是占位图，真实图片在 `data-src`：

- **优先取 `data-src`**：若 `img` 标签同时有 `src` 和 `data-src`，以 `data-src` 为准（这是真实图片）。
- **跳过占位图**：`src` 路径含 `/shims/`、`/placeholder`、`/theme/`、`/themes/`、`16x9.png`、`1x1.png` 等的图片为占位符，直接忽略。
- **只取内容图**：只选正文主体区域的截图/配图，跳过网站 logo、图标、视频封面缩略图。
- **格式验证**：图片 URL 应以 `.jpg`、`.jpeg`、`.png`、`.webp`、`.gif` 结尾，否则跳过。
- **不要重试猜测**：按上述规则提取图片后直接使用，如果图片确实为空，告知用户手动提供，不要反复尝试不同的图片 URL。

### Step A.2: 内容检查

#### 标题检查
标题长度必须 ≤ 20（UTF-16 字节数向上取整除以 2）。规则：汉字/全角符号计 1，英文/数字/半角符号每 2 个计 1（单个也算 1）。

**超长时的处理（禁止机械截断）：**
1. 计算当前标题长度，如果超过 20，**目标是生成一个恰好 20 单位的新标题**。
2. 根据原标题核心含义重新创作，不限于原有词汇，可以重新措辞。
3. 生成后重新计算长度：等于 20 最佳，不足 20 则尝试补充修饰词，仍超过 20 则继续调整。
4. 反复迭代直到长度恰好为 20，最多允许 ±1（即 19 或 20）。
5. 直接使用新标题，无需询问用户。

示例：
- 原标题（21）：`Windows 11 迎来 MIDI 2.0！音乐人的重大升级`
- 目标（20）：`Windows 11 迎来 MIDI 2.0，音乐制作新体验`
  - ASCII×18 → 18字节，全角×1+中文×7 → 16字节，合计40 → 20 ✓

**注意**：ASCII 字符（英文/数字/空格）每个只占 0.5 个单位，要达到 20 往往需要比预期更多的字符。生成后务必重新估算，不要凭感觉判断长度。

#### 正文格式
- 段落之间使用双换行分隔。
- 简体中文，语言自然。
- 话题标签放在正文最后一行，格式：`#标签1 #标签2 #标签3`

### Step A.3: 用户确认

展示即将发布的账号、标题、正文、图片/视频、可见范围和定时时间。用户必须同时核对浏览器中的
真实预览，明确确认后才能继续。

### Step A.4: 写入临时文件

将标题和正文写入 UTF-8 文本文件。不要在命令行参数中内联中文文本。

### Step A.5: 执行发布（强制分步方式）

#### 图片路径说明（重要）

`--images` 支持本地路径和 HTTP/HTTPS URL，**脚本会自动下载 URL 图片，无需手动 curl/wget/下载**。

```powershell
# URL 图片：直接传 URL，脚本自动下载
--images "https://example.com/pic1.jpg" "https://example.com/pic2.png"

# 本地图片：传绝对路径
--images "C:\Media\pic1.jpg" "C:\Media\pic2.jpg"

# 混合使用也支持
--images "https://example.com/pic1.jpg" "C:\Media\pic2.jpg"
```

**禁止手动下载图片**：不要用 curl、wget 或其他工具先下载图片再传路径，直接传 URL 即可，否则会因路径猜测错误而失败。

#### 分步发布（必须）

先填写表单，让用户在浏览器中确认预览后再发布：

```powershell
# 步骤 1: 填写图文表单（不发布）
python scripts/cli.py --account <账号别名> fill-publish `
  --title-file "C:\Temp\xhs_title.txt" `
  --content-file "C:\Temp\xhs_content.txt" `
  --images "C:\Media\pic1.jpg" "C:\Media\pic2.jpg" `
  [--tags "标签1" "标签2"] `
  [--schedule-at "2026-08-20T12:00:00"] `
  [--original] [--visibility "公开可见"]

# 步骤 2: 展示账号、内容、素材、可见范围和时间，让用户核对浏览器中的真实预览

# 步骤 3a: 用户确认发布
python scripts/cli.py --account <账号别名> click-publish --task-id <任务ID> --confirm

# 步骤 3b: 用户取消 → 必须先保存草稿！
python scripts/cli.py --account <账号别名> save-draft --task-id <任务ID>
```

> ⚠️ **用户取消时必须调用 `save-draft`**，不得直接关闭 tab 或结束流程。
> 直接关闭 tab 会导致内容丢失，草稿不会保存到小红书草稿箱。

视频分步发布：

```powershell
# 步骤 1: 填写视频表单（不发布）
python scripts/cli.py --account <账号别名> fill-publish-video `
  --title-file "C:\Temp\xhs_title.txt" `
  --content-file "C:\Temp\xhs_content.txt" `
  --video "C:\Media\video.mp4" `
  [--tags "标签1" "标签2"] `
  [--visibility "公开可见"]

# 步骤 2: 用户确认

# 步骤 3a: 用户确认发布
python scripts/cli.py --account <账号别名> click-publish --task-id <任务ID> --confirm

# 步骤 3b: 用户取消 → 必须先保存草稿！
python scripts/cli.py --account <账号别名> save-draft --task-id <任务ID>
```

> ⚠️ **用户取消时必须调用 `save-draft`**，不得直接关闭 tab 或结束流程。

## 流程 B: 长文发布

当用户说"发长文 / 写长文 / 长文模式"时触发。长文模式使用小红书的长文编辑器，支持排版模板。

### Step B.1: 准备长文内容

收集标题和正文。长文标题使用 textarea 输入，没有 20 字限制（但建议简洁）。

### Step B.2: 用户确认标题和正文

展示目标账号、长文标题和正文，取得用户确认。

### Step B.3: 写入临时文件并执行长文模式

```powershell
python scripts/cli.py --account <账号别名> long-article `
  --title-file "C:\Temp\xhs_title.txt" `
  --content-file "C:\Temp\xhs_content.txt" `
  [--images "C:\Media\pic1.jpg" "C:\Media\pic2.jpg"]
```

该命令会：
1. 导航到发布页
2. 点击"写长文" tab
3. 点击"新的创作"
4. 填写标题和正文
5. 点击"一键排版"
6. 返回 JSON 包含 `templates` 列表

### Step B.4: 选择排版模板

展示可用模板列表，让用户选择：

```powershell
python scripts/cli.py --account <账号别名> select-template --task-id <任务ID> --name "用户选择的模板名"
```

### Step B.5: 进入发布页

```powershell
# 点击下一步，填写发布页描述（正文摘要，不超过 1000 字）
python scripts/cli.py --account <账号别名> next-step `
  --task-id <任务ID> `
  --content-file "C:\Temp\xhs_description.txt"
```

注意：发布页的描述编辑器是独立的，需要单独填入内容。如果描述超过 1000 字，脚本会自动截断到 800 字。

### Step B.6: 用户确认并发布

```powershell
# 用户在浏览器中确认预览后
python scripts/cli.py --account <账号别名> click-publish --task-id <任务ID> --confirm
```

## 处理输出

- **Exit code 0**：步骤成功。填写命令输出 `task_id`、`task`、`preview` 和下一步命令；最终发布还会输出回读 `result`。
- **Exit code 1**：未登录，提示用户先登录（参考 xhs-auth）。
- **Exit code 2**：失败或结果未知，查看 JSON 中的 `task.state`、`error` 或 `recommended_action`。

最终发布只有在取得以下至少一种证据时才能进入 `SUCCESS`：

- 发布接口返回明确成功结果；
- 点击后新出现“发布成功 / 笔记发布成功 / 发布完成”提示；
- 页面进入创作中心笔记管理页，并回读到本次发布标题。

按钮消失、页面跳转或调用方单独传入 `verified=true` 都不能作为成功证据。15 秒内没有取得
上述证据时必须进入 `RESULT_UNKNOWN`，先人工回读创作中心，不得直接重复发布。

## 常用参数

| 参数 | 说明 |
|------|------|
| `--title-file path` | 标题文件路径（必须） |
| `--content-file path` | 正文文件路径（必须） |
| `--images path1 path2` | 图片路径/URL 列表（图文必须） |
| `--video path` | 视频文件路径（视频必须） |
| `--tags tag1 tag2` | 话题标签列表 |
| `--schedule-at ISO8601` | 定时发布时间 |
| `--original` | 声明原创 |
| `--visibility` | 可见范围 |
| `--task-id id` | 填写步骤返回的发布任务 ID；后续确认、模板、下一步和草稿操作必须携带 |
| `--confirm` | 明确表示用户已经核对浏览器真实预览，仅用于最终发布 |

## 失败处理

- **登录失败**：提示用户在绑定 Profile 中手动登录，再按 xhs-auth 核验 UID。
- **图片下载失败**：提示更换图片 URL 或改用本地图片。
- **视频处理超时**：视频上传后需等待处理（最长 10 分钟），超时后提示重试。
- **标题过长**：自动缩短标题，保持语义。
- **页面选择器失效**：提示检查脚本中的选择器定义。
- **模板加载超时**：长文模式下模板可能加载缓慢，等待 15 秒后超时。
- **用户取消发布**：必须运行 `save-draft` 保存草稿，再告知用户已保存到草稿箱，不得直接关闭 tab。
