from __future__ import annotations

import json

import pytest
from xhs.bridge import BridgePage
from xhs.errors import CDPError


@pytest.mark.parametrize('failed', [False, True])
def test_trace_separates_extension_and_transport(monkeypatch, tmp_path, failed):
    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send(self, message):
            assert json.loads(message)['method'] == 'get_url'

        def recv(self, timeout):
            return json.dumps({
                'error': '页面未就绪' if failed else None,
                'result': 'https://www.xiaohongshu.com/explore',
                'timing': {'extension_ms': 25},
            })

    monkeypatch.setattr('xhs.bridge.ws_client.connect', lambda *a, **kw: Socket())
    trace = tmp_path / 'trace.jsonl'
    monkeypatch.setenv('XHS_EXECUTION_TRACE_FILE', str(trace))
    page = BridgePage()
    if failed:
        with pytest.raises(CDPError):
            page._call('get_url')
    else:
        assert page._call('get_url') == 'https://www.xiaohongshu.com/explore'
    record = json.loads(trace.read_text(encoding='utf-8'))
    assert record['timing']['extension_ms'] == 25
    assert record['timing']['connect_ms'] >= 0
    assert record['timing']['roundtrip_ms'] >= 0
    assert record['status'] == ('error' if failed else 'ok')
