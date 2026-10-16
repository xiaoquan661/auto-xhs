from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
from xhs import publish
from xhs.errors import PublishError


@pytest.fixture(autouse=True)
def fast_input(monkeypatch):
    monkeypatch.setattr(publish.time, "sleep", lambda _: None)


def _run_script(script: str, harness: str) -> None:
    node = shutil.which("node") or str(
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    )
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps(script),
        text=True,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_candidate_script_selects_exact_visible_name_and_preserves_focus():
    page = Mock()
    publish._select_topic_candidate(page, "旅行")
    _run_script(
        page.evaluate.call_args.args[0],
        r"""
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const script=JSON.parse(fs.readFileSync(0,'utf8'));
const clicked=[];
const item=(name,visible=true)=>({
 querySelector:()=>({textContent:'#'+name}),
 getBoundingClientRect:()=>({width:visible?180:0,height:30}),
 click(){clicked.push(name);},
 focus(){throw Error('候选不得夺走编辑器焦点');},
 closest(){throw Error('必须选择该候选而非祖先容器');}
});
const context={getComputedStyle:()=>({visibility:'visible'}),
 document:{querySelectorAll:()=>[item('旅行攻略'),item('旅行',false),item('旅行')]}};
assert.equal(vm.runInNewContext(script,context),true);
assert.deepEqual(clicked,['旅行']);
context.document.querySelectorAll=()=>[item('旅行攻略')];
assert.equal(vm.runInNewContext(script,context),false);
""",
    )


def test_topic_readback_requires_platform_id_and_ignores_plain_text():
    page = Mock()
    publish._read_topics(page, ".tiptap")
    _run_script(
        page.evaluate.call_args.args[0],
        r"""
const vm=require('node:vm'), fs=require('node:fs'), assert=require('node:assert/strict');
const script=JSON.parse(fs.readFileSync(0,'utf8'));
const topics=[{id:'topic-1',name:'旅行'}, {name:'文字标签'},
 {id:'newTopic_Id',name:'待创建'}, null];
const editor={textContent:'#旅行 #文字标签 #待创建 #纯文本',querySelectorAll(selector){
 assert.equal(selector,'a[data-topic]');
 return topics.map(t=>({getAttribute:()=>JSON.stringify(t)}));
}};
const context={document:{querySelector:()=>editor}};
const result=vm.runInNewContext(script,context);
assert.equal(JSON.stringify(result),JSON.stringify([{id:'topic-1',name:'旅行'}]));
""",
    )


def test_text_card_form_uses_tiptap_textbox():
    page = Mock()
    page.has_element.side_effect = lambda selector: (
        selector == ("[role='textbox'][contenteditable='true']")
    )

    assert publish._find_content_element(page) == "[role='textbox'][contenteditable='true']"


def test_waits_for_matching_candidate_and_delayed_topic_commit(monkeypatch):
    candidate = Mock(side_effect=[False, False, True])
    read = Mock(side_effect=[[], [], [{"id": "1", "name": "旅行"}]])
    monkeypatch.setattr(publish, "_select_topic_candidate", candidate)
    monkeypatch.setattr(publish, "_read_topics", read)
    page = Mock()

    publish._input_single_tag(page, ".tiptap", "旅行")

    assert candidate.call_count == 3
    assert read.call_count == 3
    assert "".join(call.args[0] for call in page.type_text.call_args_list) == "#旅行"


def test_missing_candidate_stops_before_publish(monkeypatch):
    monkeypatch.setattr(publish, "_select_topic_candidate", lambda *_: False)
    page = Mock()
    with pytest.raises(PublishError, match="未找到同名话题候选") as error:
        publish._input_single_tag(page, ".tiptap", "旅行")
    assert error.value.code == "PUBLISH_TOPICS_NOT_CONFIRMED"
    page.click_publish_control.assert_not_called()
    assert all(call.args[0] != " " for call in page.type_text.call_args_list)


def test_click_without_conversion_is_not_success(monkeypatch):
    candidate = Mock(return_value=True)
    monkeypatch.setattr(publish, "_select_topic_candidate", candidate)
    monkeypatch.setattr(publish, "_read_topics", lambda *_: [])
    with pytest.raises(PublishError, match="话题尚未生效：旅行"):
        publish._input_single_tag(Mock(), ".tiptap", "旅行")
    candidate.assert_called_once()


def test_every_topic_is_rechecked_after_next_input_and_blur(monkeypatch):
    page = Mock()
    confirmed = []
    monkeypatch.setattr(publish, "_input_single_tag", lambda p, s, tag: confirmed.append(tag))
    read = Mock(side_effect=lambda *_: [{"id": tag, "name": tag} for tag in confirmed])
    monkeypatch.setattr(publish, "_read_topics", read)

    publish._input_tags(page, ".tiptap", ["#旅行", "看展", "旅行", "火山"])

    assert confirmed == ["旅行", "看展", "火山"]
    assert read.call_count == 4
    page.click_element.assert_called_once_with(publish.TITLE_INPUT)
    # 标签完成后不再通过修改旧段落调整布局。
    assert sum("insertParagraph" in c.args[0] for c in page.evaluate.call_args_list) == 1


def test_losing_an_earlier_topic_stops_the_batch(monkeypatch):
    entered = []
    monkeypatch.setattr(publish, "_input_single_tag", lambda p, s, tag: entered.append(tag))
    monkeypatch.setattr(publish, "_read_topics", lambda *_: [{"id": "1", "name": entered[-1]}])
    with pytest.raises(PublishError, match="话题尚未生效：旅行"):
        publish._input_tags(Mock(), ".tiptap", ["旅行", "看展", "火山"])
    assert entered == ["旅行", "看展"]


def test_blur_cannot_turn_topics_back_into_plain_text(monkeypatch):
    monkeypatch.setattr(publish, "_input_single_tag", lambda *_: None)
    monkeypatch.setattr(
        publish, "_read_topics", Mock(side_effect=[[{"id": "1", "name": "旅行"}], []])
    )
    with pytest.raises(PublishError, match="话题尚未生效：旅行"):
        publish._input_tags(Mock(), ".tiptap", ["旅行"])
