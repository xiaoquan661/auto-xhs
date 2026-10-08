from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import employee_codex_runner as runner_module
import manager_worker_async
import pytest
from employee_codex_runner import PROBE_CAPABILITY, EmployeeCodexRunner

from tests.test_manager_worker import make_service, task_request


def task(**overrides: object) -> dict:
    return {
        "task_id": "probe-1",
        "remote_execution_id": "execution-1",
        "account_slot": "alpha",
        "capability": PROBE_CAPABILITY,
        "parameters": {"probe_token": "local-round-trip"},
        "timeout_seconds": 10,
        **overrides,
    }


def install_fake_codex(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import json, sys, time\nfrom pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "task = json.loads((root / 'task.json').read_text(encoding='utf-8'))\n"
        "sys.stdin.read()\n" + body,
        encoding="utf-8",
    )
    popen = subprocess.Popen

    def start(command: list[str], **kwargs: object) -> subprocess.Popen:
        if command[0] != "codex.exe":
            return popen(command, **kwargs)
        root = Path(command[command.index("-o") + 1]).parent
        return popen([sys.executable, str(script), str(root)], **kwargs)

    monkeypatch.setattr(runner_module, "find_codex", lambda: "codex.exe")
    monkeypatch.setattr(runner_module.subprocess, "Popen", start)


SUCCESS_SCRIPT = """
(root / 'claimed.json').write_text(json.dumps({'task_id': task['task_id']}))
result = {'state': 'SUCCESS', 'message': '完成',
          'business_result_json': json.dumps({'probe_token': 'local-round-trip'}),
          'item_results_json': '[]', 'evidence_paths': [str(root / 'claimed.json')]}
(root / 'result.json').write_text(json.dumps(result), encoding='utf-8')
"""


FILE_RESULT_SCRIPT = (
    SUCCESS_SCRIPT
    + """
result['business_result'] = json.loads(result.pop('business_result_json'))
result['item_results'] = json.loads(result.pop('item_results_json'))
(root / 'execution-result.json').write_text(json.dumps(result), encoding='utf-8')
(root / 'result.json').write_text(json.dumps({'result_file': 'execution-result.json'}))
"""
)


@pytest.mark.parametrize("readback,expected", [(True, "SUCCESS"), (False, "RESULT_UNKNOWN")])
def test_file_receipt_preserves_windows_paths_and_comment_text(
    tmp_path: Path, monkeypatch, readback: bool, expected: str
) -> None:
    item = {
        "status": "SUCCESS",
        "platform_readback": readback,
        "evidence_path": r"C:\Users\EDY\.xhs\new\test.json",
        "content": '中文评论 "原文"\n第二行',
    }
    body = (
        FILE_RESULT_SCRIPT
        + f"""
result['item_results'] = [{item!r}]
(root / 'execution-result.json').write_text(
    json.dumps(result, ensure_ascii=False), encoding='utf-8')
"""
    )
    install_fake_codex(monkeypatch, tmp_path, body)
    result = EmployeeCodexRunner(tmp_path / "state")(
        task(
            capability="post-comment",
            parameters={"count": 1},
            authorization={"authorization_id": "approved-plan"},
        )
    )
    assert result["state"] == expected
    assert result["item_results"][0]["evidence_path"] == item["evidence_path"]
    assert result["item_results"][0]["content"] == item["content"]


def test_malformed_legacy_receipt_stays_unknown(tmp_path: Path, monkeypatch) -> None:
    invalid_items = '[{"evidence_path":"C:\\Users\\EDY"}]'
    body = (
        SUCCESS_SCRIPT
        + f"""
result['item_results_json'] = {invalid_items!r}
(root / 'result.json').write_text(json.dumps(result), encoding='utf-8')
"""
    )
    install_fake_codex(monkeypatch, tmp_path, body)
    result = EmployeeCodexRunner(tmp_path / "state")(task(write_intent=True))
    assert result["state"] == "RESULT_UNKNOWN"
    assert "Invalid" in result["message"]


def test_missing_result_file_stays_unknown(tmp_path: Path, monkeypatch) -> None:
    body = (
        SUCCESS_SCRIPT
        + """
(root / 'result.json').write_text(json.dumps({'result_file': 'execution-result.json'}))
"""
    )
    install_fake_codex(monkeypatch, tmp_path, body)
    result = EmployeeCodexRunner(tmp_path / "state")(task(write_intent=True))
    assert result["state"] == "RESULT_UNKNOWN"


def test_real_child_process_claim_and_structured_result(tmp_path: Path, monkeypatch) -> None:
    install_fake_codex(monkeypatch, tmp_path, FILE_RESULT_SCRIPT)
    receipts = []
    result = EmployeeCodexRunner(tmp_path / "state", on_claim=receipts.append)(task())
    assert result["state"] == "SUCCESS"
    assert result["business_result"]["probe_token"] == "local-round-trip"
    assert len(receipts) == 1
    assert receipts[0]["task_id"] == "probe-1"
    assert Path(receipts[0]["evidence_path"]).exists()


@pytest.mark.parametrize(
    "body",
    [
        "raise SystemExit(2)",
        SUCCESS_SCRIPT.replace(
            "(root / 'claimed.json').write_text(json.dumps({'task_id': task['task_id']}))", ""
        ),
    ],
)
def test_incomplete_write_outcome_stays_unknown(tmp_path: Path, monkeypatch, body: str) -> None:
    install_fake_codex(monkeypatch, tmp_path, body)
    result = EmployeeCodexRunner(tmp_path / "state")(task(write_intent=True))
    assert result["state"] == "RESULT_UNKNOWN"


