"""Build and verify the four REQ-20260904-001 memory/persona feature packages."""

from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
VERSION = "1.1"
TASK_CODE = "REQ-20260904-001"
FEATURES = {
    "MEMORY-01": (
        "记忆模块",
        "建立共享核心与账号长期记忆",
        "经理机记忆服务与 WebUI",
        ["save", "search", "review", "history", "recall"],
    ),
    "MEMORY-02": ("记忆模块", "保存员工短期记忆", "员工机记忆服务", ["save", "search", "recall"]),
    "REPLY-01": (
        "回复模块",
        "配置账号人格",
        "经理机人格服务、员工任务快照与 WebUI",
        ["save", "list", "show"],
    ),
    "MEMORY-03": (
        "记忆模块",
        "回传动作事实与会话记忆",
        "员工 outbox 与经理机回收服务",
        ["record", "collect", "ingest", "search", "history"],
    ),
}
FRONT_TESTS = [
    "test_manager_front_behavior.cjs",
    "test_employee_panel.cjs",
    "test_manager_identity.cjs",
]


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_dependencies() -> list[Path]:
    modules = {}
    for path in (PROJECT / "scripts").rglob("*.py"):
        relative = path.relative_to(PROJECT / "scripts").with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules[".".join(parts)] = path
    pending = ["manager_cli", "manager_worker", "manager_worker_async", "web_server", "cli"]
    selected = set()
    while pending:
        name = pending.pop()
        if name not in modules or name in selected:
            continue
        selected.add(name)
        path = modules[name]
        package = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    prefix = package[: len(package) - node.level + 1]
                    base = ".".join([*prefix, *([base] if base else [])])
                targets = [base, *(f"{base}.{a.name}" for a in node.names)]
            for target in targets:
                target = target.removeprefix("scripts.")
                pieces = target.split(".")
                pending.extend(".".join(pieces[:i]) for i in range(1, len(pieces) + 1))
    return sorted({modules[name] for name in selected})


def run(command: list[str], cwd: Path, log: Path) -> str:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = completed.stdout + completed.stderr
    log.write_text(output, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"验证失败，查看：{log}\n{output[-3000:]}")
    return output


def replay_cases(function: str) -> list[dict]:
    if function == "MEMORY-01":
        return [
            {
                "name": "长期经验审核生效",
                "layer": "account_long_term",
                "operation": "review",
                "content": "先核对光照",
            },
            {
                "name": "不同UID经验隔离",
                "layer": "account_long_term",
                "operation": "identity",
                "content": "账号专业经验",
            },
        ]
    if function == "MEMORY-02":
        return [
            {
                "name": "过期观察恢复",
                "layer": "short_term",
                "operation": "expiry",
                "content": "临时观察",
                "expires_at": "2000-01-01T00:00:00+00:00",
            },
            {
                "name": "短期观察身份隔离",
                "layer": "short_term",
                "operation": "identity",
                "content": "账号观察",
            },
        ]
    if function == "REPLY-01":
        return [
            {
                "name": "人格版本与任务快照",
                "layer": "account_long_term",
                "operation": "persona",
                "content": "自然简洁",
                "updated_content": "亲切具体",
            }
        ]
    return [
        {
            "name": "交接摘要新版和旧版重传",
            "layer": "handoff",
            "operation": "revision",
            "content": "初次观察",
            "updated_content": "核对后的观察",
        },
        {
            "name": "经验建议新版待审核",
            "layer": "account_long_term",
            "operation": "revision",
            "content": "初次经验建议",
            "updated_content": "核对后的经验建议",
        },
    ]


