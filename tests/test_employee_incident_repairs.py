from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from xhs.bridge import BridgePage
from xhs.urls import make_search_url


def test_search_url_contains_platform_note_search_type() -> None:
    from urllib.parse import parse_qs, urlsplit

    assert parse_qs(urlsplit(make_search_url("日常收纳")).query) == {
        "keyword": ["日常收纳"],
        "source": ["web_explore_feed"],
        "type": ["51"],
    }


@pytest.mark.parametrize(
    "filename", ["test_navigation_readiness.cjs", "test_publish_tab_binding.cjs"]
)
def test_extension_incident_regression(filename: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("扩展 JavaScript 回归由安装了 Node.js 的经理端执行")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name(filename))],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_publish_confirmation_keeps_tab_binding_per_bridge_instance(monkeypatch) -> None:
    requests = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send(self, body):
            requests.append(json.loads(body))

        def recv(self, timeout):
            if requests[-1]["method"] == "select_publish_tab":
                return json.dumps({"result": {"status": "ready", "tab_id": 7}})
            return json.dumps({"result": None})

    monkeypatch.setattr("xhs.bridge.ws_client.connect", lambda *a, **kw: Connection())
    page = BridgePage()
    page.select_publish_tab("预览标题")
    page.evaluate("location.href")
    page.mouse_click(10, 20)
    BridgePage().evaluate("location.href")
    assert requests[1]["params"]["_tab_id"] == 7
    assert requests[2]["params"]["_tab_id"] == 7
    assert "_tab_id" not in requests[3]["params"]
