"""Speech preset + full-duplex multimodal call contract (browser UI is tested separately)."""
from __future__ import annotations

import base64
import struct
import time
import zlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
import app.live_voice_patch as live_voice_patch_module
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


def _tiny_png() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x00\x00\x00\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


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
        video_frame_file = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "camera-frame.png",
                "mime_type": "image/png",
                "purpose": "vision",
                "data_base64": base64.b64encode(_tiny_png()).decode(),
            },
        ).json()["id"]
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
                # Text acknowledgement arrives before the media acknowledgement.
                # Reusing the same item ID must not overwrite the still-pending
                # file grants or confuse the eventual media.sent correlation.
                live.send_json({
                    "type": "input_media",
                    "item_id": "media195",
                    "text": "duplicate item",
                    "attachments": [{"type": "input_image", "file_id": file_a}],
                })
                duplicate_item = live.receive_json()
                assert duplicate_item["type"] == "error"
                assert duplicate_item["code"] == "DUPLICATE_INPUT_ITEM_ID"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.media.sent", "item_id": "media195",
                               "attachment_count": 999, "text": "看看这张图，用语音告诉我"})
                first_media_ack = live.receive_json()
                assert first_media_ack["type"] == "input_media.sent"
                assert first_media_ack["attachment_count"] == 1
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "input.media.sent", "item_id": "media195",
                               "attachment_count": "not-a-number", "text": "看看这张图，用语音告诉我"})
                duplicate_media_ack = live.receive_json()
                assert duplicate_media_ack["type"] == "input_media.sent"
                assert duplicate_media_ack["attachment_count"] == 0

                # The client sends timestamped, owner-scoped image frames;
                # the Worker converts them into a synthetic camera MediaStreamTrack
                # instead of creating a separate ChatGPT composer turn.
                live.send_json({
                    "type": "input_video.frame",
                    "item_id": "video-frame-000",
                    "sequence": 0,
                    "timestamp_ms": 1000,
                    "file_id": video_frame_file,
                    "text": "观察连续画面",
                })
                video_dispatch = ext.receive_json()
                assert video_dispatch["type"] == "voice.live.multimodal"
                assert video_dispatch["attachments"][0]["file_id"] == video_frame_file
                assert video_dispatch["video_frame"] == {"sequence": 0, "timestamp_ms": 1000}
                assert "视频帧 0 · 1.000s" in video_dispatch["text"]
                video_queued = live.receive_json()
                assert video_queued["type"] == "input_video.frame.queued"
                assert video_queued["sequence"] == 0
                assert video_queued["timestamp_ms"] == 1000
                live.send_json({
                    "type": "input_video.frame",
                    "sequence": 1,
                    "timestamp_ms": 2000,
                    "file_id": extra_files[2],
                })
                backpressure = live.receive_json()
                assert backpressure["type"] == "error"
                assert backpressure["code"] == "LIVE_VIDEO_FRAME_BACKPRESSURE"
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
                    "video_track": True, "video_track_mode": "web_rtc",
                    "sequence": 0, "timestamp_ms": 1000,
                    "text": video_dispatch["text"],
                })
                video_sent = live.receive_json()
                assert video_sent["type"] == "input_video.frame.sent"
                assert video_sent["sequence"] == 0
                assert video_sent["timestamp_ms"] == 1000
                assert video_sent["attachment_count"] == 1
                assert video_sent["video_track"] is True
                assert video_sent["video_track_mode"] == "web_rtc"

                # Duplicate/out-of-order video frame metadata is rejected
                # before a Worker dispatch.
                live.send_json({
                    "type": "input_video.frame",
                    "item_id": "bad-video-sequence",
                    "sequence": 0,
                    "timestamp_ms": 1000,
                    "file_id": extra_files[2],
                })
                bad_video = live.receive_json()
                assert bad_video["type"] == "error"
                assert bad_video["code"] == "INVALID_VIDEO_FRAME"
                assert bad_video["item_id"] == "bad-video-sequence", (
                    "video validation failures must release the matching browser pending-frame slot"
                )
                live.send_json({
                    "type": "input_video.frame",
                    "sequence": 1,
                    "timestamp_ms": 999,
                    "file_id": extra_files[2],
                })
                out_of_order_video = live.receive_json()
                assert out_of_order_video["type"] == "error"
                assert out_of_order_video["code"] == "INVALID_VIDEO_FRAME"
                time.sleep(0.21)  # Respect the server's five-FPS ceiling before validating image bytes.
                live.send_json({
                    "type": "input_video.frame",
                    "sequence": 1,
                    "timestamp_ms": 2000,
                    "file_id": extra_files[2],
                })
                fake_raster = live.receive_json()
                assert fake_raster["type"] == "error"
                assert fake_raster["code"] == "INVALID_MEDIA_INPUT"
                time.sleep(0.21)
                live.send_json({
                    "type": "input_video.frame",
                    "sequence": 1,
                    "timestamp_ms": 2000,
                    "file_id": own_pdf,
                })
                non_image_video = live.receive_json()
                assert non_image_video["type"] == "error"
                assert non_image_video["code"] == "INVALID_MEDIA_INPUT"

                # A Worker-side failure must release the in-flight frame slot,
                # return the frame identity to the caller, and allow a later
                # monotonically increasing frame to be queued.
                time.sleep(0.21)
                live.send_json({
                    "type": "input_video.frame",
                    "item_id": "failed-video-frame",
                    "sequence": 1,
                    "timestamp_ms": 2000,
                    "file_id": video_frame_file,
                })
                failed_video_dispatch = ext.receive_json()
                assert failed_video_dispatch["type"] == "voice.live.multimodal"
                assert live.receive_json()["type"] == "input_video.frame.queued"
                ext.send_json({
                    "type": "image.progress", "kind": "voice-live",
                    "request_id": request_id, "live_event": "input.error",
                    "item_id": "failed-video-frame",
                    "message": "simulated camera frame send failure",
                })
                failed_video = live.receive_json()
                assert failed_video["type"] == "error"
                assert failed_video["code"] == "VOICE_CALL_INPUT_FAILED"
                assert failed_video["video_sequence"] == 1
                assert failed_video["timestamp_ms"] == 2000

                time.sleep(0.21)
                live.send_json({
                    "type": "input_video.frame",
                    "item_id": "retry-video-frame",
                    "sequence": 2,
                    "timestamp_ms": 2200,
                    "file_id": video_frame_file,
                })
                retry_video_dispatch = ext.receive_json()
                assert retry_video_dispatch["type"] == "voice.live.multimodal"
                retry_video_queued = live.receive_json()
                assert retry_video_queued["type"] == "input_video.frame.queued"
                assert retry_video_queued["sequence"] == 2
                # Leave the retry unconfirmed and explicitly fail it to keep
                # the test focused on queue release, not media accounting.
                ext.send_json({
                    "type": "image.progress", "kind": "voice-live",
                    "request_id": request_id, "live_event": "input.error",
                    "item_id": "retry-video-frame",
                    "message": "simulated retry frame failure",
                })
                retry_video_failed = live.receive_json()
                assert retry_video_failed["type"] == "error"
                assert retry_video_failed["video_sequence"] == 2

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
                               "live_event": "response.created", "response_id": "r195"})
                assert live.receive_json() == {"type": "response.created", "response_id": "r195"}
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "response.text.snapshot", "response_id": "r195",
                               "text": "这是一段尚未完成的回答"})
                partial_delta = live.receive_json()
                assert partial_delta["type"] == "response.text.delta"
                assert partial_delta["delta"] == "这是一段尚未完成的回答"
                ext.send_json({"type": "image.progress", "kind": "voice-live", "request_id": request_id,
                               "live_event": "response.audio.delta", "response_id": "r195",
                               "pcm_base64": base64.b64encode(b"\x01\x02" * 800).decode()})
                assert live.receive_bytes() == b"\x01\x02" * 800
                live.send_json({"type": "session.finish"})
                # Graceful shutdown drains the Worker before the Broker closes.
                stopped = ext.receive_json()
                assert stopped["type"] == "voice.live.stop"
                assert stopped["request_id"] == request_id
                ext.send_json({"type": "image.progress", "kind": "voice-live",
                               "request_id": request_id, "live_event": "response.interrupted",
                               "response_id": "r195", "reason": "session_stop",
                               "text": "这是一段尚未完成的回答"})
                interrupted = live.receive_json()
                assert interrupted == {
                    "type": "response.interrupted", "response_id": "r195",
                    "reason": "session_stop", "text": "这是一段尚未完成的回答",
                }
                ext.send_json({"type": "image.progress", "kind": "voice-live",
                               "request_id": request_id, "live_event": "session.closed"})
                no_response = live.receive_json()
                assert no_response["type"] == "error"
                assert no_response["code"] == "GPT_LIVE_NO_COMPLETED_RESPONSE"
                closed = live.receive_json()
                assert closed["type"] == "session.closed"
                assert closed["status"] == "error"
        assert not app.state.broker.requests
        row = app.state.telemetry.get(request_id)
        assert row and row["request_type"] == "voice_call"
        assert row["diagnostics"]["attachments_confirmed"] == 2
        assert row["diagnostics"]["input_confirmed_turns"] == 5
        assert row["input_file_ids"] == [file_a, video_frame_file]
        assert row["diagnostics"]["audio_bytes"] == 1600