def build(package_root: Path, evidence_root: Path) -> list[dict]:
    staging_root = Path(tempfile.mkdtemp(prefix="packages-", dir=evidence_root))
    registry_path = package_root / "版本清单.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    shutil.copyfile(registry_path, evidence_root / "registry-before.json")
    for function, (module, _, _, _) in FEATURES.items():
        final_folder = package_root / module / function / VERSION
        final_archive = package_root / "可上传压缩包" / f"{function}_{VERSION}.zip"
        if final_folder.exists() or final_archive.exists():
            raise FileExistsError(f"版本已经存在：{final_folder}")
    sources = source_dependencies()
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    environment = {
        "device": platform.node(),
        "system": platform.platform(),
        "python": sys.version.split()[0],
        "scope": "本机隔离数据与包内源码执行",
    }
    summaries = []
    for function, (module, name, layer, actions) in FEATURES.items():
        folder = staging_root / module / function / VERSION
        archive = staging_root / "可上传压缩包" / f"{function}_{VERSION}.zip"
        final_folder = package_root / module / function / VERSION
        final_archive = package_root / "可上传压缩包" / f"{function}_{VERSION}.zip"
        for directory in ("runtime", "config", "tests", "evidence"):
            (folder / directory).mkdir(parents=True, exist_ok=True)
        snapshot = folder / "runtime/project"
        files = [
            *sources,
            PROJECT / "pyproject.toml",
            PROJECT / "README.md",
            PROJECT / "docs/ACCOUNT-MEMORY-PERSONA.md",
        ]
        for directory in ("webui", "prompts"):
            files.extend(
                p
                for p in (PROJECT / directory).rglob("*")
                if p.is_file() and p.suffix in {".js", ".css", ".html", ".md"}
            )
        files.extend(PROJECT / "tests" / test_name for test_name in FRONT_TESTS)
        for source in files:
            target = snapshot / source.relative_to(PROJECT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        shutil.copyfile(PROJECT / "package_templates/memory/runtime.py", folder / "runtime/run.py")
        shutil.copyfile(
            PROJECT / "package_templates/memory/test_package.py", folder / "tests/test_package.py"
        )
        shutil.copyfile(PROJECT / "tests/test_account_memory.py", folder / "tests/test_feature.py")
        (folder / "pytest.ini").write_text(
            "[pytest]\npythonpath = runtime/project/scripts\ntestpaths = tests\n", encoding="utf-8"
        )
        manifest = {
            "schema_version": 1,
            "package_type": "function_package",
            "business_module": f"小红书自动化项目/{module}",
            "function_id": function,
            "task_code": TASK_CODE,
            "requirement_version": "1.1",
            "function_name": name,
            "package_name": f"{function} {name}",
            "version": VERSION,
            "applicable_layer": layer,
            "execution_mode": "包内源码执行、经理员工任务上下文与持久化记忆服务",
            "supported_environment": (
                "Windows 10/11、Python 3.11+；自动验证使用 pytest，前端验证使用 Node.js"
            ),
            "dependencies": [
                "Python requests、websockets、python-socks（runtime/project/pyproject.toml）",
                "自动化测试依赖 pytest；前端测试依赖 Node.js",
                "业务运行使用已配置的经理或员工数据目录与已核验的账号身份",
            ],
            "call_relationships": [
                "runtime/run.py → runtime/project/scripts 中的对应服务",
                "ManagerMemory → AccountMemory → SQLite",
                "WorkerMemory → 员工任务快照与 outbox → ManagerMemory",
                "账号 WebUI → web_server.py → ManagerService",
            ],
            "entrypoint": "runtime/run.py",
            "allowed_actions": ["inspect", "verify", *actions],
            "rollback_version": None,
            "test_status": "PENDING_VALIDATION",
            "real_device_status": "PENDING_REAL_DEVICE_VERIFICATION",
            "review_status": "未审核",
            "upload_status": "未上传",
            "release_status": "未开放",
            "installation_status": "未安装",
            "evidence_files": [
                "RUN_RESULT.json",
                "VERIFICATION_REPORT.md",
                "evidence/project-tests.txt",
                "evidence/package-tests.txt",
                "evidence/frontend-tests.txt",
                "evidence/source-inventory.json",
            ],
            "run_result": "RUN_RESULT.json",
            "runtime_evidence": "RUN_RESULT.json",
            "self_check_report": "VERIFICATION_REPORT.md",
            "created_at": created,
        }
        write_json(folder / "manifest.json", manifest)
        write_json(
            folder / "tests/history_replay.json",
            {
                "source": "2026-09-09 账号记忆隔离复现与行为验收",
                "cases": replay_cases(function),
            },
        )
        examples = {
            "MEMORY-01": {
                "layer": "shared_core",
                "content": "表达依据实际观察",
                "source": "品牌规则",
            },
            "MEMORY-02": {
                "task_id": "TASK-EXAMPLE",
                "layer": "short_term",
                "event_id": "observation-1",
                "content": "接着阅读第三篇",
                "summary": "阅读进度",
                "ttl_hours": 168,
            },
            "REPLY-01": {
                "machine_id": "PC057",
                "account_slot": "brand-a",
                "display_name": "园艺分享者",
                "identity_background": "家庭园艺爱好者",
                "professional_direction": "盆栽养护",
                "tone": "自然具体",
                "reply_length": "1 至 2 句",
                "behavior_boundaries": "依据观察回答",
            },
            "MEMORY-03": {
                "task_id": "TASK-EXAMPLE",
                "layer": "handoff",
                "event_id": "handoff-1",
                "content": "阅读任务已完成，后续核对光照条件",
                "summary": "任务交接",
            },
        }
        write_json(folder / "config/example.json", examples[function])
        write_json(
            folder / "evidence/source-inventory.json",
            {
                "source_branch": "codex/account-memory-persona",
                "snapshot_kind": "working_tree",
                "dependency_selection": "入口及本地 Python 导入闭包，配套 WebUI、提示词和行为测试",
                "files": [str(p.relative_to(PROJECT)).replace("\\", "/") for p in files],
                "environment": environment,
            },
        )
        shutil.copyfile(evidence_root / "project-tests.txt", folder / "evidence/project-tests.txt")
        for log in ("package-tests.txt", "frontend-tests.txt"):
            (folder / "evidence" / log).write_text("等待本包验证\n", encoding="utf-8")
        write_json(
            folder / "RUN_RESULT.json", {"function_id": function, "status": "PENDING_VALIDATION"}
        )
        (folder / "VERIFICATION_REPORT.md").write_text(
            f"# {function} {name}验证报告\n", encoding="utf-8"
        )
        (folder / "CHANGELOG.md").write_text(
            f"# {function} {VERSION}\n\n{name}：持久化服务、可执行入口、历史回放和验证证据。\n",
            encoding="utf-8",
        )
        (folder / "ROLLBACK.md").write_text(
            f"# {function} 回退说明\n\n停止使用本版本入口，恢复安装前保存的应用目录与配置。\n"
            "账号记忆 SQLite 库和版本历史作为独立数据保留；"
            "如需还原数据，应使用安装前的对应数据库备份。\n"
            "生产安装时分别备份经理目录、员工目录及相关配置，并记录采用的应用版本。\n",
            encoding="utf-8",
        )
        default_action = "record" if function == "MEMORY-03" else "save"
        state_kind = "worker" if function in {"MEMORY-02", "MEMORY-03"} else "manager"
        (folder / "README.md").write_text(
            f"# {function} {name} {VERSION}\n\n需求：{TASK_CODE} v1.1。适用层：{layer}。\n\n"
            "包内 `runtime/project` 保存实现与运行依赖源码，`runtime/run.py` 为本功能入口。\n\n"
            "## 验证\n\n"
            f"```powershell\npython '{final_folder / 'runtime/run.py'}' --action verify\n```\n\n"
            "该命令使用临时数据目录执行固定测试、历史回放及记忆专项回归。"
            "依赖见包内 pyproject.toml。\n\n"
            "## 运行\n\n"
            f"```powershell\npython '{final_folder / 'runtime/run.py'}' --action {default_action} "
            f"--state-root 'C:\\xhs-data\\{state_kind}' "
            f"--input-file '{final_folder / 'config/example.json'}'\n```\n\n"
            "将数据目录和示例字段替换为实际配置。员工记忆操作使用已接收的任务编号。\n"
            f"可用动作：{', '.join(manifest['allowed_actions'])}。\n"
            + (
                "record 使用员工数据目录，collect、ingest、search、history 使用经理数据目录。\n\n"
                if function == "MEMORY-03"
                else "\n"
            )
            + "WebUI 源码随包提供，运行界面及其经理/员工服务需使用本次对应版本共同部署。\n"
            "详细接口与状态规则见 `runtime/project/docs/ACCOUNT-MEMORY-PERSONA.md`。\n\n"
            "自动化、员工机实机验收、审核、上传、开放与正式安装状态"
            "分别记录在 manifest 和验证报告。\n",
            encoding="utf-8",
        )
        output = run(
            [sys.executable, "-B", str(folder / "runtime/run.py"), "--action", "verify"],
            folder,
            folder / "evidence/package-tests.txt",
        )
        frontend_output = ""
        for test_name in FRONT_TESTS:
            log = folder / "evidence" / f"{test_name}.txt"
            frontend_output += run(["node", str(snapshot / "tests" / test_name)], snapshot, log)
        (folder / "evidence/frontend-tests.txt").write_text(frontend_output, encoding="utf-8")
        package_summary = next(line for line in reversed(output.splitlines()) if "passed" in line)
        result = {
            "function_id": function,
            "task_code": TASK_CODE,
            "version": VERSION,
            "executed_at": created,
            "status": "AUTOMATED_TESTED",
            "runtime_environment": environment,
            "automated_tests": {
                "package_tests": package_summary,
                "frontend_test_files": 3,
                "project_full_tests": (evidence_root / "project-tests.txt")
                .read_text(encoding="utf-8")
                .strip()
                .splitlines()[-1],
            },
            "local_runtime_status": "LOCAL_RUNTIME_VERIFIED",
            "real_device_status": manifest["real_device_status"],
            "upload_status": "未上传",
            "release_status": "未开放",
            "installation_status": "未安装",
        }
        write_json(folder / "RUN_RESULT.json", result)
        manifest["test_status"] = "AUTOMATED_TESTED"
        write_json(folder / "manifest.json", manifest)
        (folder / "VERIFICATION_REPORT.md").write_text(
            f"# {function} {name}验证报告\n\n"
            f"设备：{environment['device']}；系统：{environment['system']}；Python：{environment['python']}。\n\n"
            f"- 包内固定测试、历史回放及共享记忆回归：{package_summary}\n"
            f"- 项目全量回归：{result['automated_tests']['project_full_tests']}\n"
            "- 包内前端行为测试：3 个文件通过。\n"
            "- 实际运行入口：在临时数据目录创建、读取或回传测试记录，结果回读通过。\n"
            "- 员工机与真实账号连续运行：待实机验收。\n"
            "- 审核：未审核；上传：未上传；开放：未开放；正式安装：未安装。\n\n"
            "证据位于 `evidence/`；包内测试核对加载模块来自本包源码目录。\n",
            encoding="utf-8",
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for file in sorted(folder.rglob("*")):
                if file.is_file() and not any(
                    p in {"__pycache__", ".pytest_cache"} for p in file.parts
                ):
                    bundle.write(file, file.relative_to(folder).as_posix())
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise RuntimeError(f"ZIP 完整性检查失败：{archive}")
            with tempfile.TemporaryDirectory(prefix="xhs-memory-zip-verification-") as temporary:
                extracted = Path(temporary)
                bundle.extractall(extracted)
                zip_output = run(
                    [sys.executable, "-B", str(extracted / "runtime/run.py"), "--action", "verify"],
                    extracted,
                    evidence_root / f"{function}-zip-tests.txt",
                )
        verification = {
            "function_id": function,
            "task_code": TASK_CODE,
            "version": VERSION,
            "zip_integrity": "PASSED",
            "zip_tests": zip_output.strip().splitlines()[-1],
            "archive": str(final_archive),
        }
        write_json(folder / "evidence/zip-verification.json", verification)
        shutil.copyfile(
            evidence_root / f"{function}-zip-tests.txt", folder / "evidence/zip-tests.txt"
        )
        # The final ZIP also contains the extracted-runtime verification evidence.
        with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(
                folder / "evidence/zip-verification.json", "evidence/zip-verification.json"
            )
            bundle.write(folder / "evidence/zip-tests.txt", "evidence/zip-tests.txt")
        with zipfile.ZipFile(archive) as bundle:
            assert bundle.testzip() is None
            assert json.loads(bundle.read("manifest.json")) == manifest
            assert json.loads(bundle.read("evidence/zip-verification.json")) == verification
        summaries.append(
            {
                "function_id": function,
                "task_code": TASK_CODE,
                "function_name": FEATURES[function][1],
                "version": VERSION,
                "test_status": "AUTOMATED_TESTED",
                "real_device_status": manifest["real_device_status"],
                "runtime_environment_test": (
                    f"{environment['device']} / {environment['system']} / "
                    f"Python {environment['python']} / 本地隔离验证通过"
                ),
                "review_status": "未审核",
                "upload_status": "未上传",
                "release_status": "未开放",
                "installation_status": "未安装",
                "package_path": final_folder.relative_to(package_root).as_posix(),
                "zip_path": final_archive.relative_to(package_root).as_posix(),
                "allowed_managers": [],
                "allowed_devices": [],
                "allowed_accounts": [],
            }
        )
        print(json.dumps(verification, ensure_ascii=False), flush=True)
    for entry in summaries:
        for key in ("package_path", "zip_path"):
            source = (staging_root / entry[key]).resolve()
            target = (package_root / entry[key]).resolve()
            assert source.is_relative_to(staging_root.resolve())
            assert target.is_relative_to(package_root.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
    registry["packages"].extend(summaries)
    registry["updated_at"] = datetime.now().astimezone().date().isoformat()
    write_json(registry_path, registry)
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", type=Path, default=PROJECT.parent / "功能包")
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    build(args.package_root.resolve(), args.evidence_root.resolve())


if __name__ == "__main__":
    main()
