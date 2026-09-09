"""Run one memory/persona feature from its packaged source snapshot."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
PROJECT = PACKAGE / "runtime" / "project"
sys.path.insert(0, str(PROJECT / "scripts"))


def absolute_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("输入文件和数据目录必须使用绝对路径")
    return path.resolve()


def execute(function_id: str, action: str, root: Path, request: dict) -> dict:
    from manager_service import ManagerService
    from manager_store import ManagerStore
    from manager_worker_service import ManagerWorkerService

    if function_id == "MEMORY-02":
        if action == "save" and request.get("layer", "short_term") not in {"short_term", "handoff"}:
            raise ValueError("员工短期记忆入口支持 short_term 和 handoff")
        if action == "save":
            request.setdefault("layer", "short_term")
        return ManagerWorkerService(root=root).memory_action(action, request)
    if function_id == "MEMORY-03" and action == "record":
        return ManagerWorkerService(root=root).memory_action("save", request)
    manager = ManagerService(store=ManagerStore(root))
    if function_id == "REPLY-01":
        if action == "save":
            return manager.save_persona(request)
        if action == "list":
            return manager.list_personas()
        return manager.get_persona(str(request["persona_id"]))
    if function_id == "MEMORY-03":
        if action == "collect":
            return manager.collect_results(
                str(request["machine_id"]),
                download_evidence=bool(request.get("download_evidence", True)),
            )
        if action == "ingest":
            return manager.ingest_memory(request)
    if (
        function_id == "MEMORY-01"
        and action == "save"
        and request.get("layer")
        not in {
            "shared_core",
            "account_long_term",
        }
    ):
        raise ValueError("长期记忆入口支持 shared_core 和 account_long_term")
    return manager.memory_action(action, request)


def main() -> int:
    manifest = json.loads((PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=manifest["package_name"])
    parser.add_argument("--action", choices=manifest["allowed_actions"], default="inspect")
    parser.add_argument("--state-root", help="经理或员工服务的数据目录绝对路径")
    parser.add_argument("--input-file", help="UTF-8 JSON 请求文件绝对路径")
    args = parser.parse_args()
    if args.action == "inspect":
        print(
            json.dumps(
                {
                    key: manifest[key]
                    for key in (
                        "function_id",
                        "task_code",
                        "function_name",
                        "version",
                        "allowed_actions",
                        "test_status",
                        "real_device_status",
                        "installation_status",
                    )
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.action == "verify":
        with tempfile.TemporaryDirectory(prefix="xhs-memory-package-tests-") as temporary:
            root = Path(temporary)
            env = {
                **os.environ,
                "XHS_PRODUCT_HOME": str(root / "product"),
                "XHS_MANAGER_HOME": str(root / "manager"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONIOENCODING": "utf-8",
            }
            env.pop("PYTHONPATH", None)
            return subprocess.call(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "pytest",
                    "-c",
                    str(PACKAGE / "pytest.ini"),
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    str(PACKAGE / "tests"),
                ],
                cwd=PACKAGE,
                env=env,
            )
    if not args.state_root:
        parser.error("执行功能时需要 --state-root")
    try:
        root = absolute_path(args.state_root)
        request = (
            json.loads(absolute_path(args.input_file).read_text(encoding="utf-8-sig"))
            if args.input_file
            else {}
        )
        if not isinstance(request, dict):
            raise ValueError("请求 JSON 必须是对象")
        result = execute(manifest["function_id"], args.action, root, request)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("success", True) else 2
    except Exception as exc:
        result = (
            exc.to_dict()
            if hasattr(exc, "to_dict")
            else {
                "success": False,
                "error": {"code": "PACKAGE_INPUT_ERROR", "message": str(exc)},
            }
        )
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
