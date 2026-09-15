# 员工 Agent 视觉浏览器

扩展 1.4.0 提供截图、视觉操作和调用提示。Python CLI 接收 PNG 并保存到任务目录，员工
Agent 通过图片工具识别内容，继续在既有任务授权范围内执行。

`browser-snapshot --output-file <PNG绝对路径>` 返回原始 PNG、标签页、视口尺寸、像素尺寸、
页面地址、文档标识和采集时间。`image_path` 供 `view_image` 打开，`snapshot_file` 留作
后续操作依据。Bridge 支持 50 MiB 消息，承载较大截图。

`browser-action --snapshot-file <JSON绝对路径> --input-file <JSON绝对路径>
--output-file <操作后PNG绝对路径>` 支持 click、scroll、type、key。点击坐标采用原图像素，
执行层转换为 CSS 视口坐标；页面切换、滚动或视口变化后重新截图。操作派发与结果确认分开，
返回 OBSERVATION_REQUIRED 时 Agent 查看操作后截图，确认后继续业务。派发后截图失败保留
RESULT_UNKNOWN，先观察结果。业务提交使用对应业务 CLI 的授权、配额和确认链。

扩展图标角标和页面浮层呈现当前读取、截图、操作、等待及终态。浮层不接收鼠标事件，
完成后自动消失，调用异常呈现异常状态。调用提示只查询已有标签页。

任务入口保存 step-progress.json 和失败步骤 recovery 建议。普通页面异常保持当前步骤，
身份异常处理登录与绑定，发送结果未知核对原动作；检查新增消息由任务或巡检计划触发。

使用前安装对应 Python/扩展文件并重新加载扩展，重启账号 Bridge。扩展配对和账号配置保持
原值。已运行的 Agent 保持原提示词，后续启动的任务采用视觉流程。

模型输入能力参考 [OpenAI Docs：Codex CLI](https://learn.chatgpt.com/docs/codex/cli)。
是否完成识图以员工 Agent 的图片工具调用及实际图像判断为验收依据。
