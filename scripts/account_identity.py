"""Account slot identity records and safe XHS login switching."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from account_manager import account_dir, load_account, validate_account_name
from json_io import atomic_write_json as _atomic_write_json
from json_io import read_optional_json as _read_optional_json
from platforms import (
    DEFAULT_PLATFORM,
    get_platform_spec,
    normalize_platform,
    rewrite_platform_url,
)

IDENTITY_SCHEMA_VERSION = 2


def identity_record_path(account: str) -> Path:
    return account_dir(validate_account_name(account)) / "login-identity.json"


def switch_state_path(account: str) -> Path:
    return account_dir(validate_account_name(account)) / "login-switch.json"


def identity_history_path(account: str) -> Path:
    return account_dir(validate_account_name(account)) / "login-identity-history.jsonl"


def load_identity_record(account: str) -> dict | None:
    record = _read_optional_json(identity_record_path(account))
    if record and isinstance(record.get("current"), dict):
        record = {
            **record,
            "current": normalize_observed_identity(record["current"]),
        }
    return record


def load_switch_state(account: str) -> dict | None:
    return _read_optional_json(switch_state_path(account))


def load_identity_history(account: str, *, limit: int = 20) -> list[dict]:
    if limit < 1:
        raise ValueError("limit 必须大于 0")
    path = identity_history_path(account)
    if not path.is_file():
        return []
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events[-limit:]


def normalize_observed_identity(identity: dict, *, platform: str | None = None) -> dict:
    logged_in = bool(identity.get("logged_in"))
    user_id = str(identity.get("user_id") or "").strip()
    nickname = str(identity.get("nickname") or "").strip()
    profile_url = str(identity.get("profile_url") or "").strip()
    normalized_platform = normalize_platform(identity.get("platform") or platform)
    if profile_url:
        profile_url = rewrite_platform_url(profile_url, normalized_platform)
    return {
        "platform": normalized_platform,
        "logged_in": logged_in,
        "user_id": user_id,
        "nickname": nickname,
        "profile_url": profile_url,
        "observed_at": str(identity.get("observed_at") or _utc_now()),
    }


def record_current_identity(
    account: str,
    identity: dict,
    *,
    source: str,
    label: str | None = None,
) -> dict:
    account = validate_account_name(account)
    expected_platform = _account_platform(account)
    current = normalize_observed_identity(identity, platform=expected_platform)
    if not current["logged_in"]:
        raise ValueError("当前未登录，不能记录账号身份")
    if not current["user_id"]:
        raise ValueError("无法读取当前平台 UID，不能建立身份保护")
    if current["platform"] != expected_platform:
        raise ValueError("当前页面平台与账号槽位平台不一致，不能建立身份保护")
    payload = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "account": account,
        "current": current,
        "label": (label or "").strip(),
        "source": source,
        "updated_at": _utc_now(),
    }
    _atomic_write_json(identity_record_path(account), payload)
    return payload


def migrate_bound_platform(account: str, platform: str) -> dict:
    """Preserve the user's bound UID when they explicitly select its platform."""
    from platforms import rewrite_platform_url
    from product_store import ProductStore

    record = load_identity_record(account)
    if not record or not record.get("current", {}).get("user_id"):
        raise ValueError("槽位缺少已有 UID，不能迁移平台记录")
    if _account_platform(account) != platform:
        raise ValueError("目标平台与槽位配置不一致")
    previous = dict(record["current"])
    if previous.get("platform") != platform:
        record["current"] = {
            **previous,
            "platform": platform,
            "profile_url": rewrite_platform_url(previous.get("profile_url") or "", platform),
        }
        record.update(source="user_platform_selection", updated_at=_utc_now())
        _atomic_write_json(identity_record_path(account), record)
        _append_json_line(
            identity_history_path(account),
            {
                "event": "PLATFORM_SELECTED",
                "from": previous,
                "to": record["current"],
                "source": "user_platform_selection",
                "updated_at": _utc_now(),
            },
        )
    store = ProductStore()
    cached = store.get_setting("runtime_identity_checks", {})
    if account in cached and cached[account].get("platform") != platform:
        cached.pop(account)
        store.set_setting("runtime_identity_checks", cached)
    return record


