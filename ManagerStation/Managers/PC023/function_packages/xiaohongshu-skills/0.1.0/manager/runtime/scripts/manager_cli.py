"""Independent manager CLI for executor registration, dispatch, and collection."""

from __future__ import annotations

# Resolve stable imports when this canonical command is launched directly.
if __name__ == "__main__":
    import sys as _layout_sys
    from pathlib import Path as _LayoutPath

    _layout_root = next(
        parent for parent in _LayoutPath(__file__).resolve().parents
        if (parent / "source-layout.json").is_file()
    )
    _layout_sys.path.insert(0, str(_layout_root / "scripts"))
    import _role_layout as _role_layout


import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from manager_service import ManagerService
from manager_store import ManagerStore
from manager_transport import OpenSSHTransport
from service_errors import ServiceError

CommandHandler = Callable[[argparse.Namespace, ManagerService], dict]
ServiceFactory = Callable[[argparse.Namespace], ManagerService]


def _review_comments(args: argparse.Namespace, service: ManagerService) -> dict:
    from comment_receipt_review import review_comments

    return review_comments(service.store, load_json_object(args.input_file))


def _database_action(args: argparse.Namespace, service: ManagerService) -> dict:
    from manager_database import ManagerDatabase

    database = ManagerDatabase(service.store.root)
    synced = database.sync_store(service.store)
    if args.action == "collect":
        return service.collect_business_data(args.machine_id)
    if args.action == "sync":
        return synced
    if args.action == "schema":
        return database.schema()
    payload = load_json_object(args.input_file)
    return database.query(
        payload.get("sql", ""), payload.get("parameters"), int(payload.get("limit", 200))
    )


def _acceptance_report(args: argparse.Namespace, service: ManagerService) -> dict:
    from operations_acceptance import acceptance_report

    return acceptance_report(service.store.snapshot(), day=args.date or "", accounts=args.account)


