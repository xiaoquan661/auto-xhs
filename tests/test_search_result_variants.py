from __future__ import annotations

import json

from scripts.xhs.search import _apply_filters, _decode_search_feeds
from scripts.xhs.selectors import FILTER_BUTTON, FILTER_PANEL


def test_search_result_decoder_accepts_canonical_and_nested_note_shapes() -> None:
    payload = [
        {
            "id": "6a423024000000001c0276a2",
            "xsecToken": "token-a",
            "modelType": "note",
            "noteCard": {"displayTitle": "标准结构"},
        },
        {
            "note": {
                "noteId": "6a0868520000000036018c02",
                "xsecToken": "token-b",
                "displayTitle": "嵌套结构",
            }
        },
    ]

    feeds = _decode_search_feeds(json.dumps(payload))

    assert [feed.id for feed in feeds] == [
        "6a423024000000001c0276a2",
        "6a0868520000000036018c02",
    ]
    assert [feed.note_card.display_title for feed in feeds] == ["标准结构", "嵌套结构"]


def test_search_result_decoder_discards_hot_queries_and_unusable_entries() -> None:
    payload = [
        {
            "id": "query#123",
            "xsecToken": "token-hot",
            "modelType": "hot_query",
        },
        {"id": "6a423024000000001c0276a2", "modelType": "note"},
        {
            "id": "6a0868520000000036018c02",
            "xsecToken": "token-note",
            "modelType": "note",
            "noteCard": {"displayTitle": "保留"},
        },
    ]

    feeds = _decode_search_feeds(json.dumps(payload))

    assert [feed.id for feed in feeds] == ["6a0868520000000036018c02"]


def test_apply_filters_uses_shared_selector_constants(monkeypatch) -> None:
    scripts: list[str] = []

    class Page:
        def evaluate(self, script: str) -> None:
            scripts.append(script)

    monkeypatch.setattr("scripts.xhs.search._wait_for_search_feeds", lambda _page: None)

    _apply_filters(Page(), [(1, "综合")])

    assert f"document.querySelector({json.dumps(FILTER_BUTTON)})" in scripts[0]
    assert f"document.querySelector({json.dumps(FILTER_PANEL)})" in scripts[0]
