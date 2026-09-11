from __future__ import annotations

import re
from pathlib import Path


def test_shared_metric_order_and_hover_source() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "webui/employee-panel.js").read_text(encoding="utf-8")
    fields = script.split("const metricFields =", 1)[1].split(";", 1)[0]
    assert re.findall(r'\["([a-z_]+)",', fields) == [
        "browse", "publish", "like", "comment", "private_message", "friend_request",
        "reply", "private_reply", "new_followers", "received_likes",
        "received_comments", "received_private_messages",
    ]
    assert '<small>员工机巡检回传</small>' not in script
    assert 'title="员工机定时巡检执行或采集，经 Outbox 回传后展示"' in script
    assert 'metricFields.map(([, label]) => `<th>${label}</th>`)' in script
    assert '<div class="daily-metric-group">主动动作</div>' in script
    assert 'metric.kind === "reply"' in script
    assert '>被动动作与信息</div>' in script
