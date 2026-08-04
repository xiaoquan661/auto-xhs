"""One-time pairing protocol for the shared XHS Chrome extension."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from account_manager import (
    UNIVERSAL_EXTENSION_MODE,
    AccountConfig,
    account_dir,
    enroll_extension_instance,
    load_account,
    revoke_extension_instance,
    validate_account_name,
)

PAIRING_SCHEMA_VERSION = 1
PAIRING_BUNDLE_PREFIX = "xhs-pair-v1:"
DEFAULT_PAIRING_TTL_SECONDS = 300
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_EXTENSION_ID_RE = re.compile(r"^[a-p]{32}$")


def pairing_session_path(account: str) -> Path:
    return account_dir(validate_account_name(account)) / "extension-pairing.json"


def create_pairing_session(
    config: AccountConfig,
    *,
    ttl_seconds: int = DEFAULT_PAIRING_TTL_SECONDS,
) -> dict:
    if config.extension_mode != UNIVERSAL_EXTENSION_MODE:
        raise RuntimeError("账号尚未迁移到通用扩展；请先运行 account-sync")
    if not config.account_id or not config.bridge_token:
        raise RuntimeError("账号连接身份尚未初始化；请先运行 account-sync")
    if not 30 <= ttl_seconds <= 900:
        raise ValueError("配对有效期必须在 30-900 秒之间")

    pairing_code = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    session = {
        "schema_version": PAIRING_SCHEMA_VERSION,
        "account": config.name,
        "code_hash": _hash_pairing_code(pairing_code),
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    _atomic_write_json(pairing_session_path(config.name), session)
    bundle_payload = {
        "v": PAIRING_SCHEMA_VERSION,
        "account": config.name,
        "bridgeUrl": config.bridge_url,
        "pairingCode": pairing_code,
        "profileDirectory": config.chrome_profile_directory or "Default",
        "expiresAt": expires_at.isoformat(),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(bundle_payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).decode("ascii").rstrip("=")
    return {
        "account": config.name,
        "bridge_url": config.bridge_url,
        "profile_directory": config.chrome_profile_directory or "Default",
        "expires_at": expires_at.isoformat(),
        "pairing_bundle": PAIRING_BUNDLE_PREFIX + encoded,
    }


def decode_pairing_bundle(bundle: str) -> dict:
    if not bundle.startswith(PAIRING_BUNDLE_PREFIX):
        raise ValueError("配对包格式无效")
    encoded = bundle[len(PAIRING_BUNDLE_PREFIX) :].strip()
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("配对包无法解析") from exc
    if not isinstance(payload, dict) or payload.get("v") != PAIRING_SCHEMA_VERSION:
        raise ValueError("配对包版本不受支持")
    return payload


def get_pairing_status(account: str) -> dict:
    account = validate_account_name(account)
    config = load_account(account, allow_legacy_default=False)
    session = _read_optional_json(pairing_session_path(account))
    pending = False
    expires_at = None
    if session:
        expires_at = str(session.get("expires_at") or "")
        pending = bool(expires_at and _parse_time(expires_at) > datetime.now(UTC))
    return {
        "account": account,
        "extension_mode": config.extension_mode,
        "paired": bool(config.extension_instance_id),
        "extension_instance_id": config.extension_instance_id,
        "pairing_pending": pending,
        "pairing_expires_at": expires_at if pending else None,
    }


def consume_pairing_session(
    account: str,
    pairing_code: str,
    *,
    instance_id: str,
    extension_id: str,
    profile_directory: str,
) -> AccountConfig:
    account = validate_account_name(account)
    if not _INSTANCE_RE.fullmatch(instance_id):
        raise ValueError("扩展实例 ID 格式无效")
    if not _EXTENSION_ID_RE.fullmatch(extension_id):
        raise ValueError("Chrome 扩展 ID 格式无效")
    path = pairing_session_path(account)
    session = _read_optional_json(path)
    if not session:
        raise RuntimeError("配对会话不存在或已经使用")
    if session.get("account") != account:
        raise RuntimeError("配对会话账号不匹配")
    expires_at = _parse_time(str(session.get("expires_at") or ""))
    if expires_at <= datetime.now(UTC):
        path.unlink(missing_ok=True)
        raise RuntimeError("配对码已经过期")
    expected_hash = str(session.get("code_hash") or "")
    if not hmac.compare_digest(expected_hash, _hash_pairing_code(pairing_code)):
        raise RuntimeError("配对码不正确")

    config = load_account(account, allow_legacy_default=False)
    if config.extension_mode != UNIVERSAL_EXTENSION_MODE:
        raise RuntimeError("账号尚未迁移到通用扩展")
    expected_profile = config.chrome_profile_directory or "Default"
    if profile_directory != expected_profile:
        raise RuntimeError(
            "配对 Profile 与账号槽位不一致: "
            f"expected={expected_profile!r}, actual={profile_directory!r}"
        )

    claimed = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.claimed"
    )
    try:
        os.replace(path, claimed)
    except FileNotFoundError as exc:
        raise RuntimeError("配对会话不存在或已经使用") from exc
    try:
        rotated = revoke_extension_instance(config)
        return enroll_extension_instance(rotated, instance_id)
    finally:
        claimed.unlink(missing_ok=True)


def cancel_pairing_session(account: str) -> bool:
    path = pairing_session_path(validate_account_name(account))
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def revoke_account_pairing(account: str) -> AccountConfig:
    account = validate_account_name(account)
    cancel_pairing_session(account)
    config = load_account(account, allow_legacy_default=False)
    return revoke_extension_instance(config)


def public_binding(config: AccountConfig) -> dict:
    """Binding returned only over a successfully authenticated pairing socket."""
    return {
        "schemaVersion": PAIRING_SCHEMA_VERSION,
        "account": config.name,
        "bridgeUrl": config.bridge_url,
        "accountId": config.account_id,
        "bridgeToken": config.bridge_token,
        "profileDirectory": config.chrome_profile_directory or "Default",
        "instanceId": config.extension_instance_id,
    }


def _hash_pairing_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError("配对会话时间格式无效") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _read_optional_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"JSON 文件内容必须是对象: {path}")
    return data


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
