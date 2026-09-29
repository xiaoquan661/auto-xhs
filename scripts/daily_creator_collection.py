"""Queue daily creator metrics on the employee, using its existing Agent and Outbox."""

from __future__ import annotations

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


def envelope(
    machine: str,
    slot: str,
    platform: str,
    day: str,
    *,
    published_since: str = "",
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
