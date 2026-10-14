"""Read-only diagnostics for configured XHS accounts."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from account_manager import (
    UNIVERSAL_EXTENSION_MODE,
    AccountConfig,
    _is_port_available,
    accounts_root,
    load_account,
    public_config,
    validate_account_name,
)

_ROUTE_PATTERN = re.compile(r"Object\.freeze\((\{.*\})\)\s*;?", re.DOTALL)


def _check(
    name: str,
    status: str,
    message: str,
    *,
    fix: str | None = None,
) -> dict:
    result = {"name": name, "status": status, "message": message}
    if fix:
        result["fix"] = fix
    return result


def _profile_identity(config: AccountConfig) -> tuple[str, str] | None:
    if not config.chrome_user_data_dir:
        return None
    root = os.path.normcase(os.path.realpath(config.chrome_user_data_dir))
    profile = os.path.normcase(config.chrome_profile_directory or "Default")
    return root, profile


def _extension_identity(config: AccountConfig) -> str | None:
    if not config.extension_dir:
        return None
    return os.path.normcase(os.path.realpath(config.extension_dir))


def _duplicates(configs: list[AccountConfig], key: Callable) -> dict[object, list[str]]:
    grouped: dict[object, list[str]] = defaultdict(list)
    for config in configs:
        value = key(config)
        if value is not None:
            grouped[value].append(config.name)
    return {value: names for value, names in grouped.items() if len(names) > 1}


def _read_extension_route(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = _ROUTE_PATTERN.search(text)
    if not match:
        raise ValueError("bridge_config.js 格式无效")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("bridge_config.js 路由不是对象")
    return data


def _invalid_report(name: str, message: str, *, fix: str | None = None) -> dict:
    return {
        "name": name,
        "account": None,
        "healthy": False,
        "ready": False,
        "runtime": {
            "bridge_running": False,
            "extension_connected": False,
        },
        "checks": [_check("config", "fail", message, fix=fix)],
    }


def _load_configs_for_doctor() -> tuple[list[AccountConfig], list[dict]]:
    root = accounts_root()
    if not root.exists():
        return [], []

    configs: list[AccountConfig] = []
    invalid: list[dict] = []
    for path in sorted(root.glob("*/account.json")):
        name = path.parent.name
        try:
            configs.append(load_account(name, allow_legacy_default=False))
        except Exception as exc:
            invalid.append(
                _invalid_report(
                    name,
                    f"账号配置无法读取: {exc}",
                    fix=f"修复或恢复配置文件: {path}",
                )
            )
    return configs, invalid


def _diagnose_profile(
    config: AccountConfig,
    profile_duplicates: dict[object, list[str]],
) -> list[dict]:
    checks: list[dict] = []
    profile_key = _profile_identity(config)
    duplicate_profiles = profile_duplicates.get(profile_key) if profile_key else None
    if duplicate_profiles:
        checks.append(
            _check(
                "profile_unique",
                "fail",
                "同一 Chrome Profile 被多个账号绑定: "
                + ", ".join(duplicate_profiles),
                fix="保留一个绑定，其他账号改绑到独立 Profile",
            )
        )

    if not config.chrome_user_data_dir:
        checks.append(
            _check(
                "profile",
                "warning",
                "该账号使用原版 legacy 浏览器模式，没有固定 Chrome Profile",
                fix="使用 account-import 或 account-add 建立明确的 Profile 绑定",
            )
        )
        return checks

    user_data = Path(config.chrome_user_data_dir)
    profile_name = config.chrome_profile_directory or "Default"
    profile_path = user_data / profile_name
    if not user_data.is_dir():
        checks.append(
            _check(
                "profile",
                "fail",
                f"Chrome User Data 目录不存在: {user_data}",
                fix="恢复目录，或使用 account-import --replace 改绑 Profile",
            )
        )
    elif config.profile_mode == "existing":
        if not profile_path.is_dir():
            checks.append(
                _check(
                    "profile",
                    "fail",
                    f"绑定的 Chrome Profile 不存在: {profile_path}",
                    fix="使用 account-discover 查找正确目录后重新绑定",
                )
            )
        elif not (profile_path / "Preferences").is_file():
            checks.append(
                _check(
                    "profile",
                    "fail",
                    f"Profile 缺少 Preferences 文件: {profile_path}",
                    fix="确认该目录是已使用的 Chrome Profile",
                )
            )
        else:
            checks.append(
                _check(
                    "profile",
                    "pass",
                    f"已有 Chrome Profile 可用: {profile_path}",
                )
            )
    else:
        checks.append(
            _check(
                "profile",
                "pass",
                f"独立 Chrome User Data 目录可用: {user_data}",
            )
        )
        if not profile_path.exists():
            checks.append(
                _check(
                    "profile_initialized",
                    "info",
                    "独立 Profile 尚未生成 Default 子目录，首次手动打开 Chrome 后会创建",
                )
            )

    if profile_key and not duplicate_profiles:
        checks.append(
            _check("profile_unique", "pass", "Chrome Profile 未被其他账号重复绑定")
        )
    return checks


def _diagnose_extension(
    config: AccountConfig,
    extension_duplicates: dict[object, list[str]],
) -> list[dict]:
    checks: list[dict] = []
    extension_key = _extension_identity(config)
    duplicate_extensions = (
        extension_duplicates.get(extension_key) if extension_key else None
    )
    universal = config.extension_mode == UNIVERSAL_EXTENSION_MODE
    if duplicate_extensions and not universal:
        checks.append(
            _check(
                "extension_unique",
                "fail",
                "旧版账号专属扩展目录被多个账号配置使用: "
                + ", ".join(duplicate_extensions),
                fix="运行 account-sync 迁移到通用扩展，或恢复独立旧版扩展目录",
            )
        )
    elif duplicate_extensions and universal:
        checks.append(
            _check(
                "extension_shared",
                "pass",
                "多个账号正确共用通用扩展目录: " + ", ".join(duplicate_extensions),
            )
        )

    if not config.extension_dir:
        checks.append(
            _check(
                "extension",
                "warning",
                "该账号没有扩展运行目录",
                fix="运行 account-sync 部署通用扩展",
            )
        )
        return checks

    extension_dir = Path(config.extension_dir)
    manifest = extension_dir / "manifest.json"
    route_path = extension_dir / "bridge_config.js"
    if not extension_dir.is_dir():
        checks.append(
            _check(
                "extension",
                "fail",
                f"扩展目录不存在: {extension_dir}",
                fix="运行 account-sync 恢复通用扩展代码",
            )
        )
    elif not manifest.is_file():
        checks.append(
            _check(
                "extension",
                "fail",
                f"扩展目录缺少 manifest.json: {extension_dir}",
                fix="运行 account-sync 恢复完整扩展代码",
            )
        )
    else:
        checks.append(_check("extension", "pass", f"扩展目录结构可用: {extension_dir}"))

    if extension_dir.is_dir():
        if not route_path.is_file():
            checks.append(
                _check(
                    "extension_route",
                    "fail",
                    f"扩展缺少配置文件: {route_path}",
                    fix="运行 account-sync 重新生成通用扩展配置",
                )
            )
        else:
            try:
                route = _read_extension_route(route_path)
                if universal:
                    valid = (
                        route.get("mode") == UNIVERSAL_EXTENSION_MODE
                        and route.get("storageKey") == "xhsBridgeBinding"
                        and not route.get("accountId")
                        and not route.get("bridgeToken")
                    )
                    if not valid:
                        checks.append(
                            _check(
                                "extension_route",
                                "fail",
                                "通用扩展配置无效或仍含账号长期凭据",
                                fix=(
                                    "运行 python scripts/cli.py --account "
                                    f"{config.name} account-sync"
                                ),
                            )
                        )
                    else:
                        checks.append(
                            _check(
                                "extension_route",
                                "pass",
                                "扩展为通用配对模式，磁盘配置不含账号长期凭据",
                            )
                        )
                else:
                    expected_url = config.bridge_url
                    identity_enabled = bool(config.account_id and config.bridge_token)
                    valid = (
                        route.get("account") == config.name
                        and route.get("bridgeUrl") == expected_url
                        and (
                            not identity_enabled
                            or (
                                route.get("accountId") == config.account_id
                                and route.get("bridgeToken") == config.bridge_token
                                and route.get("profileDirectory")
                                == (config.chrome_profile_directory or "Default")
                            )
                        )
                    )
                    if not valid:
                        checks.append(
                            _check(
                                "extension_route",
                                "fail",
                                "旧版扩展路由与账号配置不一致",
                                fix=(
                                    "运行 python scripts/cli.py --account "
                                    f"{config.name} account-sync"
                                ),
                            )
                        )
                    else:
                        checks.append(
                            _check(
                                "extension_route",
                                "pass",
                                f"旧版扩展正确路由到 {config.name} / {expected_url}",
                            )
                        )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                checks.append(
                    _check(
                        "extension_route",
                        "fail",
                        f"无法读取扩展配置: {exc}",
                        fix=f"运行 python scripts/cli.py --account {config.name} account-sync",
                    )
                )

    if extension_key and not duplicate_extensions:
        checks.append(_check("extension_unique", "pass", "扩展目录未被错误复用"))
    if config.account_id and config.bridge_token:
        checks.append(_check("connection_credentials", "pass", "账号连接 ID 和令牌已配置"))
    else:
        checks.append(
            _check(
                "connection_credentials",
                "warning",
                "该账号仍使用旧版无身份认证连接",
                fix=f"运行 python scripts/cli.py --account {config.name} account-sync",
            )
        )
    if universal:
        if config.extension_instance_id:
            checks.append(_check("pairing", "pass", "账号已登记通用扩展实例"))
        else:
            checks.append(
                _check(
                    "pairing",
                    "warning",
                    "账号尚未与通用扩展实例配对",
                    fix=(
                        "运行 python scripts/cli.py --account "
                        f"{config.name} account-pair-begin --confirm"
                    ),
                )
            )
    return checks

def _diagnose_runtime(config: AccountConfig, page_factory: Callable) -> tuple[list[dict], dict]:
    from account_runtime import evaluate_profile_connection

    checks: list[dict] = []
    bridge_running = False
    extension_connected = False
    bridge_status: dict | None = None
    try:
        page = page_factory(config.bridge_url, account=config.name)
        if hasattr(page, "get_server_status"):
            bridge_status = page.get_server_status()
            bridge_running = bridge_status is not None
            extension_connected = bool(
                bridge_status and bridge_status.get("extension_connected")
            )
        else:
            bridge_running = bool(page.is_server_running())
            extension_connected = (
                bool(page.is_extension_connected()) if bridge_running else False
            )
            if bridge_running:
                bridge_status = {
                    "extension_connected": extension_connected,
                    "extension": {
                        "profile_directory": config.chrome_profile_directory
                        or "Default"
                    },
                }
    except Exception as exc:
        checks.append(
            _check(
                "runtime_probe",
                "warning",
                f"运行状态探测失败: {exc}",
                fix="检查本机网络栈和 Bridge 日志后重试",
            )
        )

    if bridge_running:
        checks.append(
            _check(
                "bridge_running",
                "pass",
                f"Bridge 正在运行: {config.bridge_url}",
            )
        )
    elif _is_port_available(config.bridge_port):
        checks.append(
            _check(
                "bridge_running",
                "warning",
                f"Bridge 尚未启动: {config.bridge_url}",
                fix=f"运行 python scripts/cli.py --account {config.name} account-start",
            )
        )
    else:
        checks.append(
            _check(
                "bridge_running",
                "fail",
                f"端口 {config.bridge_port} 已被占用，但不是匹配账号的 Bridge",
                fix="停止占用端口的进程，或为该账号重新分配端口",
            )
        )

    if extension_connected:
        checks.append(_check("extension_connected", "pass", "目标 Profile 的扩展已连接"))
    else:
        checks.append(
            _check(
                "extension_connected",
                "warning",
                "目标 Profile 的扩展当前未连接",
                fix=(
                    "启动目标 Chrome Profile，确认已加载通用扩展并完成该账号配对"
                ),
            )
        )

    extension_info = (bridge_status or {}).get("extension") or {}
    profile_runtime = evaluate_profile_connection(config, bridge_status)
    if (
        extension_connected
        and profile_runtime["profile_verification_level"] == "paired_instance"
    ):
        checks.append(
            _check(
                "connected_profile",
                "pass",
                "当前连接来自该槽位已登记的 Chrome Profile 扩展实例",
            )
        )
    elif (
        extension_connected
        and profile_runtime["profile_verification_level"] == "legacy_claim"
    ):
        checks.append(
            _check(
                "connected_profile",
                "warning",
                "旧版扩展的 Profile 声明与槽位一致，但尚无配对实例强校验",
                fix="通过 WebUI 引导该 Profile 加载通用扩展并重新安全配对",
            )
        )
    elif extension_connected:
        checks.append(
            _check(
                "connected_profile",
                "fail",
                "当前连接扩展的 Chrome Profile 与槽位配置不一致: "
                f"expected={profile_runtime['expected_profile_directory']!r}, "
                f"actual={profile_runtime['connected_profile_directory']!r}",
                fix="停止当前 Bridge，重新启动槽位指定 Profile 后再检查",
            )
        )
    else:
        checks.append(
            _check(
                "connected_profile",
                "warning",
                "扩展未连接，暂时无法核验实际 Chrome Profile",
            )
        )
    identity_verified = bool(
        extension_connected
        and config.account_id
        and bridge_status
        and bridge_status.get("account_id") == config.account_id
        and extension_info.get("identity_verified")
    )
    instance_enrolled = bool(
        identity_verified
        and config.extension_instance_id
        and extension_info.get("instance_id") == config.extension_instance_id
        and extension_info.get("instance_enrolled")
    )
    if instance_enrolled:
        checks.append(
            _check(
                "connected_profile_identity",
                "pass",
                "当前连接来自该账号已登记的 Profile 扩展实例",
            )
        )
    elif extension_connected and identity_verified:
        checks.append(
            _check(
                "connected_profile_identity",
                "warning",
                "连接凭据验证通过，但扩展安装实例尚未登记",
                fix=f"运行 python scripts/cli.py --account {config.name} "
                "account-pair-begin --confirm 重新配对",
            )
        )
    elif extension_connected:
        checks.append(
            _check(
                "connected_profile_identity",
                "warning",
                "扩展已连接，但 Bridge 未完成连接身份认证",
                fix="同步扩展、重新加载扩展并重启目标账号 Bridge",
            )
        )
    else:
        checks.append(
            _check(
                "connected_profile_identity",
                "warning",
                "扩展未连接，暂时无法核验 Profile 配对实例",
            )
        )
    return checks, {
        "bridge_running": bridge_running,
        "extension_connected": extension_connected,
        "expected_profile_directory": profile_runtime[
            "expected_profile_directory"
        ],
        "connected_profile_directory": profile_runtime[
            "connected_profile_directory"
        ],
        "profile_verified": profile_runtime["profile_verified"],
        "profile_verification_level": profile_runtime[
            "profile_verification_level"
        ],
        "connection_identity_verified": identity_verified,
        "extension_instance_enrolled": instance_enrolled,
    }


def _diagnose_account(
    config: AccountConfig,
    *,
    port_duplicates: dict[object, list[str]],
    profile_duplicates: dict[object, list[str]],
    extension_duplicates: dict[object, list[str]],
    page_factory: Callable,
) -> dict:
    checks: list[dict] = [
        _check("config", "pass", "账号配置可读取，字段格式有效")
    ]
    duplicate_ports = port_duplicates.get(config.bridge_port)
    if duplicate_ports:
        checks.append(
            _check(
                "bridge_port_unique",
                "fail",
                f"Bridge 端口 {config.bridge_port} 被多个账号使用: "
                + ", ".join(duplicate_ports),
                fix="为冲突账号重新分配独立 Bridge 端口",
            )
        )
    else:
        checks.append(
            _check(
                "bridge_port_unique",
                "pass",
                f"Bridge 端口 {config.bridge_port} 未被其他账号配置占用",
            )
        )

    checks.extend(_diagnose_profile(config, profile_duplicates))
    checks.extend(_diagnose_extension(config, extension_duplicates))
    runtime_checks, runtime = _diagnose_runtime(config, page_factory)
    checks.extend(runtime_checks)
    healthy = not any(item["status"] == "fail" for item in checks)
    ready = (
        healthy
        and runtime["bridge_running"]
        and runtime["extension_connected"]
        and runtime["profile_verified"]
    )
    return {
        "name": config.name,
        "account": public_config(config),
        "healthy": healthy,
        "ready": ready,
        "runtime": runtime,
        "checks": checks,
    }


def diagnose_accounts(
    name: str | None = None,
    *,
    page_factory: Callable | None = None,
) -> dict:
    """Diagnose one or all configured accounts without changing local state."""
    if page_factory is None:
        from xhs.bridge import BridgePage

        page_factory = BridgePage

    configs, invalid = _load_configs_for_doctor()
    port_duplicates = _duplicates(configs, lambda item: item.bridge_port)
    profile_duplicates = _duplicates(configs, _profile_identity)
    extension_duplicates = _duplicates(configs, _extension_identity)
    top_checks: list[dict] = []

    if name:
        try:
            normalized_name = validate_account_name(name)
        except ValueError as exc:
            reports = [_invalid_report(name, str(exc))]
        else:
            selected = [item for item in configs if item.name == normalized_name]
            selected_invalid = [item for item in invalid if item["name"] == normalized_name]
            if selected:
                reports = [
                    _diagnose_account(
                        selected[0],
                        port_duplicates=port_duplicates,
                        profile_duplicates=profile_duplicates,
                        extension_duplicates=extension_duplicates,
                        page_factory=page_factory,
                    )
                ]
            elif selected_invalid:
                reports = selected_invalid
            else:
                reports = [
                    _invalid_report(
                        normalized_name,
                        f"账号 {normalized_name!r} 尚未配置",
                        fix=f"运行 account-add --name {normalized_name}，"
                        "或使用 account-import 绑定已有 Profile",
                    )
                ]
    else:
        reports = [
            _diagnose_account(
                config,
                port_duplicates=port_duplicates,
                profile_duplicates=profile_duplicates,
                extension_duplicates=extension_duplicates,
                page_factory=page_factory,
            )
            for config in configs
        ]
        reports.extend(invalid)
        if not reports:
            top_checks.append(
                _check(
                    "accounts",
                    "fail",
                    "尚未配置任何多账号环境",
                    fix="运行 account-add，或使用 account-import 绑定已有 Profile",
                )
            )

    all_checks = top_checks + [
        check for report in reports for check in report["checks"]
    ]
    errors = sum(check["status"] == "fail" for check in all_checks)
    warnings = sum(check["status"] == "warning" for check in all_checks)
    info = sum(check["status"] == "info" for check in all_checks)
    healthy_count = sum(report["healthy"] for report in reports)
    ready_count = sum(report["ready"] for report in reports)
    return {
        "success": True,
        "healthy": bool(reports) and errors == 0,
        "ready": bool(reports) and ready_count == len(reports),
        "summary": {
            "total_accounts": len(reports),
            "healthy_accounts": healthy_count,
            "ready_accounts": ready_count,
            "errors": errors,
            "warnings": warnings,
            "info": info,
        },
        "checks": top_checks,
        "accounts": reports,
    }
