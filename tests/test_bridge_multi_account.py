from __future__ import annotations

import asyncio
import json

from scripts.account_manager import add_account
from scripts.account_pairing import create_pairing_session, decode_pairing_bundle
from scripts.bridge_server import BridgeServer

_CLOSE = object()


class FakeWebSocket:
    def __init__(self, first_message: dict):
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.outgoing: asyncio.Queue[str] = asyncio.Queue()
        self.incoming.put_nowait(json.dumps(first_message))

    async def recv(self) -> str:
        return await self.incoming.get()

    async def send(self, message: str) -> None:
        await self.outgoing.put(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self.incoming.put(_CLOSE)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        message = await self.incoming.get()
        if message is _CLOSE:
            raise StopAsyncIteration
        return message


async def _respond_once(extension: FakeWebSocket, account: str) -> None:
    while True:
        request = json.loads(await extension.outgoing.get())
        if request.get("id"):
            break
    await extension.incoming.put(
        json.dumps(
            {
                "id": request["id"],
                "result": {"account": account, "method": request["method"]},
            }
        )
    )


async def _exercise_two_accounts() -> None:
    alpha_server = BridgeServer("alpha")
    beta_server = BridgeServer("beta")
    alpha_extension = FakeWebSocket({"role": "extension", "account": "alpha"})
    beta_extension = FakeWebSocket({"role": "extension", "account": "beta"})
    extension_tasks = [
        asyncio.create_task(alpha_server.handle(alpha_extension)),
        asyncio.create_task(beta_server.handle(beta_extension)),
    ]
    await asyncio.sleep(0)

    alpha_cli = FakeWebSocket(
        {"role": "cli", "account": "alpha", "method": "alpha-work"}
    )
    beta_cli = FakeWebSocket({"role": "cli", "account": "beta", "method": "beta-work"})
    responders = [
        asyncio.create_task(_respond_once(alpha_extension, "alpha")),
        asyncio.create_task(_respond_once(beta_extension, "beta")),
    ]
    await asyncio.gather(alpha_server.handle(alpha_cli), beta_server.handle(beta_cli))
    await asyncio.gather(*responders)

    alpha_result = json.loads(await alpha_cli.outgoing.get())
    beta_result = json.loads(await beta_cli.outgoing.get())
    assert alpha_result["result"] == {"account": "alpha", "method": "alpha-work"}
    assert beta_result["result"] == {"account": "beta", "method": "beta-work"}

    mismatch_cli = FakeWebSocket(
        {"role": "cli", "account": "beta", "method": "wrong-route"}
    )
    await alpha_server.handle(mismatch_cli)
    mismatch = json.loads(await mismatch_cli.outgoing.get())
    assert "账号路由不匹配" in mismatch["error"]

    await alpha_extension.incoming.put(_CLOSE)
    await beta_extension.incoming.put(_CLOSE)
    await asyncio.gather(*extension_tasks)


def test_two_accounts_route_commands_concurrently():
    asyncio.run(_exercise_two_accounts())


async def _exercise_extension_identity_validation() -> None:
    server = BridgeServer(
        "alpha",
        account_id="slot-alpha",
        bridge_token="secret-alpha",
        extension_instance_id="instance-alpha",
    )
    wrong = FakeWebSocket(
        {
            "role": "extension",
            "account": "alpha",
            "account_id": "slot-alpha",
            "bridge_token": "wrong-token",
            "instance_id": "instance-alpha",
        }
    )
    await server.handle(wrong)
    rejected = json.loads(await wrong.outgoing.get())
    assert "令牌不匹配" in rejected["error"]
    assert server._extension_ws is None

    extension = FakeWebSocket(
        {
            "role": "extension",
            "account": "alpha",
            "account_id": "slot-alpha",
            "bridge_token": "secret-alpha",
            "instance_id": "instance-alpha",
            "extension_id": "chrome-extension-id",
            "profile_directory": "Default",
        }
    )
    task = asyncio.create_task(server.handle(extension))
    await asyncio.sleep(0)
    handshake_ack = json.loads(await extension.outgoing.get())
    assert handshake_ack["type"] == "handshake_ack"
    assert handshake_ack["identity_verified"] is True
    ping = FakeWebSocket(
        {"role": "cli", "account": "alpha", "method": "ping_server"}
    )
    await server.handle(ping)
    status = json.loads(await ping.outgoing.get())["result"]
    assert status["extension_connected"] is True
    assert status["extension"]["identity_verified"] is True
    assert status["extension"]["instance_enrolled"] is True

    unauthenticated_cli = FakeWebSocket(
        {"role": "cli", "account": "alpha", "method": "navigate"}
    )
    await server.handle(unauthenticated_cli)
    auth_error = json.loads(await unauthenticated_cli.outgoing.get())
    assert "CLI 账号连接 ID 不匹配" in auth_error["error"]

    authenticated_cli = FakeWebSocket(
        {
            "role": "cli",
            "account": "alpha",
            "account_id": "slot-alpha",
            "bridge_token": "secret-alpha",
            "method": "authenticated-work",
        }
    )
    responder = asyncio.create_task(_respond_once(extension, "alpha"))
    await server.handle(authenticated_cli)
    await responder
    authenticated_result = json.loads(await authenticated_cli.outgoing.get())
    assert authenticated_result["result"]["method"] == "authenticated-work"

    wrong_shutdown = FakeWebSocket(
        {
            "role": "cli",
            "account": "alpha",
            "method": "shutdown_server",
            "params": {"account_id": "slot-alpha", "bridge_token": "wrong"},
        }
    )
    await server.handle(wrong_shutdown)
    shutdown_error = json.loads(await wrong_shutdown.outgoing.get())
    assert "令牌不匹配" in shutdown_error["error"]

    shutdown = FakeWebSocket(
        {
            "role": "cli",
            "account": "alpha",
            "method": "shutdown_server",
            "params": {
                "account_id": "slot-alpha",
                "bridge_token": "secret-alpha",
            },
        }
    )
    await server.handle(shutdown)
    assert json.loads(await shutdown.outgoing.get())["result"]["shutting_down"] is True
    await asyncio.wait_for(server.wait_for_shutdown(), timeout=0.1)

    await extension.incoming.put(_CLOSE)
    await task


def test_extension_connection_requires_credentials_and_enrolled_instance():
    asyncio.run(_exercise_extension_identity_validation())


async def _exercise_pairing_and_cli_auth(config, pairing_payload) -> None:
    server = BridgeServer(
        config.name,
        account_id=config.account_id,
        bridge_token=config.bridge_token,
    )
    pairing_ws = FakeWebSocket(
        {
            "role": "pairing",
            "account": config.name,
            "pairing_code": pairing_payload["pairingCode"],
            "instance_id": "instance_alpha_123",
            "extension_id": "f" * 32,
            "profile_directory": "Default",
        }
    )
    await server.handle(pairing_ws)
    pairing_ack = json.loads(await pairing_ws.outgoing.get())
    assert pairing_ack["type"] == "pairing_ack"
    binding = pairing_ack["binding"]
    assert binding["account"] == config.name
    assert binding["instanceId"] == "instance_alpha_123"
    assert binding["bridgeToken"] != config.bridge_token

    extension = FakeWebSocket(
        {
            "role": "extension",
            "account": config.name,
            "account_id": binding["accountId"],
            "bridge_token": binding["bridgeToken"],
            "instance_id": binding["instanceId"],
            "extension_id": "f" * 32,
            "profile_directory": "Default",
        }
    )
    extension_task = asyncio.create_task(server.handle(extension))
    await asyncio.sleep(0)
    assert json.loads(await extension.outgoing.get())["type"] == "handshake_ack"

    unauthenticated = FakeWebSocket(
        {"role": "cli", "account": config.name, "method": "navigate"}
    )
    await server.handle(unauthenticated)
    assert "CLI 账号连接 ID 不匹配" in json.loads(
        await unauthenticated.outgoing.get()
    )["error"]

    wrong_token = FakeWebSocket(
        {
            "role": "cli",
            "account": config.name,
            "account_id": binding["accountId"],
            "bridge_token": "wrong",
            "method": "navigate",
        }
    )
    await server.handle(wrong_token)
    assert "CLI Bridge 连接令牌不匹配" in json.loads(
        await wrong_token.outgoing.get()
    )["error"]

    authenticated = FakeWebSocket(
        {
            "role": "cli",
            "account": config.name,
            "account_id": binding["accountId"],
            "bridge_token": binding["bridgeToken"],
            "method": "navigate",
        }
    )
    responder = asyncio.create_task(_respond_once(extension, config.name))
    await server.handle(authenticated)
    await responder
    result = json.loads(await authenticated.outgoing.get())
    assert result["result"]["method"] == "navigate"

    await extension.incoming.put(_CLOSE)
    await extension_task


def test_pairing_enrolls_instance_and_cli_commands_require_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = tmp_path / "extension-source"
    source.mkdir()
    (source / "manifest.json").write_text("{}", encoding="utf-8")
    (source / "bridge_config.js").write_text("// default", encoding="utf-8")
    config = add_account("alpha", bridge_port=19611, extension_source=source)
    pairing_payload = decode_pairing_bundle(
        create_pairing_session(config)["pairing_bundle"]
    )

    asyncio.run(_exercise_pairing_and_cli_auth(config, pairing_payload))
