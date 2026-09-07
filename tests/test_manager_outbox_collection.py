from __future__ import annotations

import json
import shutil
from pathlib import Path

from manager_service import ManagerService
from manager_store import ManagerStore


class FileTransport:
    def sftp_upload_atomic(self, *args: object, **kwargs: object) -> dict:
        raise AssertionError("收集不执行员工机业务")

    def sftp_download(
        self, machine: dict, source: str, destination: str | Path, **kwargs: object
    ) -> None:
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def test_outbox_read_failure_keeps_cursor_and_recovers_with_raw_archive(tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    metadata = {
        "sequence": 1,
        "task_id": "local-reply",
        "record_type": "result",
        "relative_path": "result.json",
    }
    (outbox / "index.json").write_text(
        json.dumps(
            {
                "machine_id": "PC057",
                "high_watermark": 1,
                "records": [metadata],
            }
        ),
        encoding="utf-8",
    )
    receipt = outbox / "result.json"
    receipt.write_text("{", encoding="utf-8")
    service = ManagerService(store=ManagerStore(tmp_path / "manager"), transport=FileTransport())
    service.store.put(
        "machines",
        "PC057",
        {
            "machine_id": "PC057",
            "task_outbox_root": str(outbox),
            "task_inbox_root": str(tmp_path / "inbox"),
        },
    )
    failed = service.collect_results("PC057", download_evidence=False)
    assert failed["success"] is False
    assert failed["outbox_collection"]["pending_records"] == 1
    assert failed["next_cursor"] == 0
    assert "result.json" in failed["message"]
    assert service.store.get("machines", "PC057")["last_result_success_at"] is None

    receipt.write_text(
        json.dumps(
            {
                **metadata,
                "payload": {
                    "result": {
                        "task_id": "local-reply",
                        "remote_execution_id": "local-execution",
                        "sequence": 1,
                        "result_revision": 1,
                        "state": "SUCCESS",
                        "account_slot": "alpha",
                        "business_result": {"readback": {"verified": True}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    collected = service.collect_results("PC057", download_evidence=False)
    assert collected["success"] is True
    assert collected["collected"] == 1
    stats = collected["outbox_collection"]
    assert stats["imported_records"] == 1
    assert stats["pending_records"] == 0
    archive = Path(stats["archive_root"]) / "local-reply" / "000000000001.json"
    assert archive.read_bytes() == receipt.read_bytes()
    archive.unlink()
    service.store.update(
        "machines",
        "PC057",
        lambda item: {
            **item,
            "outbox_archive_initialized": False,
        },
    )
    repeated = service.collect_results("PC057", download_evidence=False)
    assert repeated["status"] == "IDLE"
    assert repeated["outbox_collection"]["batch_records"] == 0
    assert len(service.list_results()["result_history"]) == 1
    assert archive.read_bytes() == receipt.read_bytes()
