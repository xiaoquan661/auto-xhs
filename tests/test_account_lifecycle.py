from __future__ import annotations

from pathlib import Path

from account_lifecycle import restart_account_runtime, start_account_runtime
from account_manager import AccountConfig
from chrome_lifecycle import launch_chrome_profile


def _config(tmp_path: Path, *, instance_id: str | None = None) -> AccountConfig:
    user_data = tmp_path / "User Data"
    (user_data / "Profile 2").mkdir(parents=True)
    return AccountConfig(
        name="alpha",
        bridge_port=19333,
        chrome_user_data_dir=str(user_data),
        extension_dir=str(tmp_path / "extension"),
        chrome_profile_directory="Profile 2",
        profile_mode="existing",
        extension_instance_id=instance_id,
    )


def test_launch_chrome_profile_uses_bound_user_data_and_profile(tmp_path) -> None:
    config = _config(tmp_path)
    calls = []

    class Process:
        pid = 321

    def process_factory(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    result = launch_chrome_profile(
        config,
        executable_finder=lambda: Path(r"C:\Program Files\Google\Chrome\chrome.exe"),
        process_factory=process_factory,
    )

    command, kwargs = calls[0]
    assert command[1] == f"--user-data-dir={config.chrome_user_data_dir}"
    assert command[2] == "--profile-directory=Profile 2"
    assert command[3] == "https://www.xiaohongshu.com/explore"
    assert kwargs["close_fds"] is True
    assert result["launched"] is True
    assert result["managed_process"] is False


def test_launch_managed_profile_allows_chrome_to_create_default_directory(tmp_path) -> None:
    user_data = tmp_path / "managed-profile"
    user_data.mkdir()
    config = AccountConfig(
        name="managed",
        bridge_port=19334,
        chrome_user_data_dir=str(user_data),
        extension_dir=str(tmp_path / "extension"),
        profile_mode="managed",
    )
    commands = []

    launch_chrome_profile(
        config,
        executable_finder=lambda: Path(r"C:\Program Files\Google\Chrome\chrome.exe"),
        process_factory=lambda command, **_kwargs: commands.append(command)
        or type("Process", (), {"pid": 322})(),
    )

    assert commands[0][2] == "--profile-directory=Default"


def test_start_account_runtime_launches_profile_and_waits_for_extension(tmp_path) -> None:
    config = _config(tmp_path)
    statuses = [
        {"extension_connected": False, "extension": None},
        {
            "extension_connected": True,
            "extension": {"profile_directory": "Profile 2"},
        },
    ]
    launched = []

    class Page:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_server_status(self):
            return statuses.pop(0)

    result = start_account_runtime(
        config,
        bridge_starter=lambda _config: {
            "bridge_running": True,
            "registered": True,
            "pid": 12,
        },
        chrome_launcher=lambda item: launched.append(item.name)
        or {"launch_attempted": True, "launched": True, "managed_process": False},
        page_factory=Page,
        sleep=lambda _seconds: None,
    )

    assert launched == ["alpha"]
    assert result["ready"] is True
    assert result["chrome_launched"] is True
    assert result["profile_verified"] is True


def test_start_account_runtime_reuses_connected_profile(tmp_path) -> None:
    config = _config(tmp_path)

    class Page:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_server_status(self):
            return {
                "extension_connected": True,
                "extension": {"profile_directory": "Profile 2"},
            }

    result = start_account_runtime(
        config,
        bridge_starter=lambda _config: {"bridge_running": True},
        chrome_launcher=lambda _config: (_ for _ in ()).throw(
            AssertionError("connected profile must be reused")
        ),
        page_factory=Page,
    )

    assert result["ready"] is True
    assert result["chrome"]["launch_attempted"] is False


def test_start_account_runtime_blocks_profile_mismatch(tmp_path) -> None:
    config = _config(tmp_path)

    class Page:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_server_status(self):
            return {
                "extension_connected": True,
                "extension": {"profile_directory": "Default"},
            }

    result = start_account_runtime(
        config,
        bridge_starter=lambda _config: {"bridge_running": True},
        page_factory=Page,
    )

    assert result["ready"] is False
    assert result["error_code"] == "PROFILE_MISMATCH"


def test_restart_account_runtime_stops_only_bridge_before_restore(tmp_path) -> None:
    config = _config(tmp_path)
    calls = []

    result = restart_account_runtime(
        config,
        bridge_stopper=lambda item: calls.append(("stop_bridge", item.name)) or {},
        account_starter=lambda item: calls.append(("start_account", item.name))
        or {"ready": True},
    )

    assert calls == [("stop_bridge", "alpha"), ("start_account", "alpha")]
    assert result["ready"] is True