def replace_current_identity(
    account: str,
    identity: dict,
    *,
    expected_recorded_user_id: str,
    expected_observed_user_id: str,
    source: str,
    label: str | None = None,
) -> dict:
    """Explicitly replace a mismatched slot identity and retain a local audit event."""
    account = validate_account_name(account)
    previous_record = load_identity_record(account)
    if not previous_record:
        raise RuntimeError("槽位尚未记录原账号身份，请先完成首次身份确认")
    previous = previous_record.get("current") or {}
    previous_user_id = str(previous.get("user_id") or "")
    expected_platform = _account_platform(account)
    current = normalize_observed_identity(identity, platform=expected_platform)
    if not current["logged_in"] or not current["user_id"]:
        raise RuntimeError("当前 Profile 尚未登录可识别的平台账号")
    if current["platform"] != expected_platform:
        raise RuntimeError("当前页面平台与账号槽位平台不一致")
    if previous_user_id != expected_recorded_user_id.strip():
        raise RuntimeError("槽位原 UID 已发生变化，请刷新账号状态后重新确认")
    if current["user_id"] != expected_observed_user_id.strip():
        raise RuntimeError("当前登录 UID 已发生变化，请刷新账号状态后重新确认")
    if current["user_id"] == previous_user_id:
        raise RuntimeError("当前登录账号已经与槽位记录一致，无需覆盖")

    record = record_current_identity(
        account,
        current,
        source=source,
        label=label or previous_record.get("label"),
    )
    event = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "account": account,
        "event": "identity-replaced",
        "from": previous,
        "to": record["current"],
        "label": record["label"],
        "completed_at": _utc_now(),
    }
    _append_json_line(identity_history_path(account), event)
    return event


def begin_login_switch(
    account: str,
    identity: dict,
    *,
    target_user_id: str | None = None,
    target_label: str | None = None,
) -> dict:
    account = validate_account_name(account)
    if load_switch_state(account):
        raise RuntimeError(f"账号 {account!r} 已处于换号流程中；请完成或取消现有换号流程")
    observed = normalize_observed_identity(identity, platform=_account_platform(account))
    record = load_identity_record(account)
    previous = observed if observed["logged_in"] else (record or {}).get("current")
    payload = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "account": account,
        "status": "awaiting_login",
        "from": previous,
        "target_user_id": (target_user_id or "").strip(),
        "target_label": (target_label or "").strip(),
        "started_at": _utc_now(),
    }
    _atomic_write_json(switch_state_path(account), payload)
    return payload


def complete_login_switch(
    account: str,
    identity: dict,
    *,
    expected_user_id: str | None = None,
    label: str | None = None,
) -> dict:
    account = validate_account_name(account)
    pending = load_switch_state(account)
    if not pending:
        if not str(expected_user_id or "").strip():
            raise RuntimeError("请输入新账号 UID")
        record = load_identity_record(account) or {}
        pending = {"from": record.get("current"), "target_label": record.get("label"),
                   "started_at": _utc_now()}
    expected_platform = _account_platform(account)
    current = normalize_observed_identity(identity, platform=expected_platform)
    if not current["logged_in"]:
        raise RuntimeError("新账号尚未登录，换号流程不能完成")
    if not current["user_id"]:
        raise RuntimeError("无法读取新账号的平台 UID，换号流程不能完成")
    if current["platform"] != expected_platform:
        raise RuntimeError("当前页面平台与账号槽位平台不一致，换号流程不能完成")

    previous = pending.get("from") or {}
    previous_user_id = str(previous.get("user_id") or "")
    if not expected_user_id and previous_user_id and previous_user_id == current["user_id"]:
        platform_name = get_platform_spec(expected_platform).display_name
        raise RuntimeError(f"当前仍是原{platform_name}账号；请先登录另一个账号")
    expected = (expected_user_id or pending.get("target_user_id") or "").strip()
    if expected and current["user_id"] != expected:
        raise RuntimeError(
            f"换号失败：输入 UID {expected} 与当前登录 UID {current['user_id']} 不一致"
        )

    record = record_current_identity(
        account,
        current,
        source="account-switch-complete",
        label=label or pending.get("target_label"),
    )
    event = {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "account": account,
        "event": "login-switched",
        "from": previous or None,
        "to": record["current"],
        "label": record["label"],
        "started_at": pending.get("started_at"),
        "completed_at": _utc_now(),
    }
    _append_json_line(identity_history_path(account), event)
    switch_state_path(account).unlink(missing_ok=True)
    return event


