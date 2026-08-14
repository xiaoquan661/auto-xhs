"""Multi-account runtime configuration for XHS browser automation."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_ACCOUNT = "default"
DEFAULT_BRIDGE_PORT = 9333
ACCOUNT_SCHEMA_VERSION = 2
UNIVERSAL_EXTENSION_MODE = "universal"
LEGACY_EXTENSION_MODE = "per_account"
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_ACCOUNT_REGISTRY_PROCESS_LOCK = threading.RLock()


@dataclass(frozen=True)
class AccountConfig:
    name: str
    bridge_port: int
    chrome_user_data_dir: str | None
    extension_dir: str | None
    chrome_profile_directory: str | None = None
    profile_mode: str = "managed"
    account_id: str | None = None
    bridge_token: str | None = None
    extension_instance_id: str | None = None
    schema_version: int = ACCOUNT_SCHEMA_VERSION
    extension_mode: str = UNIVERSAL_EXTENSION_MODE

    @property
    def bridge_url(self) -> str:
        return f"ws://localhost:{self.bridge_port}"


def accounts_root() -> Path:
    override = os.getenv("XHS_ACCOUNTS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".xhs" / "accounts"


def universal_extension_dir(extension_source: str | Path | None = None) -> Path:
    override = os.getenv("XHS_UNIVERSAL_EXTENSION_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if extension_source is not None:
        return Path(extension_source).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "extension"


def validate_account_name(name: str) -> str:
    if not _ACCOUNT_RE.fullmatch(name):
        raise ValueError(
            "账号名称只能包含英文字母、数字、下划线或连字符，长度 1-32，且必须以字母或数字开头"
        )
    return name


def account_dir(name: str) -> Path:
    return accounts_root() / validate_account_name(name)


def account_config_path(name: str) -> Path:
    return account_dir(name) / "account.json"


def list_accounts() -> list[AccountConfig]:
    root = accounts_root()
    if not root.exists():
        return []
    configs: list[AccountConfig] = []
    for path in sorted(root.glob("*/account.json")):
        try:
            configs.append(_read_config(path))
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return configs


def default_chrome_user_data_dir() -> Path:
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if not local_app_data:
            raise FileNotFoundError("LOCALAPPDATA 未设置，无法定位 Chrome User Data")
        return Path(local_app_data) / "Google" / "Chrome" / "User Data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def discover_chrome_profiles(user_data_dir: str | Path | None = None) -> list[dict]:
    """List existing Chrome profiles without reading cookies or credentials."""
    root = (
        Path(user_data_dir).expanduser().resolve()
        if user_data_dir
        else default_chrome_user_data_dir().resolve()
    )
    if not root.is_dir():
        raise FileNotFoundError(f"Chrome User Data 目录不存在: {root}")

    info_cache: dict = {}
    local_state = root / "Local State"
    if local_state.is_file():
        try:
            state = json.loads(local_state.read_text(encoding="utf-8"))
            info_cache = state.get("profile", {}).get("info_cache", {}) or {}
        except (OSError, json.JSONDecodeError):
            info_cache = {}

    profile_names = set(info_cache)
    for child in root.iterdir():
        if child.is_dir() and (
            child.name == "Default" or child.name.startswith("Profile ")
        ):
            profile_names.add(child.name)

    bound_profiles = {
        _profile_identity(
            config.chrome_user_data_dir,
            config.chrome_profile_directory or "Default",
        ): config.name
        for config in list_accounts()
        if config.chrome_user_data_dir
    }
    profiles: list[dict] = []
    for profile_name in sorted(profile_names, key=_profile_sort_key):
        profile_path = root / profile_name
        if not (profile_path / "Preferences").is_file():
            continue
        info = info_cache.get(profile_name, {})
        identity = _profile_identity(str(root), profile_name)
        profiles.append(
            {
                "profile_directory": profile_name,
                "display_name": info.get("name") or profile_name,
                "profile_path": str(profile_path),
                "bound_account": bound_profiles.get(identity),
            }
        )
    return profiles


def load_account(name: str, *, allow_legacy_default: bool = True) -> AccountConfig:
    validate_account_name(name)
    path = account_config_path(name)
    if path.exists():
        return _read_config(path)
    if name == DEFAULT_ACCOUNT and allow_legacy_default:
        return AccountConfig(
            name=DEFAULT_ACCOUNT,
            bridge_port=DEFAULT_BRIDGE_PORT,
            chrome_user_data_dir=None,
            extension_dir=None,
            profile_mode="legacy",
            schema_version=1,
            extension_mode=LEGACY_EXTENSION_MODE,
        )
    raise FileNotFoundError(
        f"账号 {name!r} 尚未配置，请先运行 account-add --name {name}"
    )


def add_account(
    name: str,
    *,
    bridge_port: int | None = None,
    extension_source: str | Path,
) -> AccountConfig:
    with account_registry_transaction():
        return _add_account_unlocked(
            name,
            bridge_port=bridge_port,
            extension_source=extension_source,
        )


def _add_account_unlocked(
    name: str,
    *,
    bridge_port: int | None,
    extension_source: str | Path,
) -> AccountConfig:
    name = validate_account_name(name)
    path = account_config_path(name)
    if path.exists():
        raise FileExistsError(f"账号 {name!r} 已存在")

    port = _select_new_account_port(bridge_port)
    extension_dir = sync_universal_extension(extension_source)
    target_dir = account_dir(name)
    profile_dir = target_dir / "chrome-profile"
    target_dir.mkdir(parents=True, exist_ok=False)
    try:
        profile_dir.mkdir()
        config = AccountConfig(
            name=name,
            bridge_port=port,
            chrome_user_data_dir=str(profile_dir),
            extension_dir=str(extension_dir),
            profile_mode="managed",
            account_id=uuid.uuid4().hex,
            bridge_token=secrets.token_urlsafe(32),
        )
        _write_config(config)
        return config
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise


def import_existing_profile(
    name: str,
    *,
    user_data_dir: str | Path,
    profile_directory: str,
    bridge_port: int | None = None,
    extension_source: str | Path,
    replace: bool = False,
) -> AccountConfig:
    """Bind an existing Chrome profile without copying or modifying it."""
    with account_registry_transaction():
        return _import_existing_profile_unlocked(
            name,
            user_data_dir=user_data_dir,
            profile_directory=profile_directory,
            bridge_port=bridge_port,
            extension_source=extension_source,
            replace=replace,
        )


def _import_existing_profile_unlocked(
    name: str,
    *,
    user_data_dir: str | Path,
    profile_directory: str,
    bridge_port: int | None,
    extension_source: str | Path,
    replace: bool,
) -> AccountConfig:
    name = validate_account_name(name)
    path = account_config_path(name)
    current = _read_config(path) if path.exists() else None
    if current and not replace:
        raise FileExistsError(f"账号 {name!r} 已存在")

    root, profile_name, profile_path = _validate_existing_profile(
        user_data_dir, profile_directory
    )
    identity = _profile_identity(str(root), profile_name)
    for existing in list_accounts():
        if existing.name == name:
            continue
        if _profile_identity(
            existing.chrome_user_data_dir,
            existing.chrome_profile_directory or "Default",
        ) == identity:
            raise ValueError(
                f"Chrome Profile 已绑定到账号 {existing.name!r}: {profile_path}"
            )

    port = _select_new_account_port(
        bridge_port,
        exclude_account=name if current else None,
        current_port=current.bridge_port if current else None,
    )
    extension_dir = sync_universal_extension(extension_source)
    target_dir = account_dir(name)
    target_existed = target_dir.exists()
    target_dir.mkdir(parents=True, exist_ok=True)
    config = AccountConfig(
        name=name,
        bridge_port=port,
        chrome_user_data_dir=str(root),
        extension_dir=str(extension_dir),
        chrome_profile_directory=profile_name,
        profile_mode="existing",
        account_id=current.account_id if current and current.account_id else uuid.uuid4().hex,
        bridge_token=(
            current.bridge_token
            if current and current.bridge_token
            else secrets.token_urlsafe(32)
        ),
        # A Profile rebind must be paired again; an instance belongs to the old Profile.
        extension_instance_id=None,
    )
    try:
        if current:
            shutil.copy2(path, path.with_name("account.previous.json"))
        _write_config(config)
        return config
    except Exception:
        if not target_existed:
            shutil.rmtree(target_dir, ignore_errors=True)
        raise


@contextmanager
def account_registry_transaction(timeout: float = 30.0):
    """Serialize slot metadata changes across threads and local processes."""
    from run_lock import RunLock

    lock_path = accounts_root() / ".registry.lock"
    with _ACCOUNT_REGISTRY_PROCESS_LOCK:
        lock = RunLock(str(lock_path))
        if not lock.acquire(timeout=timeout):
            raise TimeoutError("账号配置正在被其他进程修改")
        try:
            yield
        finally:
            lock.release()


def sync_universal_extension(extension_source: str | Path) -> Path:
    """Use the workspace extension, unless an explicit deployment target is set."""
    source = Path(extension_source).resolve()
    if not (source / "manifest.json").exists():
        raise FileNotFoundError(f"扩展源码目录无效: {source}")
    target = universal_extension_dir(source)
    if target == source:
        if not _has_universal_extension_config(source):
            _write_universal_extension_config(source)
        return source
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True)
    _write_universal_extension_config(target)
    return target


def _has_universal_extension_config(extension_dir: Path) -> bool:
    try:
        text = (extension_dir / "bridge_config.js").read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(
        re.search(r'''["']?mode["']?\s*:\s*["']universal["']''', text)
        and re.search(
            r'''["']?storageKey["']?\s*:\s*["']xhsBridgeBinding["']''',
            text,
        )
    )


def sync_account_extension(
    config: AccountConfig,
    *,
    extension_source: str | Path,
) -> Path:
    """Migrate an account to the shared universal extension deployment."""
    target = sync_universal_extension(extension_source)
    updated = AccountConfig(
        **{
            **asdict(config),
            "schema_version": ACCOUNT_SCHEMA_VERSION,
            "extension_mode": UNIVERSAL_EXTENSION_MODE,
            "extension_dir": str(target),
            "account_id": config.account_id or uuid.uuid4().hex,
            "bridge_token": config.bridge_token or secrets.token_urlsafe(32),
            "extension_instance_id": (
                config.extension_instance_id
                if config.extension_mode == UNIVERSAL_EXTENSION_MODE
                else None
            ),
        }
    )
    _write_config(updated)
    return target


def initialize_connection_identity(config: AccountConfig) -> AccountConfig:
    """Create missing Bridge credentials without exposing them in extension files."""
    updated = AccountConfig(
        **{
            **asdict(config),
            "account_id": config.account_id or uuid.uuid4().hex,
            "bridge_token": config.bridge_token or secrets.token_urlsafe(32),
        }
    )
    _write_config(updated)
    if (
        updated.extension_mode == LEGACY_EXTENSION_MODE
        and updated.extension_dir
    ):
        _write_extension_config(Path(updated.extension_dir), updated)
    return updated


def enroll_extension_instance(config: AccountConfig, instance_id: str) -> AccountConfig:
    """Bind an account slot to one observed extension installation instance."""
    normalized = instance_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", normalized):
        raise ValueError("扩展实例 ID 格式无效")
    updated = AccountConfig(**{**asdict(config), "extension_instance_id": normalized})
    _write_config(updated)
    return updated


def revoke_extension_instance(config: AccountConfig) -> AccountConfig:
    """Revoke the paired extension and rotate the long-lived Bridge token."""
    updated = AccountConfig(
        **{
            **asdict(config),
            "bridge_token": secrets.token_urlsafe(32),
            "extension_instance_id": None,
        }
    )
    _write_config(updated)
    return updated

def next_available_port(start: int = DEFAULT_BRIDGE_PORT) -> int:
    used_ports = {config.bridge_port for config in list_accounts()}
    port = start
    while port in used_ports or not _is_port_available(port):
        port += 1
        if port > 65535:
            raise RuntimeError("没有可用的 Bridge 端口")
    return port


def _select_new_account_port(
    bridge_port: int | None,
    *,
    exclude_account: str | None = None,
    current_port: int | None = None,
) -> int:
    if bridge_port is None:
        if current_port is not None:
            return current_port
        return next_available_port()

    _validate_port(bridge_port)
    used_ports = {
        config.bridge_port
        for config in list_accounts()
        if config.name != exclude_account
    }
    if bridge_port in used_ports:
        raise ValueError(f"Bridge 端口 {bridge_port} 已被其他账号占用")
    if bridge_port != current_port and not _is_port_available(bridge_port):
        raise ValueError(f"Bridge 端口 {bridge_port} 已被其他进程占用")
    return bridge_port


def account_lock_path(name: str) -> str:
    return str(account_dir(name) / "run.lock")


def public_config(config: AccountConfig) -> dict:
    result = {
        "name": config.name,
        "bridge_port": config.bridge_port,
        "bridge_url": config.bridge_url,
        "schema_version": config.schema_version,
        "profile_mode": config.profile_mode,
        "extension_mode": config.extension_mode,
        "chrome_user_data_dir": config.chrome_user_data_dir,
        "chrome_profile_directory": config.chrome_profile_directory,
        "extension_dir": config.extension_dir,
        "account_id": config.account_id,
        "connection_identity_enabled": bool(config.account_id and config.bridge_token),
        "extension_instance_enrolled": bool(config.extension_instance_id),
    }
    if config.chrome_user_data_dir:
        profile_name = config.chrome_profile_directory or "Default"
        result["chrome_profile_path"] = str(
            Path(config.chrome_user_data_dir) / profile_name
        )
    return result


def _read_config(path: Path) -> AccountConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    name = validate_account_name(str(data["name"]))
    port = int(data["bridge_port"])
    _validate_port(port)
    return AccountConfig(
        name=name,
        bridge_port=port,
        chrome_user_data_dir=data.get("chrome_user_data_dir"),
        extension_dir=data.get("extension_dir"),
        chrome_profile_directory=data.get("chrome_profile_directory"),
        profile_mode=data.get(
            "profile_mode",
            "existing" if data.get("chrome_profile_directory") else "managed",
        ),
        account_id=data.get("account_id"),
        bridge_token=data.get("bridge_token"),
        extension_instance_id=data.get("extension_instance_id"),
        schema_version=int(data.get("schema_version", 1)),
        extension_mode=data.get("extension_mode", LEGACY_EXTENSION_MODE),
    )


def _validate_existing_profile(
    user_data_dir: str | Path, profile_directory: str
) -> tuple[Path, str, Path]:
    root = Path(user_data_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Chrome User Data 目录不存在: {root}")

    profile_name = profile_directory.strip()
    if (
        not profile_name
        or profile_name in {".", ".."}
        or Path(profile_name).name != profile_name
        or "/" in profile_name
        or "\\" in profile_name
    ):
        raise ValueError("Profile 目录必须是单个目录名，例如 Default 或 Profile 2")

    profile_path = (root / profile_name).resolve()
    if profile_path.parent != root:
        raise ValueError("Profile 目录不能超出 Chrome User Data 根目录")
    if not profile_path.is_dir():
        raise FileNotFoundError(f"Chrome Profile 目录不存在: {profile_path}")
    if not (profile_path / "Preferences").is_file():
        raise ValueError(f"目录看起来不是已使用的 Chrome Profile: {profile_path}")
    return root, profile_name, profile_path


def _profile_identity(
    user_data_dir: str | None, profile_directory: str
) -> tuple[str, str] | None:
    if not user_data_dir:
        return None
    root = os.path.normcase(os.path.realpath(os.path.expanduser(user_data_dir)))
    return root, os.path.normcase(profile_directory)


def _profile_sort_key(profile_name: str) -> tuple[int, int | str]:
    if profile_name == "Default":
        return 0, 0
    match = re.fullmatch(r"Profile (\d+)", profile_name)
    if match:
        return 1, int(match.group(1))
    return 2, profile_name


def _write_extension_config(extension_dir: Path, config: AccountConfig) -> None:
    payload = json.dumps(
        {
            "account": config.name,
            "bridgeUrl": config.bridge_url,
            "accountId": config.account_id,
            "bridgeToken": config.bridge_token,
            "profileDirectory": config.chrome_profile_directory or "Default",
        },
        ensure_ascii=False,
    )
    (extension_dir / "bridge_config.js").write_text(
        f"globalThis.XHS_BRIDGE_CONFIG = Object.freeze({payload});\n",
        encoding="utf-8",
    )


def _write_universal_extension_config(extension_dir: Path) -> None:
    payload = json.dumps(
        {
            "mode": UNIVERSAL_EXTENSION_MODE,
            "storageKey": "xhsBridgeBinding",
        },
        ensure_ascii=False,
    )
    (extension_dir / "bridge_config.js").write_text(
        f"globalThis.XHS_BRIDGE_CONFIG = Object.freeze({payload});\n",
        encoding="utf-8",
    )


def _write_config(config: AccountConfig) -> None:
    path = account_config_path(config.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _validate_port(port: int) -> None:
    if not 1024 <= port <= 65535:
        raise ValueError("Bridge 端口必须在 1024-65535 之间")


def _is_port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True