def test_live_video_frame_ack_timeout_releases_slot_and_unconfirmed_grant(tmp_path, monkeypatch):
    monkeypatch.setattr(live_voice_patch_module, "LIVE_VIDEO_FRAME_ACK_TIMEOUT_SECONDS", 0.05)
    app = app_live(tmp_path)
    with TestClient(app) as client:
        token = issue_key(client, "video-timeout-caller")
        ext_id, secret = pair(client)
        frame_id = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "timeout-frame.png",
                "mime_type": "image/png",
                "purpose": "vision",
                "data_base64": base64.b64encode(_tiny_png()).decode(),
            },
        ).json()["id"]
        request_id = "voicereq_videoacktimeout"
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ext:
            ext.receive_json()
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={ext_id}",
                headers={"Authorization": f"Bearer {token}"},
            ) as live:
                live.send_json({"type": "session.start", "model": "gpt-live", "request_id": request_id})
                assert ext.receive_json()["request_id"] == request_id
                ext.send_json({
                    "type": "image.progress", "kind": "voice-live",
                    "request_id": request_id, "live_event": "session.ready",
                })
                assert live.receive_json()["type"] == "session.ready"

                live.send_json({
                    "type": "input_video.frame", "item_id": "frame-timeout",
                    "sequence": 0, "timestamp_ms": 1000,
                    "file_id": frame_id, "text": "观察画面",
                })
                first_dispatch = ext.receive_json()
                assert first_dispatch["type"] == "voice.live.multimodal"
                assert live.receive_json()["type"] == "input_video.frame.queued"

                timeout = live.receive_json()
                assert timeout["type"] == "error"
                assert timeout["code"] == "LIVE_VIDEO_FRAME_ACK_TIMEOUT"
                assert timeout["item_id"] == "frame-timeout"
                assert timeout["video_sequence"] == 0
                assert timeout["timestamp_ms"] == 1000

                # The timed-out frame's pending slot and unconfirmed grant must
                # be released so a later monotonically increasing frame can proceed.
                time.sleep(0.21)
                live.send_json({
                    "type": "input_video.frame", "item_id": "frame-after-timeout",
                    "sequence": 1, "timestamp_ms": 1200,
                    "file_id": frame_id, "text": "观察下一帧",
                })
                second_dispatch = ext.receive_json()
                assert second_dispatch["type"] == "voice.live.multimodal"
                assert live.receive_json()["type"] == "input_video.frame.queued"
                ext.send_json({
                    "type": "image.progress", "kind": "voice-live",
                    "request_id": request_id, "live_event": "input.media.sent",
                    "item_id": "frame-after-timeout", "attachment_count": 1,
                    "text": second_dispatch["text"],
                })
                ack = live.receive_json()
                assert ack["type"] == "input_video.frame.sent"
                assert ack["sequence"] == 1

                ext.send_json({
                    "type": "image.progress", "kind": "voice-live",
                    "request_id": request_id, "live_event": "response.done",
                    "response_id": "video-timeout-response", "text": "已看清第二帧",
                })
                assert live.receive_json()["type"] == "response.done"
                live.send_json({"type": "session.finish"})
                assert ext.receive_json()["type"] == "voice.live.stop"

        row = app.state.telemetry.get(request_id)
        assert row["status"] == "completed"
        assert row["input_file_ids"] == [frame_id]
        assert row["diagnostics"]["attachments_confirmed"] == 1



