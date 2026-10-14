"""Queue daily creator metrics for the employee's local CLI and Outbox."""

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
from datetime import datetime
from typing import Any

from account_manager import list_accounts
from daily_passive_collection import eligibility, save
from manager_worker_service import ManagerWorkerService
from metric_temporality import ZONE
from product_store import ProductStore
from run_lock import RunLock


def local_published_note_ids(results: list[dict[str, Any]], slot: str) -> list[str]:
    ids: set[str] = set()
    for result in results:
        task = result.get("task") or {}
        if (result.get("account_slot") or task.get("account_slot")) != slot:
            continue
        if result.get("state") != "SUCCESS":
            continue
        if (result.get("capability") or task.get("capability")) not in {"publish", "click-publish"}:
            continue
        business = result.get("business_result") or {}
        items = result.get("item_results") or [business]
        for item in items:
            confirmed = (
                item.get("success") is True
                or item.get("status") == "SUCCESS"
                or int(item.get("published") or 0) > 0
                or int(business.get("published") or 0) > 0
            )
            if confirmed:
                note_id = item.get("note_id") or business.get("note_id")
                if note_id:
                    ids.add(str(note_id))
    return sorted(ids)


def envelope(
    machine: str,
    slot: str,
    platform: str,
    day: str,
    *,
    published_since: str = "",
    local_note_ids: list[str] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    task_id = f"daily-creator-{day}-{machine.lower()}-{slot}"
    if run_id:
        task_id += f"-{run_id}"
    result = {
        "task_id": task_id,
        "dispatch_id": task_id,
        "target_machine_id": machine,
        "allowed_versions": ["*"],
        "task": {
            "account_slot": slot,
            "platform": platform,
            "capability": "collect-operations-metrics",
            "source_type": "worker-schedule",
            "request_summary": "每日已发布作品运营数据采集",
            "write_intent": False,
            "timeout_seconds": 1200,
            "parameters": {
                "source": "creator_manager",
                "max_notes": 100,
                "scheduled_at": f"{day}T00:30:00+08:00",
                "instructions": "每日采集本槽位全部已发布作品累计指标。复用绑定浏览器和UID，"
                "不检查身份、不进入我的主页；暂停账号跳过并回传原因。刷新创作中心笔记管理，"
                "使用collect-operations-metrics --source creator_manager --max-notes 100。"
                "保存全部作品note_id、note_url、title、views、likes、favorites、comments、shares，"
                "真实0保留0，缺失为空，记录captured_at与累计口径。完整采集结果写business_result，"
                "保留snapshots及incremental_data，经finish写outbox供经理回收。"
                "只读采集，不发布或互动。同账号忙时排队，结果不完整写具体原因。",
            },
        },
    }
    if published_since:
        parameters = result["task"]["parameters"]
        parameters["published_since"] = published_since
        parameters["instructions"] += (
            f"本槽位仅采集北京时间{published_since}起（含当天）发布的作品。"
            "先从创作者中心读取作品ID和发布日期，仅对范围内ID采集指标，"
            "不采集此前作品。notes仅回传范围内作品；完整核对该日期范围后"
            "写inventory_complete=true和published_since，范围内无作品可回传空清单。"
        )
    if local_note_ids is not None:
        parameters = result["task"]["parameters"]
        parameters["note_ids"] = local_note_ids
        parameters["local_published_only"] = True
        parameters["instructions"] = (
            "每日仅采集本机本槽位成功发布回执所对应作品的累计指标。"
            f"本轮目标作品ID为{','.join(local_note_ids) or '空清单'}。"
            "使用collect-operations-metrics --source creator_manager，逐个添加--note-id。"
            "仅回传目标作品的notes；目标为空时回传完整0篇。"
            "缺失目标ID时记录missing_note_ids，不把其他已发布作品补入结果。"
            "复用绑定浏览器和UID，不检查身份、不进入我的主页；只读采集，"
            "保留真实0、captured_at和完整性，经finish写outbox供经理回收。"
        )
    return result


def run(check: bool = False, *, run_id: str = "", include_all: bool = False) -> dict[str, Any]:
    service = ManagerWorkerService()
    root = service.root / "daily-creator"
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.json"
    config = (
        json.loads(config_path.read_text(encoding="utf-8-sig"))
        if config_path.exists()
        else {
            "time": "00:30",
            "accounts": {},
        }
    )
    lock = RunLock(str(root / "schedule.lock"))
    if not lock.acquire(timeout=0.1):
        return {"state": "ALREADY_RUNNING"}
    try:
        worker = service._store.snapshot()
        product = ProductStore().snapshot()
        day = datetime.now(ZONE).date().isoformat()
        machine = worker["config"]["machine_id"]
        results = []
        for account in list_accounts():
            slot_config = config.get("accounts", {}).get(account.name, {})
            account_config = config
            if include_all:
                account_config = {
                    **config,
                    "accounts": {
                        **config.get("accounts", {}),
                        account.name: {
                            **config.get("accounts", {}).get(account.name, {}),
                            "enabled": True,
                        },
                    },
                }
            status = eligibility(worker, product, account.name, account_config)
            request = envelope(
                machine,
                account.name,
                account.platform,
                day,
                published_since=config.get("accounts", {})
                .get(account.name, {})
                .get("published_since", ""),
                local_note_ids=(
                    local_published_note_ids(
                        list(worker.get("executions", {}).values()), account.name
                    )
                    if slot_config.get("local_published_only")
                    else None
                ),
                run_id=run_id,
            )
            task_id = request["task_id"]
            if not check and status in {"READY", "DEFERRED"}:
                if task_id not in worker.get("task_index", {}):
                    service.accept_task(request)
                service.start_task(task_id)
                status = "QUEUED"
            elif not check:
                receipt = {
                    "task_id": task_id,
                    "remote_execution_id": task_id,
                    "machine_id": machine,
                    "account_slot": account.name,
                    "platform": account.platform,
                    "capability": "collect-operations-metrics",
                    "state": "PAUSED",
                    "message": "每日作品采集跳过：" + status,
                    "completed_at": datetime.now(ZONE).isoformat(),
                    "result_revision": 1,
                    "business_result": {"source": "creator_manager", "skip_reason": status},
                }
                service._publish_outbox_record(
                    "result",
                    task_id,
                    task_id,
                    {"result": receipt},
                    record_id=f"daily-creator-skip:{task_id}",
                )
            results.append({"account": account.name, "status": status, "task_id": task_id})
        report = {
            "machine": machine,
            "time": "00:30",
            "timezone": "Asia/Shanghai",
            "outbox": worker["config"]["task_outbox_root"],
            "accounts": results,
            "checked_at": datetime.now(ZONE).isoformat(),
            "check_only": check,
        }
        save(root / ("last-check.json" if check else "last-run.json"), report)
        return report
    finally:
        lock.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--include-all", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.check, run_id=args.run_id, include_all=args.include_all), ensure_ascii=False
        )
    )
