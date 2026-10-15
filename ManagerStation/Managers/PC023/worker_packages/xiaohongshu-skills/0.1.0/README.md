# 小红书员工职责源包 0.1.0

`package_kind=project_source`，项目为`xiaohongshu-skills`，源码版为`0.1.0`，
本包标识`xiaohongshu-skills.worker-source`。本包收纳员工职责实现及其合同、测试入口和本机验证记录。

实际代码位于`worker/runtime/`。运行使用项目根的固定入口，并依赖项目根
`scripts/_role_layout.py`、`source-layout.json`、`pyproject.toml`及`uv.lock`。
`deploy/build-worker-layout.py` 将员工源包和固定入口构建为指定 PC 的安装目录，
`deploy/install-worker-layout.py` 保存旧源码并安装员工版本。
员工程序位于 `WorkerStation/Workers/ProgramWorkers/packages/xiaohongshu-skills/0.1.0/worker/`，
路径基准为员工项目根，具体模块由该机 `source-layout.json` 显式定位。

现有业务功能版本通过PC023的`package_relations/function_catalog.json`引用；
员工侧本版关联20个外部功能版本。业务任务按function_id、允许功能版本和账号授权选用，
源码版本与业务功能版本分别记录。

- `manifest.json`：源码包身份、入口、依赖和业务版本关联。
- `worker/contract/CONTRACT.md`：现有CLI、JSON及任务/结果边界。
- `worker/tests/`：对应根权威测试的选择与执行入口。
- `acceptance.json`：本机验证记录与远程交付状态。
- `CHANGELOG.md`、`ROLLBACK.md`：本版变化和代码切换方法。

角色配置、业务数据库、任务材料和协议状态采用工作区或程序实例定义中的实际路径。
