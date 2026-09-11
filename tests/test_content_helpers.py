from __future__ import annotations

from pathlib import Path

import pytest
from image_downloader import ImageDownloader, process_images
from title_utils import calc_title_length
from web_session import create_session_token


@pytest.mark.parametrize("title,expected", [("hello", 3), ("你好世界", 4), ("💇‍♀️", 5)])
def test_title_length_preserves_utf16_weighting(title: str, expected: int) -> None:
    assert calc_title_length(title) == expected


def test_process_images_preserves_local_and_download_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local.jpg"
    local.write_bytes(b"local image")
    downloaded = tmp_path / "downloaded.jpg"
    calls: list[str] = []

    def download(_self: ImageDownloader, url: str) -> str:
        calls.append(url)
        return str(downloaded)

    monkeypatch.setattr(ImageDownloader, "download_image", download)
    url = "https://example.com/image.jpg"
    assert process_images([str(local), url], str(tmp_path / "cache")) == [
        str(local.resolve()),
        str(downloaded),
    ]
    assert calls == [url]


def test_session_token_uses_existing_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def token_urlsafe(size: int) -> str:
        calls.append(size)
        return "test-session-token"

    monkeypatch.setattr("web_session.secrets.token_urlsafe", token_urlsafe)
    assert create_session_token() == "test-session-token"
    assert calls == [24]
