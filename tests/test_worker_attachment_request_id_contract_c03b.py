from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.registry import ClientRegistry


ROOT = Path(__file__).resolve().parents[1]
SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{6,120}")


def test_worker_attachment_request_id_grammar_matches_responses_server_contract() -> None:
    responses = (ROOT / "app/responses_v108_patch.py").read_text(encoding="utf-8")
    background = (ROOT / "chrome_extension/background.js").read_text(encoding="utf-8")
    multimodal = (ROOT / "chrome_extension/content_multimodal.js").read_text(encoding="utf-8")
    multimodal_v78 = (ROOT / "chrome_extension/content_multimodal_v78.js").read_text(encoding="utf-8")

    # Responses accepts any bounded safe ID, not only the server-generated
    # req_/resp_/imgreq_ prefixes. Every Worker hop must accept that same set.
    assert 're.fullmatch(r"[A-Za-z0-9_-]{6,120}", raw_id)' in responses
    assert "if (!/^[A-Za-z0-9_-]{6,120}$/.test(request))" in background
    assert "if (!/^[A-Za-z0-9_-]{6,120}$/.test(requestId))" in multimodal
    assert "if (!/^[A-Za-z0-9_-]{6,120}$/.test(scopedRequestId))" in multimodal

    # The compatibility content-script path already uses the same alphabet
    # and bound; keep it aligned as the Worker manifest continues to load it.
    assert "if (!/^[a-zA-Z0-9_-]{6,120}$/.test(requestId))" in multimodal_v78

    valid = (
        "custom-client-123456",
        "req_abcdef123456",
        "resp_req_abcdef123456",
        "imgreq_abcdef123456",
    )
    invalid = ("", "short", "bad/request", "bad.request", "x" * 121)
    assert all(SAFE_REQUEST_ID.fullmatch(value) for value in valid)
    assert all(not SAFE_REQUEST_ID.fullmatch(value) for value in invalid)


def test_voice_worker_fetches_audio_with_the_original_request_id() -> None:
    for filename in ("content_voice.js", "content_voice_v2.js"):
        source = (ROOT / "chrome_extension" / filename).read_text(encoding="utf-8")
        assert "async function fetchAudio(spec, requestId)" in source
        assert "requestId: String(requestId || \"\")" in source
        assert "fetchAudio(message.audio, active.requestId)" in source


