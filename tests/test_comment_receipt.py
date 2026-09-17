from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from xhs.comment_receipt import CommentRejectedError, submit_comment_with_receipt
from xhs.errors import ActionResultUnknownError


class Page:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[str] = []

    def evaluate(self, script: str):
        assert "scroll" not in script
        if "new MutationObserver" in script:
            self.calls.append("arm")
            return True
        if "new Promise" in script:
            self.calls.append("read")
            return self.result
        self.calls.append("cleanup")

    def click_element(self, selector: str) -> None:
        assert self.calls == ["arm"]
        self.calls.append("click")


def test_comment_success_retains_notice_and_single_click() -> None:
    page = Page({"status": "success", "text": "评论成功", "observed_at": "now"})
    result = submit_comment_with_receipt(page, feed_id="note", content="hello")
    assert result["platform_readback"] is True
    assert result["platform_receipt"]["text"] == "评论成功"
    assert result["feed_id"] == "note"
    assert page.calls == ["arm", "click", "read", "cleanup"]


@pytest.mark.parametrize(
    "status,error", [("unknown", ActionResultUnknownError), ("failed", CommentRejectedError)]
)
def test_comment_missing_or_failed_notice_does_not_resubmit(status, error) -> None:
    page = Page({"status": status})
    with pytest.raises(error):
        submit_comment_with_receipt(page, feed_id="note", content="hello")
    assert page.calls == ["arm", "click", "read", "cleanup"]


def test_notice_javascript() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for DOM observer regression")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_suffix(".cjs"))],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
