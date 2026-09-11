from __future__ import annotations

from pathlib import Path

from manager_transport import ManagerTransportError

from tests.test_manager_service import register_ready, service


def evidence(tmp_path: Path) -> dict:
    return {
        "evidence_key": "PC001:run:1:0", "machine_id": "PC001", "index": 0,
        "remote_path": "C:/worker/agent-runs/run", "destination": str(tmp_path / "received"),
        "state": "PENDING",
    }


def test_directory_is_collected_once_without_nested_duplicate(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    calls = []

    def download(_machine: dict, remote_path: str, destination: str, **kwargs: object) -> dict:
        calls.append(remote_path)
        target = Path(destination)
        (target / "nested").mkdir(parents=True)
        (target / "nested" / "result.json").write_text("{}", encoding="utf-8")
        return {"success": True}

    transport.download = download
    row = evidence(tmp_path)
    assert manager._attempt_evidence_download({"machine_id": "PC001"}, row) is None
    stored = manager.store.get("evidence_downloads", row["evidence_key"])
    assert stored["state"] == "SUCCESS"
    assert stored["evidence_type"] == "directory"
    assert (Path(row["destination"]) / "nested" / "result.json").read_text() == "{}"
    assert manager._retry_pending_evidence({"machine_id": "PC001"}) == []
    assert len(calls) == 1


def test_failed_partial_download_is_retried_and_consecutive_failure_delayed(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    successful_download = transport.download
    row = evidence(tmp_path)
    target = Path(row["destination"])
    target.write_text("old partial", encoding="utf-8")
    calls = []

    def download(_machine: dict, remote_path: str, destination: str, **kwargs: object) -> dict:
        calls.append(remote_path)
        Path(destination).write_text("partial", encoding="utf-8")
        raise ManagerTransportError("REMOTE_FAILED", "证据传输失败")

    transport.download = download
    assert manager._attempt_evidence_download({"machine_id": "PC001"}, row)
    assert target.read_text() == "old partial"
    assert not list(tmp_path.glob(".evidence-*"))
    manager._retry_pending_evidence({"machine_id": "PC001"})
    stored = manager.store.get("evidence_downloads", row["evidence_key"])
    assert stored["attempt_count"] == 2
    assert stored["next_retry_at"]
    manager._retry_pending_evidence({"machine_id": "PC001"})
    assert len(calls) == 2
    assert stored["state"] == "PENDING"
    transport.download = successful_download
    assert manager._attempt_evidence_download(
        {"machine_id": "PC001"}, {**stored, "next_retry_at": None}
    ) is None
    assert target.read_text() == "evidence"
    assert manager.store.get("evidence_downloads", row["evidence_key"])["state"] == "SUCCESS"