def cancel_login_switch(
    account: str,
    identity: dict,
    *,
    force: bool = False,
) -> dict:
    account = validate_account_name(account)
    pending = load_switch_state(account)
    if not pending:
        raise RuntimeError(f"账号 {account!r} 当前没有待取消的换号流程")
    current = normalize_observed_identity(identity, platform=_account_platform(account))
    previous = pending.get("from") or {}
    previous_user_id = str(previous.get("user_id") or "")
    current_user_id = current["user_id"] if current["logged_in"] else ""
    changed = bool(current_user_id and current_user_id != previous_user_id)
    if changed and not force:
        raise RuntimeError(
            "检测到当前已登录另一个小红书账号；请完成换号，或明确使用 --force 取消并重新记录身份"
        )
    switch_state_path(account).unlink(missing_ok=True)
    return {
        "account": account,
        "cancelled": True,
        "forced": force,
        "current": current,
    }


def identity_status(account: str, observed: dict | None = None) -> dict:
    account = validate_account_name(account)
    expected_platform = _account_platform(account)
    record = load_identity_record(account)
    pending = load_switch_state(account)
    current = (
        normalize_observed_identity(observed, platform=expected_platform)
        if observed is not None
        else None
    )
    comparison = "not_checked"
    if current is not None:
        if not current["logged_in"]:
            comparison = "logged_out"
        elif not record:
            comparison = "unbound"
        elif (
            current["platform"] != expected_platform
            or record.get("current", {}).get("platform") != expected_platform
        ):
            comparison = "platform_mismatch"
        elif current["user_id"] == record.get("current", {}).get("user_id"):
            comparison = "match"
        else:
            comparison = "mismatch"
    return {
        "account": account,
        "platform": expected_platform,
        "switch_pending": pending is not None,
        "comparison": comparison,
        "observed": current,
        "recorded": record,
        "switch": pending,
    }


def assert_live_identity(account: str, observed: dict) -> dict:
    status = identity_status(account, observed)
    if status["switch_pending"]:
        raise RuntimeError(
            f"账号 {account!r} 正在换号，业务任务已暂停；请先运行 account-switch-complete"
        )
    if status["comparison"] == "logged_out":
        raise RuntimeError(f"账号 {account!r} 当前未登录")
    if status["comparison"] == "mismatch":
        expected = status["recorded"]["current"]["user_id"]
        actual = status["observed"]["user_id"]
        raise RuntimeError(
            f"登录身份发生变化，已阻止任务: expected={expected!r}, actual={actual!r}；"
            "请使用安全换号流程或 account-identity --record 重新绑定"
        )
    if status["comparison"] == "platform_mismatch":
        expected = status["platform"]
        actual = (status.get("observed") or {}).get("platform") or (
            (status.get("recorded") or {}).get("current") or {}
        ).get("platform")
        raise RuntimeError(
            f"平台身份发生变化，已阻止任务: expected={expected!r}, actual={actual!r}"
        )
    return status


def _account_platform(account: str) -> str:
    try:
        return normalize_platform(load_account(account).platform)
    except FileNotFoundError:
        return DEFAULT_PLATFORM


def _append_json_line(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
