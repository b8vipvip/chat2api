"""Playground Voice-call must exercise the actual WebSocket/Worker stream."""
from __future__ import annotations

import base64
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.voice_patch import install_voice_patch
from app.live_voice_patch import install_live_voice_patch
from app.playground_lifecycle_patch import install_playground_lifecycle_patch


def test_playground_voice_call_uses_real_ws_and_multimodal_turn(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    ))
    install_voice_patch(app)
    install_live_voice_patch(app)
    install_playground_lifecycle_patch(app)
    headers = {"Authorization": "Bearer master-key"}
    with TestClient(app) as client:
        k = client.post("/api/admin/keys", headers=headers, json={"name": "call-probe"}).json()
        reg = client.post("/api/extensions/register", headers={"X-Pairing-Code": "pair-code"},
            json={"name": "Chrome", "version": "0.22.182", "metadata": {
                "chatgpt_login_state": "ready", "chatgpt_login_composer_ready": True
            }}).json()
        key_id = k["key"]["key_id"]
        cid, secret = reg["client_id"], reg["token"]
        app.state.registry.clients[cid].metadata.update({
            "chatgpt_login_state": "ready", "chatgpt_login_composer_ready": True
        })
        with client.websocket_connect(f"/ws/extensions/{cid}?token={secret}") as ext:
            ext.receive_json()
            result = client.post("/api/admin/playground/runs", headers=headers, json={
                "test_type": "voice_call", "model": "gpt-6",
                "api_key_id": key_id,
                "voice_test_text": "请用语音描述附件图片",
                "files": [{"filename": "photo.png", "mime_type": "image/png",
                           "data_base64": base64.b64encode(b"fake test raster").decode()}],
            })
            assert result.status_code == 200, result.text
            run = result.json()["run"]
            assert run["request_id"].startswith("voicereq_")
            started = ext.receive_json()
            assert started["type"] == "voice.live.start"
            assert started["request_id"] == run["request_id"]
            ext.send_json({"type": "image.progress", "kind": "voice-live",
                           "request_id": started["request_id"],
                           "live_event": "session.ready"})
            multimodal = ext.receive_json()
            assert multimodal["type"] == "voice.live.multimodal"
            assert multimodal["text"] == "请用语音描述附件图片"
            assert len(multimodal["attachments"]) == 1
            ext.send_json({"type": "image.progress", "kind": "voice-live",
                           "request_id": started["request_id"],
                           "live_event": "input.media.sent",
                           "item_id": multimodal["item_id"], "attachment_count": 1})
            rid = "call-probe-response"
            for event in [
                {"live_event": "response.created", "response_id": rid},
                {"live_event": "response.audio.started", "response_id": rid},
                {"live_event": "response.audio.delta", "response_id": rid,
                 "pcm_base64": base64.b64encode(b"\x01\x02" * 600).decode()},
                {"live_event": "response.audio.done", "response_id": rid},
                {"live_event": "response.done", "response_id": rid, "text": "图像描述"},
            ]:
                ext.send_json({"type": "image.progress", "kind": "voice-live",
                               "request_id": started["request_id"], **event})
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                saved = client.get("/api/admin/tests/" + run["run_id"], headers=headers).json()
                if saved["status"] in {"warning", "passed", "failed"}:
                    break
                time.sleep(.02)
            assert saved["status"] == "warning", saved
            row = saved["results"][0]
            assert row["kind"] == "voice_call"
            assert row["audio_bytes"] == 1200
            assert row["attachments_sent"] == 1
            assert row["media_confirmed"] is True
            assert row["input_pcm_bytes"] == 0
        history = app.state.telemetry.get(run["request_id"])
        assert history and history["request_type"] == "voice_call"
        assert history["diagnostics"]["audio_bytes"] == 1200
        assert not app.state.broker.requests
