import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v7_patch import install_v7_patch
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


def app_v7(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    return app


def pair_extension(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Chrome", "version": "0.6.2"},
    )
    assert response.status_code == 200
    body = response.json()
    return body["client_id"], body["token"]


def test_v7_version_models_capabilities_and_default_audio_asset(tmp_path: Path) -> None:
    with TestClient(app_v7(tmp_path)) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert root.json()["version"] == "0.7.0"
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["version"] == "0.7.0"
        overview = client.get("/api/admin/overview", headers=headers())
        assert overview.status_code == 200
        data = overview.json()
        assert data["version"] == "0.7.0"
        assert data["capabilities"]["voice_generation"] is True
        assert data["capabilities"]["voice_conversation"] is True
        assert data["capabilities"]["dictation"] is True
        assert data["capabilities"]["audio_transcription"] is True
        models = client.get("/v1/models", headers=headers())
        assert models.status_code == 200
        by_id = {item["id"]: item for item in models.json()["data"]}
        assert "gpt-live" in by_id
        assert "gpt-dictation" in by_id
        assert "audio-transcription" in by_id["gpt-dictation"]["capabilities"]
        assert "recommended" in by_id["gpt-live"]["label"].lower()
        sample = client.get("/assets/chat2api-test-dictation.mp3")
        assert sample.status_code == 200
        assert sample.headers["content-type"].startswith("audio/mpeg")
        assert len(sample.content) > 1000
        assert sample.content[:3] == b"ID3"


def test_dictation_round_trip_and_telemetry(tmp_path: Path) -> None:
    with TestClient(app_v7(tmp_path)) as client:
        upload = client.post(
            "/v1/files", headers=headers(),
            json={
                "filename": "dictation.mp3", "mime_type": "audio/mpeg",
                "data_base64": base64.b64encode(b"fake-mp3-audio" * 20).decode(),
                "purpose": "voice-input",
            },
        )
        assert upload.status_code == 200
        file_id = upload.json()["id"]
        client_id, extension_token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as websocket:
            websocket.receive_json()
            def request():
                return client.post(
                    "/v1/audio/transcriptions", headers=headers(),
                    json={"model": "gpt-dictation", "audio_file_id": file_id, "timeout": 30},
                )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "dictation.request"
                assert message["audio"]["file_id"] == file_id
                assert message["options"]["model"] == "gpt-dictation"
                request_id = message["request_id"]
                websocket.send_json({
                    "type": "image.started", "kind": "dictation", "request_id": request_id,
                    "diagnostics": {"route": "chatgpt-dictation", "dictation_stage": "recording", "synthetic_mic_seen": True},
                })
                websocket.send_json({
                    "type": "image.diagnostics", "kind": "dictation", "request_id": request_id,
                    "diagnostics": {"dictation_stage": "completed", "transcript_chars": 30},
                })
                websocket.send_json({
                    "type": "image.completed", "kind": "dictation", "request_id": request_id,
                    "text": "chat two API test seven four two",
                })
                response = future.result(timeout=5)
        assert response.status_code == 200
        payload = response.json()
        assert payload["object"] == "audio.transcription"
        assert payload["model"] == "gpt-dictation"
        assert payload["text"] == "chat two API test seven four two"
        assert payload["chat2api"]["diagnostics"]["synthetic_mic_seen"] is True
        records = client.get("/api/admin/requests?limit=20", headers=headers()).json()["data"]
        row = next(item for item in records if item["request_id"] == payload["chat2api"]["request_id"])
        assert row["request_type"] == "dictation"
        assert row["requested_model"] == "gpt-dictation"
        assert row["status"] == "completed"


def test_dictation_rejects_non_audio_and_wrong_model(tmp_path: Path) -> None:
    with TestClient(app_v7(tmp_path)) as client:
        upload = client.post(
            "/v1/files", headers=headers(),
            json={
                "filename": "notes.txt", "mime_type": "text/plain",
                "data_base64": base64.b64encode(b"hello").decode(), "purpose": "file-understanding",
            },
        )
        assert upload.status_code == 200
        file_id = upload.json()["id"]
        wrong_model = client.post("/v1/audio/transcriptions", headers=headers(), json={"model": "gpt-live", "audio_file_id": file_id})
        assert wrong_model.status_code == 400
        wrong_file = client.post("/v1/audio/transcriptions", headers=headers(), json={"model": "gpt-dictation", "audio_file_id": file_id})
        assert wrong_file.status_code == 400


def test_admin_v7_removes_request_detail_and_adds_default_asset_tests() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "admin_v7.js").read_text(encoding="utf-8")
    assert 'section.querySelector(".panel.detail")?.remove()' in source
    assert "onclick=\"requestDetail" not in source
    assert "makeDefaultImage" in source
    assert "makeDefaultVideo" in source
    assert "canvas.captureStream" in source
    assert "makeDefaultDocuments" in source
    assert "chat2api-test-dictation.mp3" in source
    assert "gpt-dictation" in source
    assert '["text", "vision", "file", "image_generation", "voice_generation", "voice_conversation", "dictation"]' in source


def test_audio_router_prefers_voice_v2_and_dictation_v3() -> None:
    root = Path(__file__).resolve().parents[1]
    routing = (root / "chrome_extension" / "audio_routing_v2.js").read_text(encoding="utf-8")
    voice_v2 = (root / "chrome_extension" / "content_voice_v2.js").read_text(encoding="utf-8")
    dictation_v3 = (root / "chrome_extension" / "content_dictation_v3.js").read_text(encoding="utf-8")
    main_v2 = (root / "chrome_extension" / "voice_main_v2.js").read_text(encoding="utf-8")
    assert "chat2api.voice.request.v2" in routing
    assert "chat2api.dictation.request.v3" in routing
    assert "content_voice_v2.js" in routing
    assert "content_dictation_v3.js" in routing
    assert "ui-ready" in voice_v2
    assert "prompt-sent" in voice_v2
    assert "input-started" in voice_v2
    assert "remote-track" in voice_v2
    assert voice_v2.index("await openVoice(active)") < voice_v2.index("await sendTypedPrompt(active, prompt)")
    assert "听写" in dictation_v3
    assert "voice.mic.synthetic" in dictation_v3
    assert "voice.input.play" in dictation_v3
    assert "voice.mic.stop" in dictation_v3
    assert "voice.mic.stopped" in main_v2
