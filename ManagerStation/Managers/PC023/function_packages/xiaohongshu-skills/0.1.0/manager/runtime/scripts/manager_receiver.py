"""Manager-side fixed JSON receiver invoked by employee machines over SSH."""

from __future__ import annotations

# Resolve stable imports when this canonical command is launched directly.
if __name__ == "__main__":
    import sys as _layout_sys
    from pathlib import Path as _LayoutPath

    _layout_root = next(
        parent for parent in _LayoutPath(__file__).resolve().parents
        if (parent / "source-layout.json").is_file()
    )
    _layout_sys.path.insert(0, str(_layout_root / "scripts"))
    import _role_layout as _role_layout


import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

try:
    from .manager_service import ManagerService
    from .manager_store import ManagerStore
    from .service_errors import ServiceError
except ImportError:
    from manager_service import ManagerService
    from manager_store import ManagerStore
    from service_errors import ServiceError


def dispatch_request(service: ManagerService, request: dict[str, Any]) -> dict[str, Any]:
    action = str(request.get("action") or "").strip().lower()
    payload = request.get("payload") or {}
    if not isinstance(payload, dict):
        raise ServiceError("INVALID_REQUEST", "回传 payload 必须是 JSON 对象")
    if action == "agent-message":
        return service.receive_agent_message(payload)
    if action == "task-result":
        return service.receive_task_result(payload)
    raise ServiceError("UNKNOWN_ACTION", f"未知经理机接收动作：{action}")


def main(
    *,
    state_root: str | Path | None = None,
    request_file: str | Path | None = None,
    response_file: str | Path | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    if hasattr(input_stream, "reconfigure"):
        input_stream.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(output_stream, "reconfigure"):
        output_stream.reconfigure(encoding="utf-8", errors="strict")
    try:
        if request_file:
            request_path = Path(request_file)
            request_text = request_path.read_text(encoding="utf-8-sig")
            request_path.unlink(missing_ok=True)
        else:
            request_text = input_stream.readline()
        request = json.loads(request_text)
        if not isinstance(request, dict):
            raise ServiceError("INVALID_REQUEST", "回传请求必须是 JSON 对象")
        service = ManagerService(store=ManagerStore(root=state_root))
        result = dispatch_request(service, request)
    except json.JSONDecodeError:
        result = ServiceError("INVALID_JSON", "回传请求不是有效 JSON").to_dict()
    except ServiceError as exc:
        result = exc.to_dict()
    except Exception:
        result = ServiceError("INTERNAL_ERROR", "经理机接收回传失败").to_dict()
    response_text = json.dumps(result, ensure_ascii=False) + "\n"
    if response_file:
        Path(response_file).write_text(response_text, encoding="utf-8")
    output_stream.write(response_text)
    output_stream.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="接收员工Agent通过 SSH 回传的数据")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--request-file")
    parser.add_argument("--response-file")
    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    raise SystemExit(
        main(
            state_root=arguments.state_root,
            request_file=arguments.request_file,
            response_file=arguments.response_file,
        )
    )
