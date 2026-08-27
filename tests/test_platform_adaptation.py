from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.account_identity import identity_status, record_current_identity
from scripts.account_manager import add_account, load_account, public_config
from scripts.application_service import ApplicationService
from scripts.bridge_server import BridgeServer
from scripts.business_runner import BusinessRunner
from scripts.chrome_lifecycle import launch_chrome_profile
from scripts.inbound_event_service import InboundEventService
from scripts.operations_db import OperationsDatabase
from scripts.platforms import (
    CapabilitySupport,
    detect_platform,
    get_platform_capability,
    get_platform_spec,
    rewrite_platform_url,
)
from scripts.product_store import ProductStore
from scripts.task_service import TaskService


def _extension_source(tmp_path: Path) -> Path:
    source = tmp_path / "extension-source"
    source.mkdir()
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    (source / "bridge_config.js").write_text("// test", encoding="utf-8")
    return source


def test_platform_specs_detect_and_rewrite_known_routes() -> None:
    rednote = get_platform_spec("rednote")

    assert rednote.creator_origin == "https://creator.rednote.com"
    assert detect_platform("https://www.rednote.com/discovery/item/note-1") == "rednote"
    assert detect_platform("creator.xiaohongshu.com") == "xiaohongshu"
    assert rewrite_platform_url(
        "https://www.xiaohongshu.com/explore/note-1?xsec_token=t",
        "rednote",
    ) == "https://www.rednote.com/discovery/item/note-1?xsec_token=t"
    assert rewrite_platform_url(
        "https://creator.xiaohongshu.com/publish/publish?source=official",
        "rednote",
    ) == "https://creator.rednote.com/publish/publish?source=official"


