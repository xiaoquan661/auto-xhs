"""Shared read-only application services for CLI, WebUI, and agents."""

from __future__ import annotations

from typing import Callable

from account_doctor import diagnose_accounts
from account_identity import load_identity_record
from account_manager import AccountConfig, list_accounts, load_account, public_config
from account_runtime import evaluate_profile_connection
from capability_registry import list_capability_policies
from xhs.bridge import BridgePage


class ServiceError(RuntimeError):
    """Stable application error that can be mapped by every entry point."""

    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_dict(self) -> dict:
        return {
            "success": False,
            "error": {"code": self.code, "message": self.message},
        }


class ApplicationService:
    """Read-only product boundary shared by CLI and the local HTTP API."""

    def __init__(
        self,
        *,
        account_lister: Callable[[], list[AccountConfig]] = list_accounts,
        account_loader: Callable[..., AccountConfig] = load_account,
        diagnostic: Callable[..., dict] = diagnose_accounts,
        page_factory: Callable[..., BridgePage] = BridgePage,
        identity_loader: Callable[[str], dict | None] = load_identity_record,
    ) -> None:
        self._account_lister = account_lister
        self._account_loader = account_loader
        self._diagnostic = diagnostic
        self._page_factory = page_factory
        self._identity_loader = identity_loader

    def health(self) -> dict:
        return {
            "success": True,
            "service": "auto-xhs-local",
            "status": "ok",
            "api_version": "v1",
        }

    def list_capabilities(self) -> dict:
        capabilities = [item.to_dict() for item in list_capability_policies()]
        return {
            "success": True,
            "capabilities": capabilities,
            "summary": {
                "total": len(capabilities),
                "enabled_in_v1": sum(bool(item["enabled_in_v1"]) for item in capabilities),
                "schedulable": sum(
                    bool(item["supports_scheduling"]) for item in capabilities
                ),
            },
        }

    def list_accounts(self) -> dict:
        return {
            "success": True,
            "accounts": [public_config(config) for config in self._account_lister()],
        }

    def get_account_status(self, account: str, *, bridge_url: str | None = None) -> dict:
        config = self._load_account(account)
        page = self._page_factory(bridge_url or config.bridge_url, account=config.name)
        bridge_status = page.get_server_status()
        runtime = evaluate_profile_connection(config, bridge_status)
        identity = self._identity_summary(config.name)
        state, next_action = _account_state(runtime, identity)
        connection_ready = bool(
            runtime["bridge_running"]
            and runtime["extension_connected"]
            and runtime["profile_verified"]
        )
        return {
            "success": True,
            "server_running": runtime["bridge_running"],
            "extension_connected": runtime["extension_connected"],
            "profile_verified": runtime["profile_verified"],
            "connection_ready": connection_ready,
            "ready": state == "READY",
            "status": state,
            "next_action": next_action,
            "runtime": runtime,
            "identity": identity,
            "bridge": bridge_status,
            "account": public_config(config),
        }

    def doctor_account(self, account: str | None = None) -> dict:
        return self._diagnostic(account, page_factory=self._page_factory)

    def _load_account(self, account: str) -> AccountConfig:
        try:
            return self._account_loader(account)
        except (FileNotFoundError, ValueError) as exc:
            raise ServiceError("ACCOUNT_NOT_FOUND", str(exc), 404) from exc

    def _identity_summary(self, account: str) -> dict:
        record = self._identity_loader(account)
        current = (record or {}).get("current") or {}
        return {
            "recorded": bool(record),
            "user_id": str(current.get("user_id") or ""),
            "nickname": str(current.get("nickname") or ""),
            "observed_at": str(current.get("observed_at") or ""),
        }


def _account_state(runtime: dict, identity: dict) -> tuple[str, str | None]:
    if not runtime["bridge_running"]:
        return "BLOCKED", "启动或检查该账号的 Bridge"
    if not runtime["extension_connected"]:
        return "BLOCKED", "手动打开对应 Chrome Profile 并保持扩展在线"
    if not runtime["profile_verified"]:
        return "BLOCKED", "检查 Profile 绑定和扩展配对"
    if not identity["recorded"]:
        return "IDENTITY_REQUIRED", "在 WebUI 中完成登录身份核验"
    return "IDENTITY_CHECK_REQUIRED", "运行只读登录和当前 UID 核验后进入 READY"
