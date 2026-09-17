from __future__ import annotations

from unittest.mock import Mock

import pytest
from xhs.publish_links import complete_publish_link, lookup_creator_link


@pytest.fixture(autouse=True)
def no_delay(monkeypatch):
    monkeypatch.setattr("xhs.publish_links.time.sleep", lambda _: None)


def test_late_response_enriches_success_without_navigation():
    page = Mock()
    observe = Mock(side_effect=[{}, {"feedback": {"code": 0, "data": {"noteId": "abc"}}}])
    result = complete_publish_link(
        page, {"verified": True, "evidence": "success_toast"}, "标题", observe=observe
    )
    assert result["note_id"] == "abc"
    assert result["verified"] is True
    page.navigate.assert_not_called()


def test_creator_link_preserves_full_url_and_avoids_profile():
    page = Mock()
    url = "https://www.xiaohongshu.com/explore/abc?xsec_token=sample"
    page.evaluate.return_value = [{"note_id": "abc", "note_url": url}]
    result = lookup_creator_link(page, "标题")
    assert result["note_url"] == url
    page.navigate.assert_called_once_with("https://creator.xiaohongshu.com/new/note-manager")


def test_ambiguous_title_does_not_assign_id():
    page = Mock()
    page.evaluate.return_value = [{"note_id": "abc"}, {"note_id": "def"}]
    assert lookup_creator_link(page, "重复标题")["link_status"] == "ambiguous"


def test_missing_link_preserves_confirmed_publish():
    page = Mock()
    page.evaluate.return_value = []
    result = complete_publish_link(page, {"verified": True, "evidence": "success_toast"}, "标题")
    assert result["verified"] is True
    assert result["link_status"] == "pending"
    page.click_publish_control.assert_not_called()


def test_reconcile_updates_same_local_publication(tmp_path, monkeypatch):
    from tests.test_publish_workflow import _service, _waiting_task

    service = _service(tmp_path)
    task = _waiting_task(service)
    service.complete_publish(task["task_id"], {"verified": True, "evidence": "success_toast"})
    page = Mock()
    page.evaluate.return_value = [{"note_id": "abc"}]
    result = service.reconcile_link(task["task_id"], account="alpha", page=page)
    assert result["result"]["note_id"] == "abc"
    rows = service.published_posts.list(account_slot="alpha")
    assert len(rows) == 1
    assert rows[0]["platform_post_id"] == "abc"
    assert service.tasks.get(task["task_id"])["state"] == "SUCCESS"
    service.reconcile_link(task["task_id"], account="alpha", page=page)
    assert page.navigate.call_count == 1


def test_worker_revision_preserves_history_and_counts(tmp_path):
    from tests.test_manager_worker import make_service, task_request

    service = make_service(tmp_path)
    request = task_request()
    request["task"]["capability"] = "publish"
    accepted = service.accept_task(request)
    execution_id = accepted["execution"]["remote_execution_id"]

    def seed(state):
        item = state["executions"][execution_id]
        item.update(
            state="SUCCESS",
            item_results=[{"title": "标题", "status": "SUCCESS"}],
            business_result={"publish": 1},
        )
        service._append_result(state, item)

    service._store.mutate(seed)
    link = {"note_id": "abc", "link_status": "resolved", "link_source": "creator_manager"}
    service.revise_publish_link("task-1", account="alpha", title="标题", link=link)
    result = service.revise_publish_link("task-1", account="alpha", title="标题", link=link)
    assert result["revised"] is False
    state = service._store.snapshot()
    assert len(state["results"]) == 2
    assert "note_id" not in state["results"][0]["item_results"][0]
    assert state["results"][1]["item_results"][0]["note_id"] == "abc"
    assert state["results"][1]["business_result"]["publish"] == 1
    assert len(state["outbox_records"]) >= 1


def test_real_capture_script_accepts_id_after_toast():
    import ast
    import json
    import shutil
    import subprocess
    from pathlib import Path

    source = Path("scripts/xhs/publish.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    script = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "function captureFromBody" in node.value
    )
    runner = r"""
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const script = JSON.parse(fs.readFileSync(0, 'utf8'));
let mutation;
class XHR {
 open() {} send() {} addEventListener(name, cb) { this.listener = cb; }
}
const context = {window: {fetch: async () => {}}, XMLHttpRequest: XHR,
 console: {log() {}, error() {}, warn() {}}, Date,
 sessionStorage: {removeItem() {}, setItem() {}}, document: {body: {}},
 MutationObserver: class {constructor(cb) {mutation = cb;} observe() {} disconnect() {}}};
vm.runInNewContext(script, context);
mutation([{addedNodes: [{nodeType: 1, textContent: '发布成功', className: ''}]}]);
assert.equal(context.window.__xhsPublishResult.source, 'toast_success');
const xhr = new XHR(); xhr.open('POST', '/publish'); xhr.send();
xhr.responseText = JSON.stringify({code: 0, data: {noteId: 'abc'}});
xhr.status = 200; xhr.listener();
assert.equal(context.window.__xhsPublishResult.data.noteId, 'abc');
"""
    node = shutil.which("node") or str(
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    )
    result = subprocess.run(
        [node, "-e", runner],
        input=json.dumps(script),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_rednote_id_uses_rednote_link_without_home_navigation():
    page = Mock(platform="rednote")
    result = complete_publish_link(page, {"verified": True, "note_id": "abc"}, "标题")
    assert result["note_url"] == "https://www.rednote.com/discovery/item/abc"
    page.navigate.assert_not_called()


def test_creator_card_impression_id_is_read_from_real_script():
    import json
    import shutil
    import subprocess
    from pathlib import Path

    from xhs.publish_links import creator_candidates

    page = Mock()
    page.evaluate.return_value = []
    creator_candidates(page, "目标作品")
    script = page.evaluate.call_args.args[0]
    runner = r"""
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const script=JSON.parse(fs.readFileSync(0,'utf8'));
const card=(title,id)=>({querySelector:()=>({textContent:title}),
 getAttribute:()=>JSON.stringify({noteTarget:{value:{noteId:id}}})});
const context={location:{href:'https://creator.rednote.com/new/note-manager'},
 window:{},URL,document:{querySelectorAll:selector=>selector==='.note-card[data-impression]'
 ?[card('目标作品','6aaa2b64000000000f03a800'),card('其他作品','6aaa2b64000000000f03a801')]:[]}};
const result=vm.runInNewContext(script,context);
assert.equal(result.length,1);assert.equal(result[0].note_id,'6aaa2b64000000000f03a800');
"""
    node = shutil.which("node") or str(
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    )
    run = subprocess.run(
        [node, "-e", runner],
        input=json.dumps(script),
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_same_publish_call_waits_for_creator_card_and_returns_link():
    page = Mock()
    page.platform = "rednote"
    page.evaluate.side_effect = [[], [], [], [], [{"note_id": "6aaa2b64000000000f03a800"}]]
    result = complete_publish_link(
        page, {"verified": True, "link_reason": "旧的待补查原因"}, "标题"
    )
    assert result["link_status"] == "resolved"
    assert result["link_reason"] == ""
    assert result["note_url"] == "https://www.rednote.com/discovery/item/6aaa2b64000000000f03a800"
    page.navigate.assert_called_once()
    page.click_publish_control.assert_not_called()
