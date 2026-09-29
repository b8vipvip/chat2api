from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from app.registry import ClientRegistry
import app.registry_transport_health_v149_patch  # noqa: F401  # install transport guard


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_network_terminal_evidence_keeps_request_v6_as_single_terminal_owner() -> None:
    request = source("chrome_extension/content_request_v6.js")
    network = source("chrome_extension/content_network_stream_recovery_v55.js")

    assert 'owner: "network-stream-evidence-v57"' in network
    assert 'network_response_recovery: "evidence-only-v57"' in network
    assert 'network_terminal_authority: "request-v6"' in network
    assert "active.networkTerminalText = text" in network
    assert "active.networkTerminalAt = Date.now()" in network
    assert "The network observer never emits chat.completed" in network
    assert 'type: "chat.completed"' not in network

    assert "if (v5) v5.active = active;" in request
    assert '"network-terminal-v57"' in request
    assert 'response_terminal_source: "network-sse-terminal-v57"' in request
    assert 'type: "chat.completed"' in request
    assert "networkTerminalAt && networkTerminalText && !promptStillPresent(active)" in request


def test_submission_confirmation_never_treats_generating_control_as_sufficient() -> None:
    request = source("chrome_extension/content_request_v6.js")

    assert "function composerCandidates(active = null)" in request
    assert "function composerHoldingPrompt(active)" in request
    assert "function promptStillPresent(active)" in request
    assert "if (promptStillPresent(active)) return null;" in request
    assert "composerHoldingPrompt(active) || active.promptComposer || findComposer()" in request
    assert "Stop/Generating UI is not submission proof" in request
    assert 'reason: "click-generating"' not in request


def test_windows_manual_pairing_forces_fresh_identity_and_keeps_auto_binding_off() -> None:
    popup = source("chrome_extension/popup.js")
    background = source("chrome_extension/background.js")

    assert 'type: "popup.pair"' in popup
    assert "force: true" in popup
    assert "autoBind: false" in popup
    assert "Manual Windows pairing is an explicit identity replacement" in popup
    assert "async function pair({ serverUrl, pairingCode, extensionName, force = false, autoBind = undefined })" in background
    assert "if (!force && sameServer && existing.clientId && existing.clientToken)" in background
    assert "autoBind: autoBind === undefined ? existing.autoBind : Boolean(autoBind)" in background


def test_windows_popup_distinguishes_transport_drop_from_active_request_lease() -> None:
    popup = source("chrome_extension/popup.js")

    assert "/WebSocket closed \\(1006\\)/i.test(text)" in popup
    assert "/worker_active_request_lease|still has active requests/i.test(text)" in popup
    assert "Worker 会自动重连" in popup
    assert "当前 Worker 仍有请求未进入终态" in popup


class _ReplacementSocket:
    async def send_json(self, _payload: dict) -> None:
        return None


class _DisconnectingSocket:
    def __init__(self, registry: ClientRegistry, client_id: str, replacement: object | None = None) -> None:
        self.registry = registry
        self.client_id = client_id
        self.replacement = replacement

    async def send_json(self, _payload: dict) -> None:
        if self.replacement is not None:
            self.registry.sockets[self.client_id] = self.replacement  # type: ignore[assignment]
        raise WebSocketDisconnect(code=1006)


def test_registry_1006_detaches_the_exact_dead_socket(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ClientRegistry(tmp_path)
        client_id, _token = await registry.register("Windows Worker", "Chrome", "0.22.103", {})
        dead = _DisconnectingSocket(registry, client_id)
        registry.sockets[client_id] = dead  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="Chrome extension is offline"):
            await registry.send(client_id, {"type": "heartbeat"})

        assert client_id not in registry.sockets

    asyncio.run(run())


def test_registry_1006_does_not_detach_a_newer_replacement_socket(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ClientRegistry(tmp_path)
        client_id, _token = await registry.register("Windows Worker", "Chrome", "0.22.103", {})
        replacement = _ReplacementSocket()
        dead = _DisconnectingSocket(registry, client_id, replacement)
        registry.sockets[client_id] = dead  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="Chrome extension is offline"):
            await registry.send(client_id, {"type": "heartbeat"})

        assert registry.sockets.get(client_id) is replacement

    asyncio.run(run())
