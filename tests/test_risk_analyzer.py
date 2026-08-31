from __future__ import annotations

from xhs.risk_analyzer import analyze


def test_analyze_reports_category_distribution_and_top_hosts() -> None:
    entries = [
        {
            "category": "api",
            "host": "edith.xiaohongshu.com",
            "path": "/api/sns/web/v1/homefeed",
            "status": 200,
        },
        {
            "category": "api",
            "host": "edith.xiaohongshu.com",
            "path": "/api/sns/web/v1/search/notes",
            "status": 200,
        },
        {
            "host": "t2.xiaohongshu.com",
            "path": "/api/v2/collect",
            "status": 200,
        },
    ]

    report = analyze(entries)

    assert report["risk_level"] == "safe"
    assert report["category_distribution"] == {"api": 2, "other": 1}
    assert report["top_hosts"] == {
        "edith.xiaohongshu.com": 2,
        "t2.xiaohongshu.com": 1,
    }
