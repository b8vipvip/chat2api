"""End-to-end contract for the real Playground speech/conversation paths.

Synthetic browser events stand in for the ChatGPT Voice UI; they do not claim
the external browser or microphone is verified. Every run traverses the public
/v1/audio endpoints, broker and telemetry instead of being silently skipped.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.voice_patch import install_voice_patch
from app.playground_lifecycle_patch import install_playground_lifecycle_patch
from app.playground_multimodal_defaults_patch import install_playground_multimodal_defaults_patch
from app.request_id_namespace_v136_patch import install_request_id_namespace_v136_patch


AUDIO = b"\\x1aE\\xdf\\xa3" + b"real-voice-test-transport" * 60


def app_voice(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    ))
    install_voice_patch(app)
    install_playground_lifecycle_patch(app)
    install_playground_multimodal_defaults_patch(app)
    install_request_id_namespace_v136_patch(app)
    return app


def admin(): return {"Authorization": "Bearer master-key"}


def connected(client):
    key = client.post("/api/admin/keys", headers=admin(), json={"name":"Voice-Test"}).json()
    pairing = client.post(
        "/api/extensions/register", headers={"X-Pairing-Code":"pair-code"},
        json={"name":"Chrome", "version":"0.22.178",
              "metadata":{"chatgpt_login_state":"ready", "chatgpt_login_composer_ready":True}},
    ).json()
    ext_id = pairing["client_id"]
    client.app.state.registry.clients[ext_id].metadata.update({
        "chatgpt_login_state":"ready", "chatgpt_login_composer_ready":True,
    })
    return key["key"]["key_id"], ext_id, pairing["token"]


def start(client, kind, key_id, files=None, model="gpt-6"):
    response = client.post("/api/admin/playground/runs", headers=admin(), json={
        "test_type":kind, "model":model, "api_key_id":key_id,
        "files":files or [],
    })
    assert response.status_code == 200, response.text
    run = response.json()["run"]
    assert run["status"] == "running"
    assert run["request_id"].startswith("voicereq_")
    assert all(value.startswith("voicereq_") for value in run["request_ids"])
    return run


def wait_run(client, run_id, expected, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        row = client.get("/api/admin/tests/" + run_id, headers=admin()).json()
        if row["status"] == expected: return row
        time.sleep(.02)
    raise AssertionError(f"run did not reach {expected}: {row}")


def complete(ws, request_id, transcript="中文语音测试成功"):
    ws.send_json({
        "type":"image.completed", "kind":"voice", "request_id":request_id,
        "voice":{
            "transcript":transcript,
            "b64_json":base64.b64encode(AUDIO).decode("ascii"),
            "mime_type":"audio/webm;codecs=opus",
            "size":len(AUDIO),
        },
    })


def test_voice_generation_is_dispatched_and_persisted_before_response(tmp_path):
    app = app_voice(tmp_path)
    with TestClient(app) as client:
        key_id, ext_id, secret = connected(client)
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ws:
            ws.receive_json()
            run = start(client,"voice_generation",key_id,model="gpt-6")
            request = ws.receive_json()
            assert request["type"] == "voice.request"
            assert request["request_id"] == run["request_id"]
            assert request["options"]["model"] == "gpt-live"
            assert request["options"]["mode"] == "speech"
            request_row = app.state.telemetry.get(run["request_id"])
            assert request_row["status"] == "running"
            assert request_row["request_type"] == "voice_generation"
            complete(ws,request["request_id"])
            done = wait_run(client,run["run_id"],"passed")
        assert done["results"][0]["status"] == "passed"
        assert done["results"][0]["effective_model"] == "gpt-live"
        assert done["results"][0]["selected_text_model"] == "gpt-6"
        assert done["results"][0]["audio_bytes"] == len(AUDIO)
        assert done["results"][0]["request_id"] == run["request_id"]
        assert app.state.telemetry.get(run["request_id"])["status"] == "completed"
        assert app.state.telemetry.get(run["request_id"])["response_text"] == "中文语音测试成功"
        assert not app.state.broker.requests


def test_voice_conversation_with_uploaded_audio_uses_one_real_dispatch(tmp_path):
    app = app_voice(tmp_path)
    with TestClient(app) as client:
        key_id, ext_id, secret = connected(client)
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ws:
            ws.receive_json()
            run = start(client,"voice_conversation",key_id,files=[{
                "filename":"question.webm", "mime_type":"audio/webm",
                "data_base64":base64.b64encode(AUDIO).decode(),
            }])
            assert len(run["request_ids"]) == 1
            msg = ws.receive_json()
            assert msg["type"] == "voice.request"
            assert msg["request_id"] == run["request_id"]
            assert msg["options"]["mode"] == "conversation"
            assert msg["audio"]["file_id"].startswith("file_")
            complete(ws,msg["request_id"],"听到了你的问题")
            done = wait_run(client,run["run_id"],"passed")
        assert done["results"][0]["sample_source"] == "administrator-upload"
        assert app.state.telemetry.get(run["request_id"])["status"] == "completed"
        assert len(run["request_ids"]) == 1
        assert not app.state.broker.requests


def test_voice_conversation_without_uploaded_audio_generates_real_sample_first(tmp_path):
    app = app_voice(tmp_path)
    with TestClient(app) as client:
        key_id, ext_id, secret = connected(client)
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ws:
            ws.receive_json()
            run = start(client,"voice_conversation",key_id)
            assert len(run["request_ids"]) == 2
            first = ws.receive_json()
            assert first["type"] == "voice.request"
            assert first["options"]["mode"] == "speech"
            assert first["request_id"] == run["request_ids"][1]
            complete(ws, first["request_id"],"准备好测试语音")
            second = ws.receive_json()
            assert second["type"] == "voice.request"
            assert second["options"]["mode"] == "conversation"
            assert second["request_id"] == run["request_id"]
            assert second["audio"]["file_id"].startswith("file_")
            setup_row = app.state.telemetry.get(first["request_id"])
            assert setup_row["status"] == "completed"
            primary_row = app.state.telemetry.get(second["request_id"])
            assert primary_row["status"] == "running"
            complete(ws,second["request_id"],"你好，已收到测试语音")
            done = wait_run(client,run["run_id"],"passed")
        assert done["results"][0]["setup_request_id"] == first["request_id"]
        assert done["results"][0]["sample_source"] == "generated-live-speech"
        assert all(app.state.telemetry.get(rid)["status"] == "completed" for rid in run["request_ids"])
        assert not app.state.broker.requests
        assert "base64" not in (tmp_path/"test_runs.jsonl").read_text()


def test_voice_failure_is_reported_not_skipped_and_preserves_request_identity(tmp_path):
    app=app_voice(tmp_path)
    with TestClient(app) as client:
        key_id, ext_id, secret = connected(client)
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ws:
            ws.receive_json()
            run = start(client,"voice_generation",key_id)
            msg=ws.receive_json()
            ws.send_json({"type":"image.error","kind":"voice","request_id":msg["request_id"],"error":"No microphone"})
            done = wait_run(client,run["run_id"],"failed")
        assert "No microphone" in done["results"][0]["message"]
        row=app.state.telemetry.get(run["request_id"])
        assert row["status"]=="error"
        assert not app.state.broker.requests


def test_voice_does_not_silently_execute_text_model_at_public_endpoint(tmp_path):
    app=app_voice(tmp_path)
    with TestClient(app) as client:
        r=client.post("/v1/audio/speech",headers=admin(),json={
            "model":"gpt-6", "input":"hello",
        })
        assert r.status_code == 400
        assert "gpt-live" in r.text


def test_voice_cancel_emits_voice_specific_cancel_not_chat_cancel(tmp_path):
    app = app_voice(tmp_path)
    with TestClient(app) as client:
        key_id, ext_id, secret = connected(client)
        with client.websocket_connect(f"/ws/extensions/{ext_id}?token={secret}") as ws:
            ws.receive_json()
            run=start(client,"voice_generation",key_id)
            request=ws.receive_json()
            cancelled=client.post(f"/api/admin/playground/runs/{run['run_id']}/cancel",headers=admin())
            assert cancelled.status_code==200
            msg=ws.receive_json()
            assert msg == {"type":"voice.cancel","request_id":request["request_id"]}
            wait_run(client,run["run_id"],"cancelled")
        assert app.state.telemetry.get(run["request_id"])["status"]=="cancelled"
        assert not app.state.broker.requests