def test_voice_request_binds_the_current_worker_socket_for_scoped_file_download(tmp_path) -> None:
    class FakeSocket:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

    registry = ClientRegistry(tmp_path)
    socket = FakeSocket()
    state = SimpleNamespace(
        dispatch_socket=None, client_id="ext_voice_worker",
        terminal_event_type=None, completed_mono=None,
    )
    live_state = SimpleNamespace(
        dispatch_socket=None, client_id="ext_voice_worker",
        terminal_event_type=None, completed_mono=None,
    )
    registry.sockets["ext_voice_worker"] = socket
    registry.socket_locks["ext_voice_worker"] = asyncio.Lock()
    registry.request_broker = SimpleNamespace(requests={
        "req_voice_file_123": state,
        "voicereq_live_media_123": live_state,
    })
    registry.worker_admin_enabled = lambda _client_id: True
    registry.chatgpt_routing_ready = lambda _client_id: True

    asyncio.run(registry.send("ext_voice_worker", {
        "type": "voice.request", "request_id": "req_voice_file_123",
        "audio": {"file_id": "file_audio"},
    }))

    assert state.dispatch_socket is socket
    assert socket.sent[0]["request_id"] == "req_voice_file_123"

    asyncio.run(registry.send("ext_voice_worker", {
        "type": "voice.live.start",
        "request_id": "voicereq_live_media_123",
    }))
    assert live_state.dispatch_socket is socket

    # A reconnect may replace the registry's current socket while the old
    # Live session is still owned by its original socket. Follow-up media and
    # cancellation must fail closed rather than being sent to the replacement.
    replacement = FakeSocket()
    registry.sockets["ext_voice_worker"] = replacement
    registry.socket_locks["ext_voice_worker"] = asyncio.Lock()
    with pytest.raises(RuntimeError, match="connection changed"):
        asyncio.run(registry.send("ext_voice_worker", {
            "type": "voice.live.multimodal",
            "request_id": "voicereq_live_media_123",
            "attachments": [{
                "file_id": "file_live_media",
                "request_id": "voicereq_live_media_123",
            }],
        }))
    with pytest.raises(RuntimeError, match="connection changed"):
        asyncio.run(registry.send("ext_voice_worker", {
            "type": "chat.cancel", "request_id": "req_voice_file_123",
        }))
    assert live_state.dispatch_socket is socket
    assert replacement.sent == []

    # The original socket may continue the session; a multimodal message alone
    # cannot establish a session/socket grant without voice.live.start.
    registry.sockets["ext_voice_worker"] = socket
    registry.socket_locks["ext_voice_worker"] = asyncio.Lock()
    asyncio.run(registry.send("ext_voice_worker", {
        "type": "voice.live.multimodal",
        "request_id": "voicereq_live_media_123",
        "attachments": [{
            "file_id": "file_live_media",
            "request_id": "voicereq_live_media_123",
        }],
    }))
    assert live_state.dispatch_socket is socket
    assert socket.sent[-1]["attachments"][0]["request_id"] == "voicereq_live_media_123"

    orphan_state = SimpleNamespace(
        dispatch_socket=None, client_id="ext_voice_worker",
        terminal_event_type=None, completed_mono=None,
    )
    registry.request_broker.requests["voicereq_orphan_media_123"] = orphan_state
    with pytest.raises(RuntimeError, match="connection changed"):
        asyncio.run(registry.send("ext_voice_worker", {
            "type": "voice.live.multimodal",
            "request_id": "voicereq_orphan_media_123",
            "attachments": [{"file_id": "file_orphan", "request_id": "voicereq_orphan_media_123"}],
        }))
    assert orphan_state.dispatch_socket is None

    # A request that has already reached a Broker terminal cannot receive
    # further media or cancellation commands during its release grace period.
    live_state.terminal_event_type = "voice.live.completed"
    with pytest.raises(RuntimeError, match="connection changed"):
        asyncio.run(registry.send("ext_voice_worker", {
            "type": "voice.live.audio",
            "request_id": "voicereq_live_media_123",
            "pcm_base64": "AA==",
        }))


def test_live_voice_media_grant_is_persisted_and_request_scoped_before_worker_dispatch() -> None:
    source = (ROOT / "app/live_voice_patch.py").read_text(encoding="utf-8")
    assert 'request_scoped_attachments = [' in source
    assert '{**spec, "request_id": request_id}' in source
    assert '"input_file_ids": next_media_ids' in source
    assert '"code": "MEDIA_GRANT_PERSIST_FAILED"' in source
    grant = source.index('"input_file_ids": next_media_ids')
    dispatch = source.index('"type": "voice.live.multimodal"', grant)
    assert grant < dispatch


def test_voice_request_telemetry_persists_the_audio_file_grant() -> None:
    source = (ROOT / "app/voice_patch.py").read_text(encoding="utf-8")
    assert '"input_file_ids": list(dict.fromkeys(' in source
    assert 'audio_spec.get("file_id")' in source
    assert "input_file_ids=input_file_ids" in source


def test_worker_request_prepares_request_scoped_attachments_before_prompt_submission() -> None:
    request_v6 = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    run_request = request_v6.split("async function runRequest(message)", 1)[1].split(
        "async function cancelRequest", 1
    )[0]

    assert "globalThis.__CHAT2API_MULTIMODAL_V4__" in run_request
    assert "await multimodal.attach(attachmentSpecs" in run_request
    assert "request_id: active.requestId" in run_request
    assert "prepared?.attachment_verified !== true" in run_request

    # The owner-scoped file download and ChatGPT composer upload must finish
    # before the request prompt is written or submitted.
    prepare = run_request.index("await multimodal.attach(attachmentSpecs")
    write_prompt = run_request.index("await writePrompt(active, prompt)")
    submit = run_request.index("await submitAndConfirm(active)")
    assert prepare < write_prompt < submit
