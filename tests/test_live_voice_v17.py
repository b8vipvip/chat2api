import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.live_voice_patch import install_live_voice_patch
from app.main import create_app
from app.voice_patch import install_voice_patch


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def app_live(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_live_voice_patch(app)
    return app


def pair_extension(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Chrome", "version": "0.7.6"},
    )
    assert response.status_code == 200
    body = response.json()
    return body["client_id"], body["token"]


def create_managed_key(client: TestClient, name: str = "voice-test") -> str:
    response = client.post(
        "/api/admin/keys",
        headers={"Authorization": "Bearer master-key"},
        json={"name": name},
    )
    assert response.status_code == 200
    return response.json()["token"]


def test_live_voice_stream_relays_pcm_both_directions(tmp_path: Path) -> None:
    with TestClient(app_live(tmp_path)) as client:
        managed_key = create_managed_key(client)
        client_id, extension_token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as extension:
            assert extension.receive_json()["type"] == "server.hello"
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={client_id}",
                headers={"Authorization": f"Bearer {managed_key}"},
            ) as live:
                live.send_json({"type": "session.start", "model": "gpt-live", "instructions": "自然聊天"})
                started = extension.receive_json()
                assert started["type"] == "voice.live.start"
                assert started["options"]["model"] == "gpt-live"
                assert started["options"]["requested_model"] == "gpt-live"
                request_id = started["request_id"]
                session_id = started["session_id"]

                extension.send_json({
                    "type": "image.progress",
                    "kind": "voice-live",
                    "request_id": request_id,
                    "live_event": "session.ready",
                    "model": "gpt-live",
                })
                ready = live.receive_json()
                assert ready["type"] == "session.ready"
                assert ready["session_id"] == session_id
                assert ready["model"] == "gpt-live"
                assert ready["effective_model"] == "gpt-live"
                assert ready["input_audio_format"] == "pcm16le-16000-mono"
                assert ready["output_audio_format"] == "pcm16le-24000-mono"

                microphone_pcm = b"\x01\x02" * 320
                live.send_bytes(microphone_pcm)
                inbound = extension.receive_json()
                assert inbound["type"] == "voice.live.audio"
                assert base64.b64decode(inbound["pcm_base64"]) == microphone_pcm
                assert inbound["sample_rate"] == 16000

                extension.send_json({
                    "type": "image.progress", "kind": "voice-live", "request_id": request_id,
                    "live_event": "input.speech_started",
                })
                assert live.receive_json() == {"type": "input_audio_buffer.speech_started"}
                extension.send_json({
                    "type": "image.progress", "kind": "voice-live", "request_id": request_id,
                    "live_event": "input.speech_stopped",
                })
                assert live.receive_json() == {"type": "input_audio_buffer.speech_stopped"}

                response_id = "response-live-1"
                extension.send_json({
                    "type": "image.progress", "kind": "voice-live", "request_id": request_id,
                    "live_event": "response.created", "response_id": response_id,
                })
                assert live.receive_json() == {"type": "response.created", "response_id": response_id}
                extension.send_json({
                    "type": "image.progress", "kind": "voice-live", "request_id": request_id,
                    "live_event": "response.audio.started", "response_id": response_id,
                })
                assert live.receive_json() == {"type": "response.audio.started", "response_id": response_id}

                speaker_pcm = b"\x03\x04" * 480
                extension.send_json({
                    "type": "image.progress", "kind": "voice-live", "request_id": request_id,
                    "live_event": "response.audio.delta", "response_id": response_id,
                    "pcm_base64": base64.b64encode(speaker_pcm).decode(),
                })
                assert live.receive_bytes() == speaker_pcm

                extension.send_json({
                    "type": "image.progress", "kind": "voice-live", "request_id": request_id,
                    "live_event": "response.text.snapshot", "response_id": response_id, "text": "你好呀",
                })
                text = live.receive_json()
                assert text == {"type": "response.text.delta", "response_id": response_id, "delta": "你好呀"}

                live.send_json({"type": "response.cancel"})
                cancel = extension.receive_json()
                assert cancel["type"] == "voice.live.cancel_response"

                live.send_json({"type": "session.finish"})
                stopped = extension.receive_json()
                assert stopped["type"] == "voice.live.stop"


