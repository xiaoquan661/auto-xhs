# 员工源码调用合同 0.1.0

本合同描述员工固定程序、JSON协议、Agent业务调用和回执边界。源码由PC023维护，
各机器安装位置、队列和状态通过程序实例及员工manifest引用。

## 固定程序与任务入口

- `scripts/manager_worker.py`接受JSON stdin并输出JSON stdout。请求action选择固定协议动作；
  payload为JSON对象，顶层业务字段与payload组合后按现有dispatch_request路由。
- `scripts/employee_task_cli.py`和员工task-entry.cmd提供已接任务的固定业务调用。
- `scripts/cli.py --account <槽位> <业务命令>`调用账号隔离的ApplicationService及平台能力。
- `scripts/executor_server.py`提供员工本机API和执行服务；其ApplicationService消费任务缓存与本地记忆。
- 当前技能位于worker/skills，浏览器能力位于worker/extension，提示材料位于worker/prompts。
  实现通过完整项目checkout的source-layout和固定入口加载。

## 接单和执行事实

accept-task、wake-task、start-task、run-task、task-status、pause-task和resume-task等动作
按当前manager_worker协议执行；参数由固定入口help及经理原任务给出。
任务包含原task_id、设备、账号、capability、parameters、authorization、允许项目版和允许功能版。
经理投递关联dispatch_id、manager_id、账号人设/目标快照以及SFTP结果回收约定。

员工读取inbox/pending，领取后归claimed，沿原任务身份生成执行记录及业务结果。
程序使用本机绑定账号、串行锁、暂停开关及授权配额。Agent读取当前笔记或会话上下文，
结合原任务人设生成内容，再通过固定业务能力提交并按平台证据记录结果。

## 输出及回传

回执沿outbox的acknowledgements、events、results及index.json记录，
保留原任务/执行标识、账号、状态、业务汇总、逐项结果、增量事件、证据路径及未完成原因。
项目project_version和功能actual_version分别返回；原任务结果未知时沿原执行核对。
经理离线时员工本地留存，恢复后由经理按原回执回收。

agent-runs保存任务上下文与执行材料；agent-locks、pending-outcomes、daily-creator、
daily-passive、logs及memory采用现有协议状态位置。角色规则和账号业务人设分别读取。
完整操作与回执说明见项目根docs/EMPLOYEE-OPERATIONS.md、docs/EMPLOYEE-EXECUTION-RULES.md及
当前worker/skills/employee-task-entry.md。