def test_timeout_terminates_owned_process(tmp_path: Path, monkeypatch) -> None:
    install_fake_codex(monkeypatch, tmp_path, "time.sleep(60)")
    result = EmployeeCodexRunner(tmp_path / "state")(task(timeout_seconds=0.05))
    assert result["state"] == "FAILED"
    assert "超时" in result["message"]


@pytest.mark.parametrize("active", [True, False])
def test_browser_checkpoint_controls_idle_timeout(
    tmp_path: Path,
    monkeypatch,
    active: bool,
) -> None:
    body = (
        "for index in range(20):\n"
        "    (root / 'command.bridge.progress.json').write_text(json.dumps({'items': index}))\n"
        "    time.sleep(0.2)\n"
        if active
        else "(root / 'command.bridge.progress.json').write_text('{}')\ntime.sleep(20)\n"
    )
    install_fake_codex(monkeypatch, tmp_path, body + FILE_RESULT_SCRIPT)
    result = EmployeeCodexRunner(tmp_path / "state")(
        task(
            parameters={"agent_idle_seconds": 1.5, "probe_token": "local-round-trip"},
            timeout_seconds=10,
        )
    )
    assert result["state"] == ("SUCCESS" if active else "FAILED"), result
    if not active:
        assert "无进展超时" in result["message"]


def test_browser_activity_keeps_absolute_timeout(tmp_path: Path, monkeypatch) -> None:
    body = (
        "for index in range(100):\n"
        "    (root / 'command.bridge.progress.json').write_text(str(index))\n"
        "    time.sleep(0.1)\n"
    )
    install_fake_codex(monkeypatch, tmp_path, body)
    result = EmployeeCodexRunner(tmp_path / "state")(
        task(parameters={"agent_idle_seconds": 5}, timeout_seconds=1)
    )
    assert "执行超时" in result["message"]


def test_pause_stops_agent(tmp_path: Path, monkeypatch) -> None:
    install_fake_codex(monkeypatch, tmp_path, "time.sleep(60)")
    result = EmployeeCodexRunner(tmp_path / "state", should_stop=lambda: True)(
        task(write_intent=True)
    )
    assert result["state"] == "RESULT_UNKNOWN"
    assert "已停止" in result["message"]


@pytest.mark.parametrize(
    "readback,expected", [(True, "PARTIAL_SUCCESS"), (False, "RESULT_UNKNOWN")]
)
def test_comment_result_uses_observed_items(
    tmp_path: Path, monkeypatch, readback, expected
) -> None:
    body = (
        SUCCESS_SCRIPT
        + f"""
result['item_results_json'] = json.dumps([{{'status': 'SUCCESS',
    'platform_readback': {readback!r}}}])
(root / 'result.json').write_text(json.dumps(result), encoding='utf-8')
"""
    )
    install_fake_codex(monkeypatch, tmp_path, body)
    result = EmployeeCodexRunner(tmp_path / "state")(
        task(
            capability="post-comment",
            parameters={"count": 5},
            authorization={"authorization_id": "approved-plan"},
        )
    )
    assert result["state"] == expected
    assert result["business_result"]["requested"] == 5
    assert result["business_result"]["succeeded"] == int(readback)


def test_async_runner_records_model_claim_and_outbox(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path / "worker")
    request = task_request()
    request["task"]["capability"] = PROBE_CAPABILITY
    request["task"]["parameters"] = {"probe_token": "local-round-trip"}
    execution = service.accept_task(request)["execution"]
    monkeypatch.setattr(manager_worker_async, "ManagerWorkerService", lambda **kwargs: service)
    install_fake_codex(monkeypatch, tmp_path, SUCCESS_SCRIPT)
    assert (
        manager_worker_async.run(str(service.root), "task-1", execution["remote_execution_id"]) == 0
    )
    final = service.task_status("task-1")["execution"]
    assert final["state"] == "SUCCESS"
    assert any(
        message["payload"].get("stage") == "agent_claimed" for message in final["agent_messages"]
    )
    assert final["business_result"]["probe_token"] == "local-round-trip"
    outbox = list((tmp_path / "worker").rglob("index.json"))
    assert outbox


def test_async_creator_metrics_uses_local_cli_runner(tmp_path: Path, monkeypatch) -> None:
    service = make_service(tmp_path / "worker")
    request = task_request()
    request["task"]["capability"] = "collect-operations-metrics"
    request["task"]["parameters"] = {"source": "creator_manager", "max_notes": 100}
    execution = service.accept_task(request)["execution"]
    monkeypatch.setattr(manager_worker_async, "ManagerWorkerService", lambda **kwargs: service)
    monkeypatch.setattr(
        manager_worker_async,
        "EmployeeCodexRunner",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Codex started")),
    )
    monkeypatch.setattr(
        manager_worker_async,
        "DirectCreatorMetricsRunner",
        lambda *args, **kwargs: (
            lambda task: {
                "state": "SUCCESS",
                "business_result": {"source": "creator_manager", "notes": []},
            }
        ),
    )
    assert (
        manager_worker_async.run(str(service.root), "task-1", execution["remote_execution_id"]) == 0
    )
    assert service.task_status("task-1")["execution"]["state"] == "SUCCESS"
    assert (tmp_path / "worker" / "task-outbox" / "index.json").exists()
