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

- `SUCCESS`：平台接口成功响应、发布成功提示或笔记管理页回读到本次笔记，计入正式发帖实际数。
- `RESULT_UNKNOWN`：已触发提交但缺少确定结果，回传任务编号和原始证据用于对账。
- `FAILED`：记录未完成原因与证据。
- 回执写入任务/轮次、账号、时间、`published`、平台回读结果、笔记 ID 或证据路径。
- 表单准备与正式发帖分别记录；实际数以正式发布成功证据为准。