def _debug_action(args: argparse.Namespace, service: ManagerService) -> dict:
    payload = load_json_object(args.input_file) if args.action != "list" else {}
    return service.debug_action(args.action, payload)


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
    review = resources.add_parser("review-comments", help="根据用户核对修订已提交评论的归属")
    _add_input_file(review, "包含机器、槽位、回执编号和核对依据的 UTF-8 JSON 绝对路径")
    review.set_defaults(handler=_review_comments)
    database = resources.add_parser("database", help="经理运营数据库与全字段只读查询")
    database_actions = database.add_subparsers(dest="action", required=True)
    for name in ("sync", "schema", "query", "collect"):
        command = database_actions.add_parser(name)
        if name == "collect":
            command.add_argument("--machine-id", required=True)
        if name == "query":
            _add_input_file(command, "包含 sql、parameters、limit 的查询 JSON 文件")
        command.set_defaults(handler=_database_action)
    acceptance = resources.add_parser("acceptance", help="逐动作目标、实际与成功证据")
    acceptance.add_argument("--date", help="按完成日期筛选 YYYY-MM-DD；省略则历史累计")
    acceptance.add_argument("--account", action="append", help="机器:账号；可重复指定")
    acceptance.set_defaults(handler=_acceptance_report)

    debug = resources.add_parser("debug", help="员工报障、经理修复和安装验收")
    debug_actions = debug.add_subparsers(dest="action", required=True)
    for action in (
        "list",
        "report",
        "show",
        "claim",
        "diagnose",
        "build",
        "dispatch",
        "apply",
        "reconcile",
        "verify",
        "accept",
        "resolve",
        "reopen",
        "rollback",
    ):
        command = debug_actions.add_parser(action)
        if action != "list":
            _add_input_file(command, "调试操作 JSON 文件的绝对路径")
        command.set_defaults(handler=_debug_action)

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

    daily = resources.add_parser("daily-plan", help="保存和读取每日账号操作计划")
    daily_actions = daily.add_subparsers(dest="action", required=True)
    daily_save = daily_actions.add_parser("save", help="保存每日数量计划")
    _add_input_file(daily_save, "包含日期和账号操作数量的 UTF-8 JSON 绝对路径")
    daily_save.set_defaults(handler=_daily_plan_save)
    daily_list = daily_actions.add_parser("list", help="读取每日数量计划")
    daily_list.set_defaults(handler=_daily_plan_list)
    for action, method, help_text in (
        ("generate", "generate_daily_plan", "生成每日运营建议草稿"),
        ("preview", "preview_daily_plan", "查看已保存版本的执行材料"),
        ("submit", "submit_daily_plan", "提交已保存版本到经理执行队列"),
    ):
        command = daily_actions.add_parser(action, help=help_text)
        _add_input_file(command, "包含 plan_date、version 或账号草稿的 JSON 绝对路径")
        command.set_defaults(handler=_daily_plan_action, daily_method=method)
    daily_prepare = daily_actions.add_parser("prepare", help="将已保存每日计划交给经理 Agent")
    daily_prepare.add_argument("--date", required=True, help="计划日期 YYYY-MM-DD")
    _add_input_file(daily_prepare, "账号动作目标及素材参数 JSON 文件")
    daily_prepare.set_defaults(handler=_daily_plan_prepare)
    daily_inputs = daily_actions.add_parser("inputs", help="追加每日任务的目标与素材")
    daily_inputs.add_argument("--operation-task-id", required=True)
    _add_input_file(daily_inputs, "包含 action_inputs 对象的 JSON 绝对路径")
    daily_inputs.set_defaults(handler=_daily_plan_inputs)

    memory = resources.add_parser("memory", help="账号记忆、经验审核和来源追溯")
    memory_actions = memory.add_subparsers(dest="action", required=True)
    for action in (
        "save",
        "search",
        "review",
        "history",
        "recall",
        "update",
        "usage",
        "review-pending",
    ):
        command = memory_actions.add_parser(action)
        _add_input_file(command, "记忆操作 JSON 文件的绝对路径")
        command.set_defaults(handler=_memory_action)

    employee = resources.add_parser("employee", help="员工机运营配置与汇总")
    employee_actions = employee.add_subparsers(dest="action", required=True)
    employee_summary = employee_actions.add_parser("summary", help="读取运营指标与昨日日报")
    employee_summary.set_defaults(handler=_employee_summary)
    employee_config = employee_actions.add_parser("configure", help="更新账号运营配置")
    employee_config.add_argument("--machine-id", required=True, help="执行机编号")
    employee_config.add_argument("--account", required=True, help="账号槽位")
    _add_input_file(employee_config, "运营配置 JSON 文件的绝对路径")
    employee_config.set_defaults(handler=_employee_configure)

    persona = resources.add_parser("persona", help="保存和读取账号人设卡")
    persona_actions = persona.add_subparsers(dest="action", required=True)
    persona_save = persona_actions.add_parser("save", help="新建或更新账号人设卡")
    _add_input_file(persona_save, "包含账号人设卡的 UTF-8 JSON 绝对路径")
    persona_save.set_defaults(handler=_persona_save)
    persona_list = persona_actions.add_parser("list", help="列出账号人设卡")
    persona_list.set_defaults(handler=_persona_list)
    persona_show = persona_actions.add_parser("show", help="读取一张账号人设卡")
    persona_show.add_argument("--persona-id", required=True, help="人设卡编号")
    persona_show.set_defaults(handler=_persona_show)

    goal = resources.add_parser("goal", help="保存和管理运营目标")
    goal_actions = goal.add_subparsers(dest="action", required=True)
    goal_save = goal_actions.add_parser("save", help="保存运营目标草稿")
    _add_input_file(goal_save, "包含运营目标的 UTF-8 JSON 绝对路径")
    goal_save.set_defaults(handler=_goal_save)
    goal_list = goal_actions.add_parser("list", help="列出运营目标")
    goal_list.set_defaults(handler=_goal_list)
    goal_show = goal_actions.add_parser("show", help="读取一个运营目标")
    goal_show.add_argument("--goal-id", required=True, help="运营目标编号")
    goal_show.set_defaults(handler=_goal_show)
    for action, help_text, handler in (
        ("activate", "启用目标供 Agent 规划", _goal_activate),
        ("pause", "暂停运营目标", _goal_pause),
        ("complete", "完成运营目标", _goal_complete),
    ):
        command = goal_actions.add_parser(action, help=help_text)
        command.add_argument("--goal-id", required=True, help="运营目标编号")
        command.set_defaults(handler=handler)

    operation_task = resources.add_parser("operation-task", help="管理WebUI提交的运营任务")
    operation_task_actions = operation_task.add_subparsers(dest="action", required=True)
    operation_submit = operation_task_actions.add_parser("submit", help="提交运营任务")
    _add_input_file(operation_submit, "包含运营任务的 UTF-8 JSON 绝对路径")
    operation_submit.set_defaults(handler=_operation_task_submit)
    operation_list = operation_task_actions.add_parser("list", help="列出经理Agent任务收件箱")
    operation_list.add_argument("--state", help="仅查看指定状态")
    operation_list.set_defaults(handler=_operation_task_list)
    operation_show = operation_task_actions.add_parser("show", help="读取一个运营任务")
    operation_show.add_argument("--operation-task-id", required=True, help="运营任务编号")
    operation_show.set_defaults(handler=_operation_task_show)
    operation_claim = operation_task_actions.add_parser("claim", help="经理Agent领取运营任务")
    operation_claim.add_argument("--operation-task-id", required=True, help="运营任务编号")
    operation_claim.add_argument("--agent-id", required=True, help="经理Agent编号")
    operation_claim.set_defaults(handler=_operation_task_claim)
    for action, help_text, handler in (
        ("plan", "记录经理Agent运营计划", _operation_task_plan),
        ("finish", "记录运营任务最终状态", _operation_task_finish),
    ):
        command = operation_task_actions.add_parser(action, help=help_text)
        command.add_argument("--operation-task-id", required=True, help="运营任务编号")
        _add_input_file(command, "包含经理Agent编号和计划或结果的 UTF-8 JSON 绝对路径")
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

    schedule = resources.add_parser("schedule", help="登记和观察Agent调度计划")
    schedule_actions = schedule.add_subparsers(dest="action", required=True)
    schedule_register = schedule_actions.add_parser("register", help="登记Agent调度计划")
    _add_input_file(schedule_register, "包含Agent调度计划的 UTF-8 JSON 绝对路径")
    schedule_register.set_defaults(handler=_schedule_register)
    schedule_list = schedule_actions.add_parser("list", help="列出Agent调度计划")
    schedule_list.set_defaults(handler=_schedule_list)
    for action, help_text, handler in (
        ("pause", "暂停调度计划", _schedule_pause),
        ("resume", "恢复调度计划", _schedule_resume),
    ):
        command = schedule_actions.add_parser(action, help=help_text)
        command.add_argument("--schedule-id", required=True, help="Agent调度计划编号")
        command.set_defaults(handler=handler)
    schedule_run = schedule_actions.add_parser("record-run", help="记录调度运行结果")
    schedule_run.add_argument("--schedule-id", required=True, help="Agent调度计划编号")
    _add_input_file(schedule_run, "包含调度运行结果的 UTF-8 JSON 绝对路径")
    schedule_run.set_defaults(handler=_schedule_record_run)

    report = resources.add_parser("operation-report", help="生成和查看经理机运营报告")
    report_actions = report.add_subparsers(dest="action", required=True)
    report_generate = report_actions.add_parser("generate", help="生成运营报告")
    _add_input_file(report_generate, "包含报告筛选范围的 UTF-8 JSON 绝对路径")
    report_generate.set_defaults(handler=_operation_report_generate)
    report_list = report_actions.add_parser("list", help="列出运营报告")
    report_list.set_defaults(handler=_operation_report_list)
    report_revise = report_actions.add_parser("revise", help="生成报告修订版")
    report_revise.add_argument("--report-id", required=True, help="原报告编号")
    _add_input_file(report_revise, "包含修订原因的 UTF-8 JSON 绝对路径")
    report_revise.set_defaults(handler=_operation_report_revise)

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