def test_live_mini_is_explicit_alias_of_gpt_live(tmp_path: Path) -> None:
    with TestClient(app_live(tmp_path)) as client:
        managed_key = create_managed_key(client)
        client_id, extension_token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as extension:
            extension.receive_json()
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={client_id}",
                headers={"Authorization": f"Bearer {managed_key}"},
            ) as live:
                live.send_json({"type": "session.start", "model": "gpt-live-mini"})
                started = extension.receive_json()
                assert started["options"]["requested_model"] == "gpt-live-mini"
                assert started["options"]["model"] == "gpt-live"
                assert started["options"]["model_alias_of"] == "gpt-live"
                extension.send_json({
                    "type": "image.progress",
                    "kind": "voice-live",
                    "request_id": started["request_id"],
                    "live_event": "session.ready",
                    "model": "gpt-live",
                })
                ready = live.receive_json()
                assert ready["model"] == "gpt-live-mini"
                assert ready["effective_model"] == "gpt-live"
                assert ready["model_alias_of"] == "gpt-live"
                live.send_json({"type": "session.finish"})
                assert extension.receive_json()["type"] == "voice.live.stop"


def test_browser_webapp_can_use_one_time_realtime_session_token(tmp_path: Path) -> None:
    with TestClient(app_live(tmp_path)) as client:
        managed_key = create_managed_key(client)
        client_id, extension_token = pair_extension(client)
        token_response = client.post(
            "/v1/audio/realtime/sessions",
            headers={"Authorization": f"Bearer {managed_key}"},
        )
        assert token_response.status_code == 200
        token_payload = token_response.json()
        assert token_payload["object"] == "realtime.session_token"
        assert token_payload["expires_in"] == 60
        assert token_payload["session_token"].startswith("rt-chat2api-")
        assert "session_token=" in token_payload["websocket_path"]

        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as extension:
            extension.receive_json()
            with client.websocket_connect(
                token_payload["websocket_path"] + f"&client_id={client_id}",
            ) as live:
                live.send_json({"type": "session.start", "model": "gpt-live"})
                started = extension.receive_json()
                assert started["type"] == "voice.live.start"
                live.send_json({"type": "session.finish"})
                assert extension.receive_json()["type"] == "voice.live.stop"

        try:
            with client.websocket_connect(token_payload["websocket_path"]):
                raise AssertionError("one-time realtime token unexpectedly reused")
        except Exception:
            pass


def test_live_voice_rejects_legacy_master_key_and_wrong_key(tmp_path: Path) -> None:
    with TestClient(app_live(tmp_path)) as client:
        for token in ("master-key", "wrong-key"):
            try:
                with client.websocket_connect(
                    "/v1/audio/realtime",
                    headers={"Authorization": f"Bearer {token}"},
                ):
                    raise AssertionError("unauthorized websocket unexpectedly connected")
            except Exception:
                pass


def test_live_extension_assets_are_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    entry = (root / "app" / "entry.py").read_text(encoding="utf-8")
    background = (root / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    main = (root / "chrome_extension" / "voice_live_main.js").read_text(encoding="utf-8")
    content = (root / "chrome_extension" / "content_voice_live.js").read_text(encoding="utf-8")
    router = (root / "chrome_extension" / "audio_routing_live.js").read_text(encoding="utf-8")
    assert "install_live_voice_patch(app)" in entry
    assert "audio_routing_live.js" in background
    assert "voice.live.audio" in main and "24000" in main
    assert "voice.live.input.speech_started" in main
    assert "chat2api.voice.live.start" in content
    assert "input.speech_started" in content
    assert "voice.live.start" in router
