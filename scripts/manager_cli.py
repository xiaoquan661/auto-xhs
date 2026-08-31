"""Independent manager CLI for executor registration, dispatch, and collection."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path

from manager_service import ManagerService
from manager_store import ManagerStore
from manager_transport import OpenSSHTransport
from service_errors import ServiceError

CommandHandler = Callable[[argparse.Namespace, ManagerService], dict]
ServiceFactory = Callable[[argparse.Namespace], ManagerService]


class ManagerArgumentParser(argparse.ArgumentParser):
    """Convert command-line usage failures into the shared JSON error contract."""

    def error(self, message: str) -> None:
        raise ServiceError("INVALID_ARGUMENT", f"命令参数无效：{message}")


def load_json_object(value: str) -> dict:
    """Read one UTF-8 JSON object from an explicitly absolute path."""

    source = Path(value)
    if not source.is_absolute():
        raise ServiceError("INPUT_PATH_NOT_ABSOLUTE", "JSON 输入文件必须使用绝对路径")
    source = source.resolve()
    try:
        content = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ServiceError("INPUT_FILE_NOT_FOUND", f"JSON 输入文件不存在：{source}", 404) from exc
    except (OSError, UnicodeError) as exc:
        raise ServiceError("INPUT_FILE_UNREADABLE", f"无法读取 UTF-8 JSON 文件：{source}") from exc
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ServiceError("INVALID_INPUT_JSON", f"JSON 输入文件格式错误：{source}") from exc
    if not isinstance(payload, dict):
        raise ServiceError("INPUT_JSON_NOT_OBJECT", "JSON 输入文件的根节点必须是对象")
    return payload


def create_service(args: argparse.Namespace) -> ManagerService:
    """Build the manager service without changing process-wide environment variables."""

    manager_home = _optional_absolute_directory(args.manager_home, "经理机数据目录")
    openssh_home = _optional_absolute_directory(args.openssh_home, "OpenSSH 目录")
    store = ManagerStore(root=manager_home)
    environment = dict(os.environ)
    if openssh_home:
        environment["XHS_OPENSSH_HOME"] = openssh_home
    transport = OpenSSHTransport(environ=environment)
    return ManagerService(store=store, transport=transport)


def build_parser() -> argparse.ArgumentParser:
    parser = ManagerArgumentParser(
        prog="auto-xhs-manager",
        description="通过 OpenSSH 管理 auto-xhs Windows 执行机",
    )
    parser.add_argument("--manager-home", help="经理机状态目录的绝对路径")
    parser.add_argument("--openssh-home", help="包含 ssh.exe 和 scp.exe 的绝对目录")
    resources = parser.add_subparsers(dest="resource", required=True)

    machine = resources.add_parser("machine", help="注册和控制执行机")
    machine_actions = machine.add_subparsers(dest="action", required=True)
    machine_register = machine_actions.add_parser("register", help="注册执行机")
    _add_input_file(machine_register, "包含执行机配置的 UTF-8 JSON 绝对路径")
    machine_register.set_defaults(handler=_machine_register)
    machine_list = machine_actions.add_parser("list", help="列出执行机")
    machine_list.set_defaults(handler=_machine_list)
    for action, help_text, handler in (
        ("check", "检查执行机", _machine_check),
        ("pause", "暂停执行机", _machine_pause),
        ("resume", "恢复执行机", _machine_resume),
    ):
        command = machine_actions.add_parser(action, help=help_text)
        command.add_argument("--machine-id", required=True, help="执行机编号")
        command.set_defaults(handler=handler)

    task = resources.add_parser("task", help="派发和控制远程任务")
    task_actions = task.add_subparsers(dest="action", required=True)
    task_dispatch = task_actions.add_parser("dispatch", help="派发任务")
    _add_input_file(task_dispatch, "包含任务请求的 UTF-8 JSON 绝对路径")
    task_dispatch.set_defaults(handler=_task_dispatch)
    task_list = task_actions.add_parser("list", help="列出经理机任务")
    task_list.set_defaults(handler=_task_list)
    task_status = task_actions.add_parser("status", help="读取任务状态")
    task_status.add_argument("--task-id", required=True, help="经理机任务编号")
    task_status.add_argument("--refresh", action="store_true", help="先从执行机对账状态")
    task_status.set_defaults(handler=_task_status)
    for action, help_text, handler in (
        ("pause", "暂停任务", _task_pause),
        ("resume", "恢复任务", _task_resume),
        ("reconcile", "对账任务", _task_reconcile),
    ):
        command = task_actions.add_parser(action, help=help_text)
        command.add_argument("--task-id", required=True, help="经理机任务编号")
        command.set_defaults(handler=handler)

    transfer = resources.add_parser("transfer", help="传输任务包或功能包")
    _add_input_file(transfer, "包含传输请求的 UTF-8 JSON 绝对路径")
    transfer.set_defaults(handler=_transfer)

    version = resources.add_parser("version", help="查看或设置功能包版本策略")
    version_actions = version.add_subparsers(dest="action", required=True)
    version_list = version_actions.add_parser("list", help="读取执行机功能包版本登记")
    version_list.add_argument("--machine-id", required=True, help="执行机编号")
    version_list.add_argument("--function-id", help="仅查看指定功能编号")
    version_list.set_defaults(handler=_version_list)
    version_set = version_actions.add_parser("set", help="设置正式、试用或停用策略")
    _add_input_file(version_set, "包含版本策略的 UTF-8 JSON 绝对路径")
    version_set.set_defaults(handler=_version_set)

    collect = resources.add_parser("collect", help="回收执行结果和增量数据")
    collect.add_argument("--machine-id", required=True, help="执行机编号")
    collect.add_argument(
        "--no-download-evidence",
        "--skip-evidence",
        action="store_false",
        dest="download_evidence",
        help="只回收结构化结果，不下载证据文件",
    )
    collect.set_defaults(handler=_collect, download_evidence=True)

    result = resources.add_parser("result", help="查看已回收并汇总的结果")
    result_actions = result.add_subparsers(dest="action", required=True)
    result_list = result_actions.add_parser("list", help="列出已入库结果")
    result_list.add_argument("--machine-id", help="仅查看指定执行机")
    result_list.set_defaults(handler=_result_list)
    return parser


def execute_command(args: argparse.Namespace, service: ManagerService) -> dict:
    handler: CommandHandler = args.handler
    result = handler(args, service)
    if not isinstance(result, dict):
        raise ServiceError("INVALID_SERVICE_RESPONSE", "经理机服务未返回 JSON 对象")
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    service_factory: ServiceFactory | None = None,
) -> int:
    try:
        args = build_parser().parse_args(argv)
        service = (service_factory or create_service)(args)
        result = execute_command(args, service)
    except ServiceError as exc:
        _output(exc.to_dict())
        return 2
    except Exception as exc:
        _output(
            {
                "success": False,
                "error": {"code": "MANAGER_CLI_ERROR", "message": str(exc)},
            }
        )
        return 2
    _output(result)
    return 0 if result.get("success", True) else 2


def _add_input_file(parser: argparse.ArgumentParser, help_text: str) -> None:
    parser.add_argument(
        "--input-file",
        "--request-file",
        dest="input_file",
        required=True,
        help=help_text,
    )


def _optional_absolute_directory(value: str | None, label: str) -> str | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ServiceError("INPUT_PATH_NOT_ABSOLUTE", f"{label}必须使用绝对路径")
    return str(path.resolve())


def _machine_register(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.register_machine(load_json_object(args.input_file))


def _machine_list(_args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_machines()


def _machine_check(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.check_machine(args.machine_id)


def _machine_pause(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.pause_machine(args.machine_id)


def _machine_resume(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.resume_machine(args.machine_id)


def _task_dispatch(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.dispatch_task(load_json_object(args.input_file))


def _task_list(_args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_tasks()


def _task_status(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.get_task(args.task_id, refresh=args.refresh)


def _task_pause(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.pause_task(args.task_id)


def _task_resume(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.resume_task(args.task_id)


def _task_reconcile(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.reconcile_task(args.task_id)


def _transfer(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.transfer(load_json_object(args.input_file))


def _version_list(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_function_versions(args.machine_id, args.function_id)


def _version_set(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.set_function_version_policy(load_json_object(args.input_file))


def _collect(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.collect_results(
        args.machine_id,
        download_evidence=args.download_evidence,
    )


def _result_list(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_results(args.machine_id)


def _output(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
