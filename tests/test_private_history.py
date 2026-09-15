from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from xhs.private_history import _READ_HISTORY


def read_history(chats: list[dict], pages: list[list[dict]], *, limit: int = 50) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("JavaScript runtime required")
    fixture = json.dumps({"chats": chats, "pages": pages})
    script = """
const fixture = FIXTURE;
let call = 0;
class Service {
  getMessageRepository() {}
  getChatRepository() {}
}
Service.instance = {currentUserId:'owner',sdk:{
  async getChatList({page}) {
    return {success:true,data:{chats:fixture.chats,total:fixture.chats.length,nextTs:-1}};
  },
  async getChatMessageHistory() {
    const data = fixture.pages[call++];
    return data ? {success:true,data} : {success:false};
  }
}};
global.window = {webpackChunkxhs_pc_web:[],
  __autoXhsIMRequire:{c:{fixture:{exports:{Service}}}}};
(async()=>console.log(JSON.stringify(await eval(SOURCE))))();
""".replace("FIXTURE", fixture).replace(
        "SOURCE", json.dumps(_READ_HISTORY.replace("LIMIT", str(limit)))
    )
    result = subprocess.run(
        [node, "-e", script], capture_output=True, encoding="utf-8", check=True
    )
    return json.loads(result.stdout)


def message(index: int, *, sender: str = "peer", kind: int = 1) -> dict:
    return {
        "id": f"message-{index}",
        "storeId": index,
        "senderId": sender,
        "createdAt": 1789000000000 + index,
        "content": json.dumps({"contentType": kind, "content": "hello"}),
    }


def test_message_pagination_preserves_individual_ids_times_and_direction() -> None:
    rows = [message(i) for i in range(31, 1, -1)]
    result = read_history(
        [{"chatUserId": "peer", "maxStoreId": 31}],
        [rows, [message(2), message(1, sender="owner", kind=3), message(0, kind=4)]],
    )
    assert result["complete"]
    assert len(result["messages"]) == 31
    incoming = [row for row in result["messages"] if row["role"] == "peer"]
    assert len(incoming) == 30
    assert incoming[0]["occurred_at"] == 1789000000031
    assert result["messages"][-1]["content_type"] == 3
    assert result["batches"][-1]["cursor"] == 1


def test_history_failure_and_account_limit_keep_coverage_partial() -> None:
    chats = [{"chatUserId": "peer", "maxStoreId": 31}]
    assert not read_history(chats, [])["complete"]
    assert not read_history(chats, [[]])["complete"]
    chats.append({"chatUserId": "second", "maxStoreId": 0})
    assert not read_history(chats, [[]], limit=1)["complete"]


def test_confirmed_empty_history_is_complete() -> None:
    result = read_history([], [])
    assert result["complete"] and result["messages"] == []
