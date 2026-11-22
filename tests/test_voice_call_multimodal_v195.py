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
        own_pdf = client.post("/v1/files", headers={"Authorization": f"Bearer {token}"},
                              json={"filename": "frame-not-image.pdf", "mime_type": "application/pdf",
                                    "purpose": "assistants",
                                    "data_base64": base64.b64encode(b"fake-pdf").decode()}).json()["id"]
        extra_files = [
            client.post("/v1/files", headers={"Authorization": f"Bearer {token}"},
                        json={"filename": f"extra-{index}.png", "mime_type": "image/png",
                              "data_base64": base64.b64encode(f"image-{index}".encode()).decode()}).json()["id"]
            for index in range(5)
        ]
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
                assert "input_video.frame" in ready["supports"]
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
                               "live_event": "input.text.sent", "item_id": "media195",
                               "text": "看看这张图，用语音告诉我"})
                assert live.receive_json()["type"] == "input_text.sent"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.media.sent", "item_id": "media195",
                               "attachment_count": 999, "text": "看看这张图，用语音告诉我"})
                assert live.receive_json()["type"] == "input_media.sent"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.media.sent", "item_id": "media195",
                               "attachment_count": 999, "text": "看看这张图，用语音告诉我"})
                assert live.receive_json()["type"] == "input_media.sent"

                # A video track is sampled by the client into timestamped,
                # owner-scoped JPEG/PNG/WebP uploads. Each frame travels as an
                # image turn in this same Voice session, never as a fake MP4.
                live.send_json({
                    "type": "input_video.frame",
                    "item_id": "video-frame-000",
                    "sequence": 0,
                    "timestamp_ms": 1000,
                    "file_id": extra_files[1],
                    "text": "观察连续画面",
                })
                video_dispatch = ext.receive_json()
                assert video_dispatch["type"] == "voice.live.multimodal"
                assert video_dispatch["attachments"][0]["file_id"] == extra_files[1]
                assert "视频帧 0 · 1.000s" in video_dispatch["text"]
                video_queued = live.receive_json()
                assert video_queued["type"] == "input_video.frame.queued"
                assert video_queued["sequence"] == 0
                assert video_queued["timestamp_ms"] == 1000
                ext.send_json({
                    "type": "image.progress", "kind": "voice-live",
                    "request_id": request_id, "live_event": "input.text.sent",
                    "item_id": "video-frame-000", "text": video_dispatch["text"],
                })
                assert live.receive_json()["type"] == "input_text.sent"
                ext.send_json({
                    "type": "image.progress", "kind": "voice-live",
                    "request_id": request_id, "live_event": "input.media.sent",
                    "item_id": "video-frame-000", "attachment_count": 999,
                    "text": video_dispatch["text"],
                })
                video_sent = live.receive_json()
                assert video_sent["type"] == "input_video.frame.sent"
                assert video_sent["sequence"] == 0
                assert video_sent["timestamp_ms"] == 1000
                assert video_sent["attachment_count"] == 1

                # Duplicate/out-of-order video frame metadata is rejected
                # before a Worker dispatch.
                live.send_json({
                    "type": "input_video.frame",
                    "sequence": 0,
                    "timestamp_ms": 1000,
                    "file_id": extra_files[2],
                })
                bad_video = live.receive_json()
                assert bad_video["type"] == "error"
                assert bad_video["code"] == "INVALID_VIDEO_FRAME"
                live.send_json({
                    "type": "input_video.frame",
                    "sequence": 1,
                    "timestamp_ms": 999,
                    "file_id": extra_files[2],
                })
                out_of_order_video = live.receive_json()
                assert out_of_order_video["type"] == "error"
                assert out_of_order_video["code"] == "INVALID_VIDEO_FRAME"
                live.send_json({
                    "type": "input_video.frame",
                    "sequence": 1,
                    "timestamp_ms": 2000,
                    "file_id": own_pdf,
                })
                non_image_video = live.receive_json()
                assert non_image_video["type"] == "error"
                assert non_image_video["code"] == "INVALID_MEDIA_INPUT"
                # Invalid cross-key file must produce a per-input error, NOT
                # a voice.live.stop or WebSocket disconnect.
                live.send_json({"type": "input_media", "text": "私密文件", "attachments": [
                    {"type": "input_file", "file_id": file_b},
                ]})
                denied = live.receive_json()
                assert denied["type"] == "error"
                assert denied["code"] == "MEDIA_ACCESS_DENIED"
                live.send_json({
                    "type": "input_media", "text": "too many files",
                    "attachments": [{"type": "input_file", "file_id": file_id} for file_id in extra_files],
                })
                too_many = live.receive_json()
                assert too_many["type"] == "error"
                assert too_many["code"] == "INVALID_MEDIA_INPUT"
                live.send_json({
                    "type": "input_media", "text": "这张图片发送失败",
                    "item_id": "failed-media",
                    "attachments": [{"type": "input_image", "file_id": extra_files[0]}],
                })
                failed_media_dispatch = ext.receive_json()
                assert failed_media_dispatch["type"] == "voice.live.multimodal"
                assert live.receive_json()["type"] == "input_media.queued"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.text.sent", "item_id": "failed-media",
                               "text": "这张图片发送失败"})
                assert live.receive_json()["type"] == "input_text.sent"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.error", "item_id": "failed-media",
                               "message": "simulated attachment send failure"})
                failed_media = live.receive_json()
                assert failed_media["type"] == "error"
                assert failed_media["code"] == "VOICE_CALL_INPUT_FAILED"

                live.send_json({"type": "input_text", "text": "失败后重试", "item_id": "retry-item"})
                failed_dispatch = ext.receive_json()
                assert failed_dispatch["type"] == "voice.live.text"
                assert live.receive_json()["type"] == "input_text.queued"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.error", "item_id": "retry-item",
                               "message": "simulated composer failure"})
                failed_input = live.receive_json()
                assert failed_input["type"] == "error"
                assert failed_input["code"] == "VOICE_CALL_INPUT_FAILED"

                # The same item ID is reusable after an explicit Worker failure.
                live.send_json({"type": "input_text", "text": "重试成功", "item_id": "retry-item"})
                retry_dispatch = ext.receive_json()
                assert retry_dispatch["type"] == "voice.live.text"
                assert live.receive_json()["type"] == "input_text.queued"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.text.sent", "item_id": "retry-item", "text": "重试成功"})
                assert live.receive_json()["type"] == "input_text.sent"

                live.send_json({"type": "input_text", "text": "通话仍然在吗？", "item_id": "followup"})
                after = ext.receive_json()
                assert after["type"] == "voice.live.text"
                assert after["text"] == "通话仍然在吗？"
                assert live.receive_json()["type"] == "input_text.queued"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.text.sent", "item_id": "followup", "text": "通话仍然在吗？"})
                assert live.receive_json()["type"] == "input_text.sent"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "response.audio.delta", "response_id": "r195",
                               "pcm_base64": base64.b64encode(b"\x01\x02" * 800).decode()})
                assert live.receive_bytes() == b"\x01\x02" * 800
                live.send_json({"type": "session.finish"})
                no_response = live.receive_json()
                assert no_response["type"] == "error"
                assert no_response["code"] == "GPT_LIVE_NO_COMPLETED_RESPONSE"
                closed = live.receive_json()
                assert closed["type"] == "session.closed"
                assert closed["status"] == "error"
                assert ext.receive_json()["type"] == "voice.live.stop"
        assert not app.state.broker.requests
        row = app.state.telemetry.get(request_id)
        assert row and row["request_type"] == "voice_call"
        assert row["diagnostics"]["attachments_confirmed"] == 2
        assert row["diagnostics"]["input_confirmed_turns"] == 5
        assert row["input_file_ids"] == [file_a, extra_files[1]]
        assert row["diagnostics"]["audio_bytes"] == 1600
