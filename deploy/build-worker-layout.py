"""Build one employee's installed WorkerStation from the manager's source package."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

WORKER_PACKAGE = "WorkerStation/Workers/ProgramWorkers/packages/xiaohongshu-skills/0.1.0"


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def package_notes(machine_id: str) -> dict[str, str]:
    return {
        "README.md": f"# {machine_id} 员工程序 0.1.0\n\n"
        "程序位于 worker/runtime，技能位于 worker/skills，扩展源位于 worker/extension。\n"
        "项目根 scripts 为固定入口，source-layout.json 绑定实际安装版本。\n"
        "实例位于 WorkerStation/Workers/ProgramWorkers/instances/本机编号。\n"
        "账号、Chrome Profile、已加载扩展与历史数据沿实例的现有路径维护。\n",
        "CHANGELOG.md": "# 员工程序 0.1.0\n\n采用 V0.20 员工程序包、实例与 Agent 岗位目录。\n"
        "共用服务健康检查归入员工共享模块，固定入口解析到员工安装版本。\n",
        "ROLLBACK.md": "# 员工程序回退\n\n从本机部署记录中的 backup_root 定位 previous 代码。\n"
        "等待现有任务完成，保存当前代码后，配套恢复 scripts、布局文件和原源码目录。\n"
        "使用既有 Python 重载执行服务，并核对健康、接单与回传。\n"
        "账号配置、Chrome Profile、配对、inbox/outbox 和业务历史继续使用原位置。\n",
        "acceptance.json": json.dumps(
            {"machine_id": machine_id, "version": "0.1.0", "status": "DEVICE_VALIDATION_REQUIRED"},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    }


def build(source: Path, destination: Path, machine_id: str) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    layout = json.loads((source / "source-layout.json").read_text(encoding="utf-8"))
    if machine_id not in layout["workers"]:
        raise ValueError("员工编号未登记")
    destination.mkdir(parents=True, exist_ok=False)
    worker_source = layout["worker_source_root"]
    worker_target = WORKER_PACKAGE + "/worker"
    shutil.copytree(
        source / worker_source,
        destination / worker_target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    modules = {
        name: path.replace(worker_source, worker_target, 1)
        for name, path in layout["module_files"].items()
        if path.startswith(worker_source + "/")
    }
    # Employee repair installation belongs to the installed execution support.
    modules["worker_debug"] = worker_target + "/runtime/execution/worker_debug.py"
    shutil.copy2(
        source / layout["module_files"]["worker_debug"], destination / modules["worker_debug"]
    )
    scripts = destination / "scripts"
    scripts.mkdir()
    for name in ("__init__.py", "_role_layout.py"):
        shutil.copy2(source / "scripts" / name, scripts / name)
    for name in modules:
        module_source = Path(layout["module_files"][name])
        relative = Path(*name.split("."))
        relative = (
            relative / "__init__.py"
            if module_source.name == "__init__.py"
            else relative.with_suffix(".py")
        )
        target = scripts / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "scripts" / relative, target)
    powershell = {
        name: path.replace(worker_source, worker_target, 1)
        for name, path in layout["powershell_files"].items()
        if path.startswith(worker_source + "/")
    }
    for name in powershell:
        shutil.copy2(source / "scripts" / name, scripts / name)
    installed_layout = {
        "schema_version": layout["schema_version"],
        "project_id": layout["project_id"],
        "source_version": layout["source_version"],
        "installation_role": "worker",
        "machine_id": machine_id,
        "worker_source_root": worker_target,
        "module_files": modules,
        "module_roles": {name: layout["module_roles"][name] for name in modules},
        "powershell_files": powershell,
        "resource_roots": {
            name: path.replace(worker_source, worker_target, 1)
            for name, path in layout["resource_roots"].items()
        },
        "legacy_script_root": "scripts",
        "workers": [machine_id],
    }
    write_json(destination / "source-layout.json", installed_layout)
    webui = json.loads((source / "webui-layout.json").read_text(encoding="utf-8"))
    write_json(
        destination / "webui-layout.json",
        {
            "schema_version": 1,
            "worker_root": worker_target + "/runtime/webui",
            "shared_root": worker_target + "/runtime/webui/shared",
            "assets": {
                name: path.replace(worker_source, worker_target, 1)
                for name, path in webui["assets"].items()
                if path.startswith(worker_source + "/")
            },
        },
    )
    agent_base = Path("WorkerStation/Workers/AgentWorkers")
    (destination / agent_base).mkdir(parents=True)
    shutil.copy2(source / agent_base / "AGENTS.md", destination / agent_base / "AGENTS.md")
    shutil.copytree(source / agent_base / machine_id, destination / agent_base / machine_id)
    instance_path = (
        Path("WorkerStation/Workers/ProgramWorkers/instances") / machine_id / "instance.json"
    )
    instance = json.loads((source / instance_path).read_text(encoding="utf-8"))
    instance.update(
        definition_status="INSTALLED_LAYOUT",
        program_source_reference=worker_target + "/runtime",
        package_reference=WORKER_PACKAGE,
        environment_reference="existing-python",
    )
    write_json(destination / instance_path, instance)
    manifest_path = agent_base / machine_id / "worker_manifest.json"
    manifest = json.loads((destination / manifest_path).read_text(encoding="utf-8"))
    manifest["definition_status"] = "INSTALLED_LAYOUT"
    manifest["paths"] = {
        name: path.replace(worker_source, worker_target, 1)
        for name, path in manifest["paths"].items()
    }
    write_json(destination / manifest_path, manifest)
    # Resolve source references inside the delivered employee's own guidance.
    for path in [
        *(destination / agent_base).rglob("*.md"),
        *(destination / worker_target / "skills").rglob("*.md"),
    ]:
        text = path.read_text(encoding="utf-8").replace(worker_source, worker_target)
        path.write_text(text, encoding="utf-8")
    for name in ("pyproject.toml", "uv.lock", "LICENSE"):
        shutil.copy2(source / name, destination / name)
    (destination / "AGENTS.md").write_text(
        f"# {machine_id} 小红书员工执行项目\n\n"
        f"岗位入口：`{agent_base.as_posix()}/{machine_id}/AGENTS.md`。\n"
        f"公共执行规则：`{agent_base.as_posix()}/AGENTS.md`。\n"
        f"程序与技能版本：`{worker_target}/`；固定调用入口：`scripts/`。\n"
        "现有账号、浏览器、配对、任务状态和历史数据沿实例登记的实际路径维护。\n",
        encoding="utf-8",
    )
    (destination / "SKILL.md").write_text(
        f"# 小红书员工技能\n\n读取本机员工岗位规则及 `{worker_target}/skills/` 中当前业务技能。\n"
        f"任务调用指引：`{worker_target}/skills/employee-task-entry.md`。\n"
        "使用项目根 scripts/cli.py、scripts/employee_task_cli.py 固定入口及绝对输入文件路径。\n",
        encoding="utf-8",
    )
    (destination / "README.md").write_text(
        f"# {machine_id} 小红书员工执行包\n\n程序版本 0.1.0，采用 NexaFlow V0.20 员工目录。\n"
        f"程序：`{worker_target}`。岗位：`{agent_base.as_posix()}/{machine_id}`。\n"
        "Python、账号、Chrome、扩展配对、inbox/outbox 与恢复状态由实例路径引用。\n",
        encoding="utf-8",
    )
    write_json(
        destination / WORKER_PACKAGE / "manifest.json",
        {
            "package_id": "xiaohongshu-skills",
            "version": "0.1.0",
            "worker_type": "program",
            "runtime_root": "worker/runtime",
            "entrypoint": "scripts/manager_worker.py",
            "source_reference": worker_source,
            "manager_workspace_id": "PC023",
            "configuration": "由实例引用本机既有配置与受控存储",
        },
    )
    write_json(
        destination / "WorkerStation/Workers/worker_registry.json",
        {
            "schema_version": 1,
            "machine_id": machine_id,
            "agent_workspace": (agent_base / machine_id).as_posix(),
            "program_instance": instance_path.as_posix(),
            "program_package": WORKER_PACKAGE,
        },
    )
    write_json(
        destination
        / "WorkerStation/Workers/ProgramWorkers/environments/existing-python/environment.json",
        {
            "environment_id": "existing-python",
            "python_executable": ".venv/Scripts/python.exe",
            "path_base": "PROJECT_ROOT",
            "management": "引用本机既有虚拟环境",
        },
    )
    for name, content in package_notes(machine_id).items():
        (destination / WORKER_PACKAGE / name).write_text(content, encoding="utf-8")
    paths = sorted(
        p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()
    )
    result = {
        "machine_id": machine_id,
        "source_version": "0.1.0",
        "files": paths,
        "file_count": len(paths),
        "bytes": sum((destination / p).stat().st_size for p in paths),
    }
    write_json(destination / "deployment-files.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="构建员工 V0.20 安装目录")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--machine-id", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.destination, args.machine_id), ensure_ascii=False))
