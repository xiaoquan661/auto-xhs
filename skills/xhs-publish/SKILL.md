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

根据用户或经理任务的发布目标生成内容，连续完成填写、正式发布与平台结果回读。
图文使用 `publish`，视频使用 `publish-video`；`fill-publish` 和 `fill-publish-video`
同样会填写并正式发布。发布数量取任务的 `publish` 目标。

## 执行规则

- 使用本项目 `python scripts/cli.py --account <账号槽位> <子命令>`。
- 使用指定账号的人设、主题、知识与素材要求，复用槽位已绑定 UID。
- 生成最终标题、正文和素材后直接执行正式发布，自动完成表单检查。
- 遵守经理配置的发帖数量、间隔、发布时间和可见范围；定时任务传 `--schedule-at`。
- 图文必须有图片，视频必须有视频。标题、正文通过 UTF-8 文件传递，本地路径使用绝对路径。
- 按平台标题长度限制调整标题并保持语义；正文使用自然中文和清楚的段落。
- 图片 URL 直接交给 `--images` 下载；提取网页素材时优先正文图片的 `data-src`，跳过占位图。
- 同账号串行。发布后回读确认，再记录成功数量与 Outbox 回执。
- `RESULT_UNKNOWN` 先回读原发布记录；核对完成前保持任务状态，避免重复提交。
- 用户明确要求只保存草稿或暂停时，按该次任务要求处理。

## 图文与视频

```powershell
python scripts/cli.py --account <账号槽位> publish `
  --title-file "C:\Temp\xhs_title.txt" `
  --content-file "C:\Temp\xhs_content.txt" `
  --images "C:\Media\pic1.jpg"

python scripts/cli.py --account <账号槽位> publish-video `
  --title-file "C:\Temp\xhs_title.txt" `
  --content-file "C:\Temp\xhs_content.txt" `
  --video "C:\Media\video.mp4"
```

可选参数：`--tags`、`--schedule-at`、`--visibility`；图文还支持 `--original`。
命令返回任务编号、任务最终状态与平台回读结果。

## 长文

1. 将标题、长文正文写入 UTF-8 文件，用 `long-article` 创建并排版。
2. 根据内容从返回的模板列表选择合适模板，用 `select-template --task-id <任务ID> --name <模板名>` 应用。
3. 将发布描述写入文件，用 `next-step --task-id <任务ID> --content-file <绝对路径>`
   进入发布页、填写描述并正式发布。

## 已准备的任务

对于已填写且尚未提交的任务，复用原任务执行：

```powershell
python scripts/cli.py --account <账号槽位> click-publish --task-id <任务ID>
```

可传 `--preview-revision` 校验表单版本。提交前核对原任务状态，已提交或结果未知的任务先对账。
用户明确要求保存现有表单时使用 `save-draft --task-id <任务ID> --preview-revision <版本> --confirm`。

## 回执与验收

- 每篇保存 `note_id` 和可打开的 `note_url`，同时写入逐项回执与业务结果。优先保留平台完整作品链接；只有 ID 时构造作品链接。暂未取得链接时记录原因，后续通过同一执行的回执修订补齐，供控制台人工核对。

- `SUCCESS`：平台接口成功响应、发布成功提示或笔记管理页回读到本次笔记，计入正式发帖实际数。
- `RESULT_UNKNOWN`：已触发提交但缺少确定结果，回传任务编号和原始证据用于对账。
- `FAILED`：记录未完成原因与证据。
- 回执写入任务/轮次、账号、时间、`published`、平台回读结果、笔记 ID 或证据路径。
- 表单准备与正式发帖分别记录；实际数以正式发布成功证据为准。

正式提交通过扩展原生鼠标点击封闭 DOM 内的“发布”按钮。收到接口成功响应或“发布成功”页面提示后保存证据，继续收集带 ID 的响应；仍缺链接时从创作中心笔记管理按标题提取唯一作品链接。缺失或多项匹配保留发布成功状态并记录待补查原因。

## 已发布作品链接补查

发布结果 `link_status` 为 `resolved` 时保存 `note_id` 和 `note_url`；`pending` 或 `ambiguous` 时记录 `link_reason`。成功提示后会继续等待响应，并自动尝试创作中心补查一次（页面数据最多等待20秒）。在同一发布任务结束前，若自动补查仍未取得链接，继续使用以下命令并核对创作中心目标作品；取得链接后才将整条任务回报 SUCCESS。平台无法读取或作品有歧义时报告 PARTIAL_SUCCESS，保留已发布数量，禁止再次发布：

```powershell
python scripts/cli.py --account <槽位> publish-link-reconcile --task-id <员工本地发布任务ID> --worker-task-id <原经理任务ID>
```

省略 `--worker-task-id` 时仅更新本地发布记录；提供时同时修订原员工成功回执并写入 outbox，经理按既有回收流程导入。同一链接重复补查保持回执幂等。该命令只适用于已确认发布成功的原任务，补查读取创作中心，复用绑定 UID。仍未找到或有多个同名作品时保留待补查状态，由后续明确任务再次核对，不创建新发帖任务。
