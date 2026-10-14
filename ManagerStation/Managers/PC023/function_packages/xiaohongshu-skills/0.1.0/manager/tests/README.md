# 经理职责源包测试

测试实现维护在项目根`tests/`。本目录通过`selection.json`选择相关回归用例，
`run_tests.py`使用当前解释器在实际项目根执行pytest。

```powershell
$projectRoot = (Resolve-Path .).Path
python "$projectRoot\ManagerStation\Managers\PC023\function_packages\xiaohongshu-skills\0.1.0\manager\tests\run_tests.py" --collect-only
python "$projectRoot\ManagerStation\Managers\PC023\function_packages\xiaohongshu-skills\0.1.0\manager\tests\run_tests.py"
```

依赖及工具版本按项目根`pyproject.toml`、`uv.lock`准备。完整项目回归从项目根执行。
本版验证记录见包根`acceptance.json`。