def test_rednote_account_is_persisted_and_legacy_slot_defaults_to_xiaohongshu(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    monkeypatch.setenv("XHS_UNIVERSAL_EXTENSION_DIR", str(tmp_path / "shared-extension"))
    config = add_account(
        "rednote-a",
        platform="rednote",
        bridge_port=19931,
        extension_source=_extension_source(tmp_path),
    )

    assert config.platform == "rednote"
    assert config.schema_version == 3
    assert public_config(config)["platform_display_name"] == "RedNote"

    legacy_dir = tmp_path / "accounts" / "legacy"
    legacy_dir.mkdir()
    legacy_path = legacy_dir / "account.json"
    legacy_path.write_text(
        json.dumps(
            {
                "name": "legacy",
                "bridge_port": 19932,
                "chrome_user_data_dir": None,
                "extension_dir": None,
                "schema_version": 2,
            }
        ),
        encoding="utf-8",
    )

    legacy = load_account("legacy", allow_legacy_default=False)
    assert legacy.platform == "xiaohongshu"
    assert "platform" not in json.loads(legacy_path.read_text(encoding="utf-8"))


def test_identity_comparison_includes_platform(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    monkeypatch.setenv("XHS_UNIVERSAL_EXTENSION_DIR", str(tmp_path / "shared-extension"))
    add_account(
        "rednote-a",
        platform="rednote",
        bridge_port=19933,
        extension_source=_extension_source(tmp_path),
    )
    record_current_identity(
        "rednote-a",
        {
            "platform": "rednote",
            "logged_in": True,
            "user_id": "uid-1",
            "nickname": "RedNote User",
            "profile_url": "https://www.rednote.com/user/profile/uid-1",
        },
        source="test",
    )

    assert identity_status(
        "rednote-a",
        {"platform": "rednote", "logged_in": True, "user_id": "uid-1"},
    )["comparison"] == "match"
    assert identity_status(
        "rednote-a",
        {"platform": "xiaohongshu", "logged_in": True, "user_id": "uid-1"},
    )["comparison"] == "platform_mismatch"


def test_bridge_rejects_cross_platform_extension_and_cli() -> None:
    server = BridgeServer("rednote-a", platform="rednote")

    assert server._validate_extension_handshake({"platform": "rednote"}) is None
    assert "平台" in server._validate_extension_handshake(
        {"platform": "xiaohongshu"}
    )
    assert server._validate_cli_request(
        {"method": "evaluate", "platform": "rednote"}
    ) is None
    assert "平台" in server._validate_cli_request(
        {"method": "evaluate", "platform": "xiaohongshu"}
    )
    assert server._resolve_pairing_platform({"platform": "rednote"}) == "rednote"
    with pytest.raises(ValueError, match="重载扩展"):
        server._resolve_pairing_platform({})


def test_rednote_profile_launch_uses_rednote_home(tmp_path) -> None:
    user_data = tmp_path / "User Data"
    (user_data / "Profile 4").mkdir(parents=True)
    config = SimpleNamespace(
        name="rednote-a",
        platform="rednote",
        chrome_user_data_dir=str(user_data),
        chrome_profile_directory="Profile 4",
        profile_mode="existing",
    )
    commands = []

    launch_chrome_profile(
        config,
        executable_finder=lambda: Path(r"C:\Program Files\Google\Chrome\chrome.exe"),
        process_factory=lambda command, **_kwargs: commands.append(command)
        or SimpleNamespace(pid=1),
    )

    assert commands[0][-1] == "https://www.rednote.com/explore"


def test_task_snapshot_and_capability_gate_are_platform_specific(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    tasks = TaskService(store, platform_resolver=lambda _account: "rednote")
    task = tasks.create(
        source="webui",
        account_slot="rednote-a",
        capability="search-feeds",
        request_summary="搜索",
        parameters={"keyword": "coffee"},
    )

    assert task["platform"] == "rednote"
    assert get_platform_capability("rednote", "search-feeds") is CapabilitySupport.EXPERIMENTAL
    assert get_platform_capability("rednote", "like-feed") is CapabilitySupport.UNKNOWN
    assert get_platform_capability("rednote", "future-command") is CapabilitySupport.UNKNOWN


def test_application_service_blocks_unknown_rednote_capability_before_execution(
    tmp_path,
) -> None:
    config = SimpleNamespace(name="rednote-a", platform="rednote")
    executed = []
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(
            lambda *_args: executed.append(True) or {"success": True}
        ),
    )
    service.get_account_status = lambda _account: {
        "ready": True,
        "next_action": None,
        "identity": {"live_user_id": "uid-1"},
    }
    task = service.create_task(
        source="webui",
        account_slot="rednote-a",
        capability="like-feed",
        request_summary="点赞",
        parameters={"feed_id": "note-1", "xsec_token": "token"},
    )["task"]

    result = service.execute_task(task["task_id"])

    assert result["task"]["state"] == "BLOCKED"
    assert result["task"]["error_code"] == "PLATFORM_CAPABILITY_UNAVAILABLE"
    assert executed == []


def test_operations_database_persists_event_platform(tmp_path) -> None:
    events = InboundEventService(OperationsDatabase(tmp_path / "operations"))

    result = events.record(
        account_slot="rednote-a",
        platform="rednote",
        event_type="note_comment",
        platform_event_id="comment-1",
        occurred_at="2026-08-24T10:00:00+00:00",
    )

    assert result["event"]["platform"] == "rednote"


def test_extension_sources_register_both_platforms() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "extension" / "manifest.json").read_text("utf-8"))
    background = (root / "extension" / "background.js").read_text("utf-8")
    popup = (root / "extension" / "popup.js").read_text("utf-8")
    interceptor = (root / "extension" / "interceptor.js").read_text("utf-8")

    assert "https://*.rednote.com/*" in manifest["host_permissions"]
    assert "https://creator.rednote.com/*" in manifest["content_scripts"][0]["matches"]
    assert "getOrOpenPlatformTab" in background
    assert "命令平台与扩展绑定不一致" in background
    assert "platformProtocolVersion" in background
    assert "当前页面平台与配对槽位不一致" in background
    assert 'const isNeutralPage = url === "about:blank"' in background
    assert "扩展后台仍是旧版本" in popup
    assert "rednote" in interceptor