def test_live_video_frame_budget_is_bounded_per_session(tmp_path, monkeypatch):
    assert live_voice_patch_module.MAX_LIVE_VIDEO_FRAMES == 300
    assert live_voice_patch_module.MAX_LIVE_VIDEO_TOTAL_BYTES == 64 * 1024 * 1024
    assert live_voice_patch_module.MIN_LIVE_VIDEO_FRAME_INTERVAL_SECONDS == 0.2
    assert live_voice_patch_module._live_session_media_file_limit(0, False) == 64
    assert live_voice_patch_module._live_session_media_file_limit(0, True) == 65
    assert live_voice_patch_module._live_session_media_file_limit(299, True) == 364
    assert live_voice_patch_module._live_session_media_file_limit(300, False) == 364
    monkeypatch.setattr(live_voice_patch_module, "MAX_LIVE_VIDEO_TOTAL_BYTES", 1)

    with TestClient(app_live(tmp_path)) as client:
        token = issue_key(client, "video-budget-caller")
        ext_id, secret = pair(client)
        frame_id = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "budget-frame.png",
                "mime_type": "image/png",
                "purpose": "vision",
                "data_base64": base64.b64encode(_tiny_png()).decode(),
            },
        ).json()["id"]
        request_id = "voicereq_videobudget"
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ext:
            assert ext.receive_json()["type"] == "server.hello"
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={ext_id}",
                headers={"Authorization": f"Bearer {token}"},
            ) as live:
                live.send_json({"type": "session.start", "model": "gpt-live", "request_id": request_id})
                assert ext.receive_json()["request_id"] == request_id
                ext.send_json({
                    "type": "image.progress",
                    "kind": "voice-live",
                    "request_id": request_id,
                    "live_event": "session.ready",
                })
                assert live.receive_json()["type"] == "session.ready"

                live.send_json({
                    "type": "input_video.frame",
                    "item_id": "frame-over-budget",
                    "sequence": 0,
                    "timestamp_ms": 1000,
                    "file_id": frame_id,
                    "text": "观察实时画面",
                })
                rejected = live.receive_json()
                assert rejected["type"] == "error"
                assert rejected["code"] == "INVALID_MEDIA_INPUT"
                assert rejected["item_id"] == "frame-over-budget"
                assert "per session" in rejected["message"]
                live.send_json({"type": "session.finish"})
                assert ext.receive_json()["type"] == "voice.live.stop"
                ext.send_json({
                    "type": "image.progress",
                    "kind": "voice-live",
                    "request_id": request_id,
                    "live_event": "session.closed",
                    "status": "cancelled",
                })
                closed = live.receive_json()
                assert closed["type"] == "session.closed"
                assert closed["status"] == "cancelled"
