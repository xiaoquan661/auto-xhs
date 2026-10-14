# PC057 小红书员工工作区

本目录使用`workspace_id=PC057`定位本项目本地交付定义。共同执行规则见`../AGENTS.md`，
稳定沟通与行为偏好见`Personal.md`，本机岗位和任务边界见`Role.md`，路径见`worker_manifest.json`。
工程维护及产品依据通过项目根`AGENTS.md`与`SKILL.md`索引。

## 技能与程序入口

按当前任务读取经理维护的员工版本包：
`ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/worker/skills/<skill_id>/SKILL.md`。
员工任务固定指引位于同一`skills/employee-task-entry.md`。

登录、换号与配对使用`xhs-auth`；发布使用`xhs-publish`；发现及读取使用`xhs-explore`；
智能评论、私信、关注和点赞使用`xhs-interact`；入站回复使用`xhs-reply`；
复合运营使用`xhs-content-ops`。实际技能和程序组合采用任务授权及版本记录。

程序使用项目根固定入口`scripts/cli.py`、`scripts/employee_task_cli.py`及
`scripts/manager_worker.py`。运行时从manifest和机器配置读取实际安装根，
将项目及输入文件解析为绝对路径后调用。个人入口、公共规则及本次技能须实际进入执行上下文。
