import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
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


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer master-key"}


def app_v6(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    return app


def pair_extension(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Chrome", "version": "0.6.4"},
    )
    assert response.status_code == 200
    body = response.json()
    return body["client_id"], body["token"]


def complete_voice(websocket, request_id: str, transcript: str = "语音测试成功") -> None:
    websocket.send_json({"type": "image.started", "kind": "voice", "request_id": request_id, "diagnostics": {"route": "chatgpt-voice"}})
    websocket.send_json({
        "type": "image.diagnostics", "kind": "voice", "request_id": request_id,
        "diagnostics": {"route": "chatgpt-voice", "remote_track_seen": True, "remote_sound_started": True, "audio_bytes": 64},
    })
    websocket.send_json({
        "type": "image.completed", "kind": "voice", "request_id": request_id,
        "voice": {
            "transcript": transcript,
            "b64_json": base64.b64encode(b"fake-webm-opus-audio" * 8).decode(),
            "mime_type": "audio/webm;codecs=opus",
            "size": 160,
            "duration_ms": 950,
        },
    })


def test_v6_version_capabilities_and_models(tmp_path: Path) -> None:
    with TestClient(app_v6(tmp_path)) as client:
        root = client.get("/").json()
        assert root["version"] == "0.6.0"
        health = client.get("/healthz").json()
        assert health["version"] == "0.6.0"
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.6.0"
        assert overview["capabilities"]["voice_generation"] is True
        assert overview["capabilities"]["voice_conversation"] is True
        assert overview["capabilities"]["gpt_live"] is True
        models = client.get("/v1/models", headers=headers()).json()["data"]
        ids = {item["id"] for item in models}
        assert {"gpt-live", "gpt-live-mini"}.issubset(ids)


def test_gpt_live_speech_round_trip_and_telemetry(tmp_path: Path) -> None:
    with TestClient(app_v6(tmp_path)) as client:
        client_id, extension_token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as websocket:
            websocket.receive_json()
            def request():
                return client.post("/v1/audio/speech", headers=headers(), json={"model": "gpt-live", "input": "请说测试成功", "timeout": 30})
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "voice.request"
                assert message["options"]["mode"] == "speech"
                complete_voice(websocket, message["request_id"], "GPT Live 语音生成成功")
                response = future.result(timeout=5)
        assert response.status_code == 200
        payload = response.json()
        assert payload["object"] == "audio.speech"
        assert payload["model"] == "gpt-live"
        assert payload["transcript"] == "GPT Live 语音生成成功"
        assert payload["audio"]["b64_json"]
        assert payload["chat2api"]["diagnostics"]["remote_track_seen"] is True
        records = client.get("/api/admin/requests?limit=20", headers=headers()).json()["data"]
        row = next(item for item in records if item["request_id"] == payload["chat2api"]["request_id"])
        assert row["request_type"] == "voice_generation"
        assert row["status"] == "completed"


def test_gpt_live_audio_conversation_round_trip(tmp_path: Path) -> None:
    with TestClient(app_v6(tmp_path)) as client:
        upload = client.post("/v1/files", headers=headers(), json={
            "filename": "question.webm", "mime_type": "audio/webm",
            "data_base64": base64.b64encode(b"fake-user-audio" * 12).decode(), "purpose": "voice-input",
        })
        assert upload.status_code == 200
        file_id = upload.json()["id"]
        client_id, extension_token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as websocket:
            websocket.receive_json()
            def request():
                return client.post("/v1/audio/conversations", headers=headers(), json={"model": "gpt-live", "audio_file_id": file_id, "timeout": 30})
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "voice.request"
                assert message["options"]["mode"] == "conversation"
                assert message["audio"]["file_id"] == file_id
                complete_voice(websocket, message["request_id"], "我听到了你的语音")
                response = future.result(timeout=5)
        assert response.status_code == 200
        payload = response.json()
        assert payload["object"] == "audio.conversation"
        assert payload["transcript"] == "我听到了你的语音"
        assert payload["audio"]["b64_json"]


def test_managed_keys_gain_audio_scope(tmp_path: Path) -> None:
    with TestClient(app_v6(tmp_path)) as client:
        created = client.post("/api/admin/keys", headers=headers(), json={"name": "Voice App", "expires_in_days": 7})
        assert created.status_code == 200
        assert "audio" in created.json()["key"]["scopes"]


def test_extension_contains_visual_error_guard_and_voice_bridge() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = (root / "chrome_extension" / "manifest.json").read_text(encoding="utf-8")
    guard = (root / "chrome_extension" / "content_guard.js").read_text(encoding="utf-8")
    voice = (root / "chrome_extension" / "content_voice.js").read_text(encoding="utf-8")
    voice_v2 = (root / "chrome_extension" / "content_voice_v2.js").read_text(encoding="utf-8")
    dictation_v3 = (root / "chrome_extension" / "content_dictation_v3.js").read_text(encoding="utf-8")
    dictation_v4 = (root / "chrome_extension" / "content_dictation_v4.js").read_text(encoding="utf-8")
    main_world = (root / "chrome_extension" / "voice_main.js").read_text(encoding="utf-8")
    main_world_v2 = (root / "chrome_extension" / "voice_main_v2.js").read_text(encoding="utf-8")
    routing = (root / "chrome_extension" / "voice_routing.js").read_text(encoding="utf-8")
    audio_routing = (root / "chrome_extension" / "audio_routing_v2.js").read_text(encoding="utf-8")
    image_routing = (root / "chrome_extension" / "image_routing.js").read_text(encoding="utf-8")
    multimodal = (root / "chrome_extension" / "content_multimodal.js").read_text(encoding="utf-8")
    assert '"version": "0.6.4"' in manifest
    assert '"world": "MAIN"' in manifest
    assert "content_dictation_v4.js" in manifest
    assert "出了点问题" in guard and "ui_retry_count" in guard and "chat.error" in guard
    assert "chat2api.voice.request" in voice
    assert "voice_stage" in voice_v2 and "启动语音功能" in voice_v2 and "chat2api.voice.request.v2" in voice_v2
    assert "chat2api.dictation.request.v3" in dictation_v3
    assert "chat2api.dictation.request.v4" in dictation_v4
    assert "dictation-ui-ended" in dictation_v4 and "transcription-ready" in dictation_v4 and "send-confirmed" in dictation_v4
    assert "voice.mic.stopped" in main_world_v2
    assert "RTCPeerConnection" in main_world and "MediaRecorder" in main_world and "getUserMedia" in main_world
    assert "voice.request" in routing and "voice-generation" in routing
    assert "chat2api.dictation.request.v4" in audio_routing and "activateAudioTab" in audio_routing
    assert "reuse-bound-tab" in image_routing and "restoreImageSession" in image_routing
    assert "duplicate_upload_dialog" in multimodal and "attachment_retry_suppressed" in multimodal
    assert "uploadOne" in multimodal and "attachment_verified" in multimodal
