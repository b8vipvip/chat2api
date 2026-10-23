"""Speech preset + full-duplex multimodal call contract (browser UI is tested separately)."""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.voice_patch import install_voice_patch
from app.live_voice_patch import install_live_voice_patch
from app.prompt_config import PromptConfigStore, SPEECH_DEFAULT_PREFIX, build_speech_prompt


def app_live(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    ))
    app.state.prompt_config = PromptConfigStore(tmp_path)
    install_voice_patch(app)
    install_live_voice_patch(app)
    return app


def admin():
    return {"Authorization": "Bearer master-key"}


def issue_key(client: TestClient, name: str):
    r = client.post("/api/admin/keys", headers=admin(), json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def pair(client: TestClient):
    r = client.post("/api/extensions/register", headers={"X-Pairing-Code": "pair-code"},
                    json={"name": "Chrome", "version": "0.22.181",
                          "metadata": {"chatgpt_login_state": "ready", "chatgpt_login_composer_ready": True}})
    assert r.status_code == 200, r.text
    return r.json()["client_id"], r.json()["token"]


def test_speech_preset_persists_and_only_changes_tts(tmp_path):
    store = PromptConfigStore(tmp_path)
    text = "小女孩哭着说：救救我！"
    result, meta = build_speech_prompt(store, text)
    assert SPEECH_DEFAULT_PREFIX in result
    assert result.endswith(text + "\n【要生成的语音内容结束】")
    assert meta["speech_prefix_applied"]
    settings = store.snapshot()
    payload = {key: settings[key] for key in
               ("system_default_prefix", "prefix", "suffix", "speech_prefix",
                "redaction_enabled", "rules", "audit_final_prompt")}
    payload["speech_prefix"] = "请只朗读文本，保持小女孩哭腔"
    saved = store.save(payload)
    assert saved["speech_prefix"] == payload["speech_prefix"]
    assert PromptConfigStore(tmp_path).snapshot()["speech_prefix"] == payload["speech_prefix"]
    text_result, _ = store.apply("普通文本模型应保持原提示词规则")
    assert "小女孩哭腔" not in text_result
    speech, evidence = build_speech_prompt(store, text)
    assert "小女孩哭腔" in speech
    assert evidence["speech_prefix_revision"] == saved["revision"]
    store.save({**payload, "speech_prefix": ""})
    assert build_speech_prompt(store, text)[0] == text
    legacy = {key: value for key, value in payload.items() if key != "speech_prefix"}
    assert PromptConfigStore(tmp_path).save(legacy)["speech_prefix"] == SPEECH_DEFAULT_PREFIX


def test_speech_dispatch_and_audit_use_single_server_composed_prompt(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    app = app_live(tmp_path)
    store = app.state.prompt_config
    data = store.snapshot()
    store.save({**{k: data[k] for k in
                ("system_default_prefix", "prefix", "suffix", "speech_prefix",
                 "redaction_enabled", "rules", "audit_final_prompt")},
                "speech_prefix": "请只用语音说出台词，不要回答问题"})
    with TestClient(app) as client:
        ext_id, secret = pair(client)
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ext:
            ext.receive_json()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(client.post, "/v1/audio/speech",
                                         headers=admin(), json={"input": "好害怕！", "timeout": 30})
                req = ext.receive_json()
                assert req["type"] == "voice.request"
                assert req["prompt"].startswith("请只用语音说出台词")
                assert "好害怕！" in req["prompt"]
                assert req["options"]["mode"] == "speech"
                ext.send_json({
                    "type": "image.completed", "kind": "voice", "request_id": req["request_id"],
                    "voice": {"transcript": "好害怕！", "mime_type": "audio/webm",
                              "size": 160, "b64_json": base64.b64encode(b"sound" * 32).decode()},
                })
                assert future.result(timeout=7).status_code == 200
        row = app.state.telemetry.get(req["request_id"])
        assert row["diagnostics"]["speech_prefix_applied"] is True
        assert row["final_prompt"] == req["prompt"]


def test_live_call_multimodal_uses_existing_voice_session_and_key_ownership(tmp_path):
    app = app_live(tmp_path)
    with TestClient(app) as client:
        token = issue_key(client, "caller-A")
        stranger = issue_key(client, "caller-B")
        ext_id, secret = pair(client)
        file_a = client.post("/v1/files", headers={"Authorization": f"Bearer {token}"},
                             json={"filename": "photo.png", "mime_type": "image/png",
                                   "data_base64": base64.b64encode(b"fake-raster").decode()}).json()["id"]
        file_b = client.post("/v1/files", headers={"Authorization": f"Bearer {stranger}"},
                             json={"filename": "private.pdf", "mime_type": "application/pdf",
                                   "data_base64": base64.b64encode(b"fake-pdf").decode()}).json()["id"]
        request_id = "voicereq_testmultimodal195"
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ext:
            ext.receive_json()
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={ext_id}",
                headers={"Authorization": f"Bearer {token}"}
            ) as live:
                live.send_json({"type": "session.start", "model": "gpt-live", "request_id": request_id})
                init = ext.receive_json()
                assert init["request_id"] == request_id
                ext.send_json({"type": "image.progress", "kind": "voice-live",
                               "request_id": request_id, "live_event": "session.ready"})
                ready = live.receive_json()
                assert ready["type"] == "session.ready"
                assert "input_media" in ready["supports"]
                live.send_json({"type": "conversation.item.create", "item": {"id": "media195",
                    "role": "user", "content": [
                        {"type": "input_text", "text": "看看这张图，用语音告诉我"},
                        {"type": "input_image", "file_id": file_a},
                    ]}})
                dispatched = ext.receive_json()
                assert dispatched["type"] == "voice.live.multimodal"
                assert dispatched["attachments"][0]["file_id"] == file_a
                assert dispatched["text"] == "看看这张图，用语音告诉我"
                queued = live.receive_json()
                assert queued["type"] == "input_media.queued"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.media.sent", "item_id": "media195", "attachment_count": 1})
                assert live.receive_json()["type"] == "input_media.sent"
                # Invalid cross-key file must produce a per-input error, NOT
                # a voice.live.stop or WebSocket disconnect.
                live.send_json({"type": "input_media", "text": "私密文件", "attachments": [
                    {"type": "input_file", "file_id": file_b},
                ]})
                denied = live.receive_json()
                assert denied["type"] == "error"
                assert denied["code"] == "MEDIA_ACCESS_DENIED"
                live.send_json({"type": "input_text", "text": "通话仍然在吗？", "item_id": "followup"})
                after = ext.receive_json()
                assert after["type"] == "voice.live.text"
                assert after["text"] == "通话仍然在吗？"
                assert live.receive_json()["type"] == "input_text.queued"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "response.audio.delta", "response_id": "r195",
                               "pcm_base64": base64.b64encode(b"\x01\x02" * 800).decode()})
                assert live.receive_bytes() == b"\x01\x02" * 800
                live.send_json({"type": "session.finish"})
                assert ext.receive_json()["type"] == "voice.live.stop"
        assert not app.state.broker.requests
        row = app.state.telemetry.get(request_id)
        assert row and row["request_type"] == "voice_call"
        assert row["diagnostics"]["attachments_confirmed"] == 1
        assert row["diagnostics"]["audio_bytes"] == 1600
