"""Joint Bridge and Chrome Profile lifecycle for one account slot."""

from __future__ import annotations

import time
from collections.abc import Callable

from account_manager import AccountConfig
from account_runtime import evaluate_profile_connection
from bridge_lifecycle import start_bridge, stop_bridge
from chrome_lifecycle import launch_chrome_profile
from xhs.bridge import BridgePage


def start_account_runtime(
    config: AccountConfig,
    *,
    bridge_url: str | None = None,
    bridge_starter: Callable[..., dict] = start_bridge,
    chrome_launcher: Callable[..., dict] = launch_chrome_profile,
    page_factory: Callable[..., BridgePage] = BridgePage,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 15,
    interval: float = 1.0,
) -> dict:
    """Start Bridge, open the bound Profile when needed, then verify the connection."""
    bridge = bridge_starter(config)
    url = bridge_url or config.bridge_url
    page = page_factory(
        url,
        account=config.name,
        account_id=config.account_id,
        bridge_token=config.bridge_token,
    )
    bridge_status = page.get_server_status()
    runtime = evaluate_profile_connection(config, bridge_status)
    chrome = {
        "launch_attempted": False,
        "launched": False,
        "managed_process": False,
        "profile_directory": config.chrome_profile_directory or "Default",
    }

    if runtime["bridge_running"] and not runtime["extension_connected"]:
        try:
            chrome = chrome_launcher(config)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            return _result(
                config,
                bridge,
                runtime,
                chrome={**chrome, "launch_attempted": True, "error": str(exc)},
                error_code="CHROME_START_FAILED",
                message=f"Bridge 已启动，但无法打开绑定的 Chrome Profile：{exc}",
            )
        for _ in range(attempts):
            sleep(interval)
            bridge_status = page.get_server_status()
            runtime = evaluate_profile_connection(config, bridge_status)
            if runtime["extension_connected"]:
                break

    if not runtime["bridge_running"]:
        return _result(
            config,
            bridge,
            runtime,
            chrome=chrome,
            error_code="BRIDGE_NOT_READY",
            message="Bridge 启动失败，请在 WebUI 运行诊断",
        )
    if runtime["extension_connected"] and not runtime["profile_verified"]:
        return _result(
            config,
            bridge,
            runtime,
            chrome=chrome,
            error_code="PROFILE_MISMATCH",
            message="已连接扩展的 Profile 或扩展实例与当前槽位不一致",
        )
    if not runtime["extension_connected"]:
        return _result(
            config,
            bridge,
            runtime,
            chrome=chrome,
            error_code="EXTENSION_NOT_CONNECTED",
            message=(
                "已打开绑定的 Chrome Profile，但扩展仍未连接；"
                "请确认该 Profile 已加载并配对 XHS Bridge 扩展"
            ),
        )
    return _result(
        config,
        bridge,
        runtime,
        chrome=chrome,
        message="账号运行环境已启动，Bridge、扩展和 Profile 均已核验",
    )


def restart_account_runtime(
    config: AccountConfig,
    *,
    bridge_stopper: Callable[..., dict] = stop_bridge,
    account_starter: Callable[..., dict] = start_account_runtime,
) -> dict:
    """Restart only Bridge, then restore the joint runtime without closing Chrome."""
    bridge_stopper(config)
    return account_starter(config)


def _result(
    config: AccountConfig,
    bridge: dict,
    runtime: dict,
    *,
    chrome: dict,
    message: str,
    error_code: str | None = None,
) -> dict:
    ready = bool(
        runtime["bridge_running"]
        and runtime["extension_connected"]
        and runtime["profile_verified"]
    )
    result = {
        **bridge,
        **runtime,
        "account": config.name,
        "ready": ready,
        "status": "READY" if ready else "BLOCKED",
        "chrome": chrome,
        "chrome_launched": bool(chrome.get("launched")),
        "message": message,
    }
    if error_code:
        result["error_code"] = error_code
    return result
