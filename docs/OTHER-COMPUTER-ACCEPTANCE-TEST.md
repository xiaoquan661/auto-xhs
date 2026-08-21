# 另一台 Windows 电脑部署、使用与验收手册

适用系统：Windows 10/11

目标是在另一台电脑重新建立项目运行环境、Chrome Profile 与扩展配对，并验证账号不会串槽。本文默认
只做安装、连接和只读验收；发布、评论、回复、关注或私信需要另行明确授权和验收内容。

## 1. 准备项目

取得仓库后，在项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Prepare
```

然后双击 `start-auto-xhs.cmd`，确认浏览器能打开 `http://127.0.0.1:8765`。WebUI 应只监听本机地址。

## 2. 准备两个独立 Profile

对每个账号分别完成：

1. 在 WebUI“账号”页选择已有 Chrome Profile，或新建独立槽位；
2. 手动打开该 Profile，并确认其中登录的是预期小红书账号；
3. 在 `chrome://extensions` 打开开发者模式，加载项目根目录的 `extension`；
4. 同一 Profile 只保留一个当前项目的 XHS Bridge 扩展实例；
5. 在 WebUI 发起一次性配对，并在该 Profile 的扩展弹窗完成配对；
6. 点击“启动账号”，等待 Bridge 与扩展连接；
7. 检查并记录当前昵称和 UID。

两个 Profile 使用同一份扩展代码，但各自保存独立配对。不要复制原电脑的 Chrome Profile、登录数据
或本机账号状态目录来代替重新配对。

## 3. 只读验收

分别对两个槽位执行：

```powershell
python scripts/cli.py --account <账号别名> account-doctor --require-ready
python scripts/cli.py --account <账号别名> account-identity
python scripts/cli.py --account <账号别名> check-login
python scripts/cli.py --account <账号别名> search-feeds --keyword "验收关键词"
```

验收时核对：

- WebUI 显示的 Profile 目录与实际窗口一致；
- 配对实例、槽位别名、昵称和 UID 对应同一账号；
- 两个槽位可以分别 READY，不会把 A 的扩展连接到 B；
- 搜索结果能返回结构化数据，且未触发点赞、收藏、评论或发布；
- 关闭临时 PowerShell 后，受管 Bridge 状态仍可在 WebUI 读取。

若出现 `PROFILE_MISMATCH`、UID 不一致或 `RESULT_UNKNOWN`，停止该槽位的后续验收，先恢复正确
Profile 和身份，不通过切换账号或复用其他窗口继续。

## 4. 可选写操作验收

只有用户另行指定账号、目标、最终内容和操作范围后，才逐项测试写操作。发布必须执行
“Agent/CLI 填写 → 浏览器真实预览 → 单任务确认 → 平台回读”；私信、评论和回复也必须保留各自的
确认与结果规则。一次只验收一个明确动作，避免把安装验收扩展为批量运营。

## 5. 验收记录模板

```text
验收日期：
电脑/系统版本：
项目分支：

槽位 A：
- Profile：
- 昵称与 UID 是否匹配：
- 配对/连接状态：
- account-doctor：
- 只读搜索：

槽位 B：
- Profile：
- 昵称与 UID 是否匹配：
- 配对/连接状态：
- account-doctor：
- 只读搜索：

跨槽位串号检查：通过 / 未通过
写操作验收：未执行 / 已另行授权并附结果
遗留问题：
结论：通过 / 部分通过 / 未通过
```
