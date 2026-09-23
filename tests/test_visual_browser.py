from __future__ import annotations

import base64
import json
import struct
from pathlib import Path

import pytest
from employee_recovery import recovery_for
from visual_browser import act_on_snapshot, save_snapshot
from xhs.errors import XHSError


class Page:
    def __init__(self) -> None:
        self.calls = []
        self.fail_after = False

    def visual_snapshot(self, tab_id=None):
        if self.fail_after and self.calls:
            raise RuntimeError("snapshot unavailable")
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + struct.pack(">II", 2000, 1000)
        return {
            "data": base64.b64encode(png).decode(),
            "tab_id": tab_id or 8,
            "frame": {"width": 1000, "height": 500},
        }

    def visual_action(self, snapshot, action):
        self.calls.append(action)
        return {"dispatched": True}


def test_snapshot_waits_for_delayed_debugger_resize(tmp_path, monkeypatch):
    monkeypatch.setattr("visual_browser.time.sleep", lambda _: None)
    page = Page()
    original = page.visual_snapshot
    heights = iter([585, 529, 529])

    def capture(tab_id=None):
        result = original(tab_id)
        result["frame"]["height"] = next(heights)
        return result

    page.visual_snapshot = capture
    result = save_snapshot(page, tmp_path / "settled.png", "alpha")
    assert result["frame"]["height"] == 529


def test_image_handoff_and_pixel_coordinates(tmp_path: Path):
    page = Page()
    before = save_snapshot(page, tmp_path / "before.png", "alpha")
    action = tmp_path / "action.json"
    action.write_text(
        json.dumps({"action": "click", "x": 1000, "y": 600, "intent": "close dialog"})
    )
    after = act_on_snapshot(
        page, Path(before["snapshot_file"]), action, tmp_path / "after.png", "alpha"
    )
    assert page.calls[0]["x"] == 500 and page.calls[0]["y"] == 300
    assert Path(after["image_path"]).is_file()
    assert after["status"] == "OBSERVATION_REQUIRED"
    assert "view_image" in after["next_action"]
    with pytest.raises(XHSError, match="账号"):
        act_on_snapshot(
            page, Path(before["snapshot_file"]), action, tmp_path / "other.png", "other"
        )
    assert len(page.calls) == 1


def test_readback_failure_preserves_dispatched_action(tmp_path: Path):
    page = Page()
    before = save_snapshot(page, tmp_path / "before.png", "alpha")
    action = tmp_path / "action.json"
    action.write_text(json.dumps({"action": "key", "key": "Escape", "intent": "close"}))
    page.fail_after = True
    result = act_on_snapshot(
        page, Path(before["snapshot_file"]), action, tmp_path / "after.png", "alpha"
    )
    assert result["status"] == "RESULT_UNKNOWN" and len(page.calls) == 1


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"error": "按钮未找到", "success": False}, "observe"),
        ({"error": {"code": "LOGIN_REQUIRED"}}, "identity"),
        ({"task": {"state": "RESULT_UNKNOWN"}}, "reconcile"),
    ],
)
def test_recovery_keeps_current_step_and_inbox(payload, expected):
    result = recovery_for(payload, 2)
    assert result["kind"] == expected
    assert result["identity_check_required"] is False
    assert result["user_action_required"] == (expected == "identity")
    assert not result["refresh_inbox"] and result["resume_scope"] == "current_step"


def test_page_content_does_not_trigger_identity_recovery():
    assert recovery_for({"success": True, "result": {"content": "LOGIN_REQUIRED 换号"}}, 0) == {}


def test_bridge_transfers_large_image_response():
    import asyncio

    import websockets
    from bridge_server import MAX_MESSAGE_SIZE, BridgeServer

    async def run():
        bridge = BridgeServer("alpha")
        async with websockets.serve(
            bridge.handle, "127.0.0.1", 0, max_size=MAX_MESSAGE_SIZE
        ) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with websockets.connect(url, max_size=MAX_MESSAGE_SIZE) as extension:
                await extension.send(json.dumps({"role": "extension", "account": "alpha"}))
                await extension.recv()
                async with websockets.connect(url, max_size=MAX_MESSAGE_SIZE) as client:
                    await client.send(
                        json.dumps({"role": "cli", "account": "alpha", "method": "visual_snapshot"})
                    )
                    command = json.loads(await extension.recv())
                    data = "P" * (2 * 1024 * 1024)
                    await extension.send(
                        json.dumps({"id": command["id"], "result": {"data": data}})
                    )
                    result = json.loads(await client.recv())
                    assert result["result"]["data"] == data

    asyncio.run(run())
