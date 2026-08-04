"""XHS Extension Bridge Server

Extension 连接到这里（WebSocket），CLI 命令通过同一端口发送（role=cli），
Bridge 将命令路由给 Extension 并把结果返回给 CLI。

启动方式：
    python scripts/bridge_server.py

端口：9333（可通过 --port 覆盖）
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import sys
import uuid
from typing import Any

import websockets
from websockets.server import ServerConnection

logger = logging.getLogger("xhs-bridge")


class BridgeServer:
    def __init__(
        self,
        account: str = "default",
        *,
        account_id: str | None = None,
        bridge_token: str | None = None,
        extension_instance_id: str | None = None,
    ) -> None:
        self.account = account
        self.account_id = account_id or None
        self.bridge_token = bridge_token or None
        self.extension_instance_id = extension_instance_id or None
        self._extension_ws: ServerConnection | None = None
        self._extension_info: dict[str, Any] | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._shutdown_event = asyncio.Event()

    async def handle(self, ws: ServerConnection) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
        except (TimeoutError, Exception) as e:
            logger.warning("握手超时或失败: %s", e)
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        role = msg.get("role")
        requested_account = msg.get("account", "default")
        if requested_account != self.account:
            await ws.send(json.dumps({
                "error": (
                    f"账号路由不匹配: bridge={self.account}, "
                    f"client={requested_account}"
                )
            }, ensure_ascii=False))
            return
        if role == "pairing":
            await self._handle_pairing(ws, msg)
        elif role == "extension":
            error = self._validate_extension_handshake(msg)
            if error:
                logger.warning("拒绝 Extension 连接: %s", error)
                await ws.send(json.dumps({"error": error}, ensure_ascii=False))
                await ws.close(code=4003, reason="Extension 身份校验失败")
                return
            await ws.send(
                json.dumps(
                    {
                        "type": "handshake_ack",
                        "account": self.account,
                        "identity_verified": bool(
                            self.account_id and self.bridge_token
                        ),
                    }
                )
            )
            await self._handle_extension(ws, msg)
        elif role == "cli":
            error = self._validate_cli_request(msg)
            if error:
                await ws.send(json.dumps({"error": error}, ensure_ascii=False))
                return
            await self._handle_cli(ws, msg)
        else:
            logger.warning("未知 role: %s", role)

    # ─── 配对与身份验证 ───────────────────────────────────────────────

    async def _handle_pairing(self, ws: ServerConnection, msg: dict) -> None:
        from account_pairing import consume_pairing_session, public_binding

        try:
            updated = consume_pairing_session(
                self.account,
                str(msg.get("pairing_code") or ""),
                instance_id=str(msg.get("instance_id") or ""),
                extension_id=str(msg.get("extension_id") or ""),
                profile_directory=str(msg.get("profile_directory") or ""),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            await ws.send(
                json.dumps(
                    {"type": "pairing_error", "error": str(exc)},
                    ensure_ascii=False,
                )
            )
            return

        old_extension = self._extension_ws
        self._extension_ws = None
        self._extension_info = None
        self.account_id = updated.account_id
        self.bridge_token = updated.bridge_token
        self.extension_instance_id = updated.extension_instance_id
        if old_extension is not None:
            await old_extension.close(code=4002, reason="账号扩展已重新配对")
        await ws.send(
            json.dumps(
                {
                    "type": "pairing_ack",
                    "binding": public_binding(updated),
                },
                ensure_ascii=False,
            )
        )

    def _validate_cli_request(self, msg: dict) -> str | None:
        if msg.get("method") == "ping_server":
            return None
        params = msg.get("params") or {}
        received_account_id = msg.get("account_id") or params.get("account_id")
        if self.account_id and received_account_id != self.account_id:
            return "CLI 账号连接 ID 不匹配"
        received_token = str(
            msg.get("bridge_token") or params.get("bridge_token") or ""
        )
        if self.bridge_token and not hmac.compare_digest(received_token, self.bridge_token):
            return "CLI Bridge 连接令牌不匹配"
        return None

    # ─── Extension 端（长连接） ───────────────────────────────────────

    def _validate_extension_handshake(self, msg: dict) -> str | None:
        if self.account_id and msg.get("account_id") != self.account_id:
            return "账号连接 ID 不匹配"
        received_token = str(msg.get("bridge_token") or "")
        if self.bridge_token and not hmac.compare_digest(received_token, self.bridge_token):
            return "Bridge 连接令牌不匹配"
        if (
            self.extension_instance_id
            and msg.get("instance_id") != self.extension_instance_id
        ):
            return "扩展实例未登记到该账号槽位"
        return None

    async def _handle_extension(self, ws: ServerConnection, handshake: dict) -> None:
        if self._extension_ws is not None:
            logger.warning("拒绝账号 %s 的重复 Extension 连接", self.account)
            await ws.close(code=4001, reason="该账号已有 Extension 连接")
            return
        logger.info("Extension 已连接: account=%s", self.account)
        self._extension_ws = ws
        self._extension_info = {
            "account_id": handshake.get("account_id"),
            "instance_id": handshake.get("instance_id"),
            "extension_id": handshake.get("extension_id"),
            "profile_directory": handshake.get("profile_directory"),
            "identity_verified": bool(self.account_id and self.bridge_token),
            "instance_enrolled": bool(
                self.extension_instance_id
                and handshake.get("instance_id") == self.extension_instance_id
            ),
        }
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                msg_id = msg.get("id")
                if msg_id and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if not future.done():
                        future.set_result(msg)
        finally:
            if self._extension_ws is ws:
                self._extension_ws = None
                self._extension_info = None
                logger.info("Extension 已断开: account=%s", self.account)
                # 唤醒所有等待中的 CLI 请求并报错
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("Extension 断开连接"))
                self._pending.clear()

    # ─── CLI 端（短连接，发一条命令，收一条回复） ─────────────────────

    async def _handle_cli(self, ws: ServerConnection, msg: dict) -> None:
        # 特殊命令：查询 server/extension 状态，无需转发
        if msg.get("method") == "ping_server":
            await ws.send(json.dumps({
                "result": {
                    "extension_connected": self._extension_ws is not None,
                    "account": self.account,
                    "account_id": self.account_id,
                    "connection_identity_required": bool(
                        self.account_id and self.bridge_token
                    ),
                    "extension": self._extension_info,
                }
            }))
            return

        if msg.get("method") == "shutdown_server":
            params = msg.get("params") or {}
            if self.account_id and params.get("account_id") != self.account_id:
                await ws.send(json.dumps({"error": "账号连接 ID 不匹配"}))
                return
            received_token = str(params.get("bridge_token") or "")
            if self.bridge_token and not hmac.compare_digest(
                received_token, self.bridge_token
            ):
                await ws.send(json.dumps({"error": "Bridge 连接令牌不匹配"}))
                return
            await ws.send(json.dumps({"result": {"shutting_down": True}}))
            self._shutdown_event.set()
            return

        if not self._extension_ws:
            await ws.send(
                json.dumps(
                    {"error": "Extension 未连接，请确认浏览器已安装并启用 XHS Bridge 扩展"}
                )
            )
            return

        msg_id = str(uuid.uuid4())
        msg["id"] = msg_id

        loop = asyncio.get_event_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[msg_id] = future

        await self._extension_ws.send(json.dumps(msg))

        try:
            result = await asyncio.wait_for(future, timeout=90.0)
            await ws.send(json.dumps(result))
        except TimeoutError:
            self._pending.pop(msg_id, None)
            await ws.send(json.dumps({"error": "命令执行超时（90s）"}))
        except ConnectionError as e:
            await ws.send(json.dumps({"error": str(e)}))

    async def wait_for_shutdown(self) -> None:
        await self._shutdown_event.wait()


async def main(
    port: int,
    account: str,
    account_id: str | None = None,
    bridge_token: str | None = None,
    extension_instance_id: str | None = None,
) -> None:
    server = BridgeServer(
        account,
        account_id=account_id,
        bridge_token=bridge_token,
        extension_instance_id=extension_instance_id,
    )
    async with websockets.serve(server.handle, "localhost", port):
        logger.info(
            "Bridge server 已启动: account=%s ws://localhost:%d",
            account,
            port,
        )
        logger.info("等待浏览器扩展连接...")
        await server.wait_for_shutdown()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="XHS Extension Bridge Server")
    parser.add_argument("--port", type=int, default=9333, help="监听端口（默认 9333）")
    parser.add_argument("--account", default="default", help="该 Bridge 对应的账号名称")
    parser.add_argument(
        "--account-id",
        default=os.getenv("XHS_BRIDGE_ACCOUNT_ID"),
        help="账号连接 ID；通常由 CLI 通过环境变量注入",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("XHS_BRIDGE_TOKEN"),
        help="Bridge 连接令牌；通常由 CLI 通过环境变量注入",
    )
    parser.add_argument(
        "--extension-instance-id",
        default=os.getenv("XHS_EXTENSION_INSTANCE_ID"),
        help="已登记的扩展实例 ID",
    )
    args = parser.parse_args()

    asyncio.run(
        main(
            args.port,
            args.account,
            args.account_id,
            args.token,
            args.extension_instance_id,
        )
    )
