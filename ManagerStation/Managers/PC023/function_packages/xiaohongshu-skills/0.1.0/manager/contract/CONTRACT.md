# 经理源码调用合同 0.1.0

本合同描述小红书现有经理CLI、业务请求和结果回收边界。实现通过项目根固定入口加载，
字段以当前ManagerService、ManagerStore和ManagerCLI为准。

## 入口及依赖

- `scripts/manager_cli.py`：设备、账号、人设、计划、任务、版本、数据库、验收与故障管理。
- `scripts/web_server.py`：经理业务HTTP服务和WebUI；本地ApplicationService显式注入
  ManagerReplyMemoryResolver，使用同一经理store的当前知识与人设。
- 路径及共享模块通过项目根`source-layout.json`与`scripts/_role_layout.py`定位。
- 业务状态采用`XHS_MANAGER_HOME`选择的ManagerStore；工作区manifest记录本机实际位置。

## JSON任务输入

`task dispatch --input-file <绝对路径>`读取UTF-8 JSON对象。
必需字段为task_id、machine_id、account_slot、capability。
parameters和authorization为原生JSON对象。可选关联包括schedule_id、operation_task_id、goal_id，
以及platform、request_summary、target_type、target_id、operation、source_type、write_intent、timeout_seconds。

项目允许版本由allowed_versions指定。指定function_id时，同时给出allowed_function_versions。
经理在投递包中绑定schema_version、dispatch_id、manager_id、manager_agent_id、goal_snapshot、
persona_snapshot及result_transport。所有关联沿原任务和经理配置产生。
示例及完整字段见项目根`docs/examples/manager-task.json`和`docs/MANAGER-SSH-OPERATIONS.md`。

## 收发与结果

机器配置提供项目根、inbox和outbox。经理将任务JSON原子投递到inbox/pending，
调用wake-task唤醒员工领取执行。TASK_ACK表示接收，Agent领取、业务完成、outbox写入及经理导入分别记录。
结果超时或锁占用时按原task_id核对经理及员工原件，再继续对应链路。

员工outbox回执由经理回收，按原任务、设备及执行标识去重导入；
manager-operations.db维护业务查询投影。查询先读database schema和metadata同步时间，
按database query或acceptance读取结果。数据回收核对实际执行、传输和数据库导入三步。

## 业务功能与源码身份

project_id/source_version/package_id定位职责源码包。
业务任务采用function_id和明确允许版本，外部包按task_code/function_id/version复合定位。
PC023的function_catalog保存34个既有业务版本的真实路径及经理/员工侧职责关联。
目录与来源登记、代码验证、设备安装和业务结果分别沿对应证据记录。
