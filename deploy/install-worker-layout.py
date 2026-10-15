"""Install a staged employee directory while keeping machine state in its existing stores."""

from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def local_tasks() -> list[dict[str, Any]]:
    with urllib.request.urlopen("http://127.0.0.1:8766/api/v1/tasks", timeout=20) as response:
        return json.load(response)["tasks"]


def check_idle(state: dict[str, Any]) -> None:
    executions = state["executions"].values()
    protocol = [
        e["task_id"]
        for e in executions
        if e.get("state") in {"ACCEPTED", "RUNNING", "PAUSE_REQUESTED"}
    ]
    active = [
        t["task_id"]
        for t in local_tasks()
        if t.get("state") in {"PENDING", "QUEUED", "RUNNING", "CANCEL_REQUESTED"}
    ]
    pending = list((Path(state["config"]["task_inbox_root"]) / "pending").glob("*.json"))
    if protocol or active or pending:
        raise RuntimeError(f"员工机有待执行或运行任务：{protocol + active}，inbox={len(pending)}")


def inventory(root: Path) -> dict[str, int]:
    return {
        p.relative_to(root).as_posix(): p.stat().st_size for p in root.rglob("*") if p.is_file()
    }


def install(archive: Path, project: Path, backup: Path, machine_id: str) -> dict[str, Any]:
    archive, project, backup = archive.resolve(), project.resolve(), backup.resolve()
    state_root = Path.home() / ".xhs/auto-xhs/manager-worker"
    state = read_json(state_root / "worker-state.json")
    if state["config"]["machine_id"] != machine_id:
        raise RuntimeError("目标员工编号与配置不符")
    check_idle(state)
    staged = archive.parent / "unpacked"
    with zipfile.ZipFile(archive) as package:
        package.extractall(staged)
    manifest = read_json(staged / "deployment-files.json")
    if manifest["machine_id"] != machine_id:
        raise RuntimeError("安装包员工编号不符")
    if not backup.is_relative_to(project.parent) or backup.is_relative_to(project):
        raise RuntimeError("备份必须位于项目同级部署记录目录")
    backup.mkdir(parents=True, exist_ok=False)
    protected = []
    xhs = Path.home() / ".xhs"
    for account in (xhs / "accounts").iterdir():
        if account.is_dir() and not account.name.startswith("."):
            for name in ("account.json", "login-identity.json", "extension-pairing.json"):
                path = account / name
                if path.is_file():
                    protected.append(path)
    for path in (
        project / "extension/bridge_config.js",
        xhs / "universal-extension/bridge_config.js",
    ):
        if path.is_file():
            protected.append(path)
    protected_files = []
    for index, path in enumerate(protected):
        copy = backup / "protected-reference" / str(index)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, copy)
        protected_files.append({"path": str(path), "reference": str(copy)})
    baseline = {
        "config": state["config"],
        "machine": state["machine"],
        "account_management": state["account_management"],
        "historical_executions": {
            key: value["state"] for key, value in state["executions"].items()
        },
        "protected_files": protected_files,
        "inbox": inventory(Path(state["config"]["task_inbox_root"])),
        "outbox": inventory(Path(state["config"]["task_outbox_root"])),
    }
    (backup / "baseline.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    retired = []
    # Keep the complete previous source trees, including any local changes, for rollback.
    for name in ("scripts", "skills", "prompts", "webui"):
        old = (project / name).resolve()
        target = (backup / "previous" / name).resolve()
        if not old.is_relative_to(project) or not target.is_relative_to(backup):
            raise RuntimeError("源码归档路径超出项目或备份目录")
        if old.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(target))
            retired.append(name)
    files = [*manifest["files"], "deployment-files.json"]
    for relative in files:
        target = project / relative
        if target.is_file():
            previous = backup / "previous" / relative
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, previous)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged / relative, target)
    different = [
        relative
        for relative in files
        if not filecmp.cmp(staged / relative, project / relative, shallow=False)
    ]
    changed_config = [
        item["path"]
        for item in protected_files
        if not filecmp.cmp(item["path"], item["reference"], shallow=False)
    ]
    if different or changed_config:
        raise RuntimeError(f"文件回读差异：{different}，账号配置差异：{changed_config}")
    receipt = {
        "success": True,
        "machine_id": machine_id,
        "project_root": str(project),
        "backup_root": str(backup),
        "file_count": len(files),
        "bytes": sum((project / p).stat().st_size for p in files),
        "byte_comparison": "MATCH",
        "protected_file_count": len(protected_files),
        "previous_source_trees": retired,
        "source_layout": read_json(project / "source-layout.json"),
    }
    (backup / "installation.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {key: value for key, value in receipt.items() if key != "source_layout"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="安装员工 V0.20 执行目录")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--machine-id", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            install(args.archive, args.project_root, args.backup_root, args.machine_id),
            ensure_ascii=False,
        )
    )