def _daily_plan_save(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.save_daily_plan(load_json_object(args.input_file))


def _daily_plan_list(_args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_daily_plans()


def _daily_plan_action(args: argparse.Namespace, service: ManagerService) -> dict:
    return getattr(service, args.daily_method)(load_json_object(args.input_file))


def _daily_plan_prepare(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.prepare_daily_plan(args.date, load_json_object(args.input_file))


def _daily_plan_inputs(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.supply_daily_inputs(args.operation_task_id, load_json_object(args.input_file))


def _employee_summary(_args: argparse.Namespace, service: ManagerService) -> dict:
    return service.employee_summary()


def _employee_configure(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.save_account_operations(
        args.machine_id, args.account, load_json_object(args.input_file)
    )


def _memory_action(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.memory_action(args.action, load_json_object(args.input_file))


def _persona_save(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.save_persona(load_json_object(args.input_file))


def _persona_list(_args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_personas()


def _persona_show(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.get_persona(args.persona_id)


def _goal_save(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.save_goal(load_json_object(args.input_file))


def _goal_list(_args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_goals()


def _goal_show(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.get_goal(args.goal_id)


def _goal_activate(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.activate_goal(args.goal_id)


def _goal_pause(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.pause_goal(args.goal_id)


def _goal_complete(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.complete_goal(args.goal_id)


def _operation_task_submit(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.create_operation_task(load_json_object(args.input_file))


def _operation_task_list(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_operation_tasks(args.state)


def _operation_task_show(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.get_operation_task(args.operation_task_id)


def _operation_task_claim(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.claim_operation_task(
        args.operation_task_id,
        {"manager_agent_id": args.agent_id},
    )


def _operation_task_plan(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.record_operation_plan(
        args.operation_task_id,
        load_json_object(args.input_file),
    )


def _operation_task_finish(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.finish_operation_task(
        args.operation_task_id,
        load_json_object(args.input_file),
    )


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


def _schedule_register(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.register_schedule(load_json_object(args.input_file))


def _schedule_list(_args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_schedules()


def _schedule_pause(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.pause_schedule(args.schedule_id)


def _schedule_resume(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.resume_schedule(args.schedule_id)


def _schedule_record_run(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.record_schedule_run(args.schedule_id, load_json_object(args.input_file))


def _operation_report_generate(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.generate_operation_report(load_json_object(args.input_file))


def _operation_report_list(_args: argparse.Namespace, service: ManagerService) -> dict:
    return service.list_operation_reports()


def _operation_report_revise(args: argparse.Namespace, service: ManagerService) -> dict:
    return service.revise_operation_report(args.report_id, load_json_object(args.input_file))


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
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
