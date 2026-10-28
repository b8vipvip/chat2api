"""Real WebSocket contract: Worker image.error must not be silently swallowed."""
from pathlib import Path
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.voice_patch import install_voice_patch
from app.live_voice_patch import install_live_voice_patch


def test_live_voice_browser_start_error_propagates_and_is_recorded(tmp_path: Path):
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
    with TestClient(app) as client:
        paired = client.post("/api/extensions/register",
            headers={"X-Pairing-Code":"pair-code"},
            json={"name":"Chrome","version":"0.22.183",
                  "metadata":{"chatgpt_login_state":"ready",
                              "chatgpt_login_composer_ready": True}}).json()
        cid, token = paired["client_id"],paired["token"]
        business_key = client.post(
            "/api/admin/keys", headers={"Authorization":"Bearer master-key"},
            json={"name":"voice-browser-error-regression"},
        ).json()["token"]
        app.state.registry.clients[cid].metadata.update({
            "chatgpt_login_state":"ready", "chatgpt_login_composer_ready":True,
        })
        request_id="voicereq_browserstart184"
        with client.websocket_connect(f"/ws/extensions/{cid}?token={token}") as ext:
            ext.receive_json()
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={cid}",
                headers={"Authorization":f"Bearer {business_key}"},
            ) as live:
                live.send_json({"type":"session.start","model":"gpt-live","request_id":request_id})
                started = ext.receive_json()
                assert started["type"] == "voice.live.start"
                ext.send_json({"type":"image.error", "kind":"voice-live",
                               "request_id":request_id,
                               "error":"VOICE_PAGE_HIDDEN: window was not foreground"})
                failed = live.receive_json()
                assert failed["type"] == "error"
                assert failed["code"] == "GPT_LIVE_BROWSER_ERROR"
                assert "VOICE_PAGE_HIDDEN" in failed["message"]
            stopped = ext.receive_json()
            assert stopped["type"] == "voice.live.stop"
        row=app.state.telemetry.get(request_id)
        assert row["status"] == "error"
        assert "VOICE_PAGE_HIDDEN" in row["error"]
        assert "VOICE_PAGE_HIDDEN" in row["diagnostics"]["browser_startup_error"]
        assert row["diagnostics"]["session_ready"] is False
        assert row["audio_bytes"] == 0
        assert not app.state.broker.requests

def test_live_browser_error_reclaims_without_client_finish_even_after_audio(tmp_path: Path):
    """Fatal Worker errors win over earlier audio and close the original lease.

    The client deliberately does NOT send session.finish or disconnect until
    the browser's stop instruction is received. Previously the server could
    strand the call at websocket.receive(), and mark it completed after audio.
    """
    import base64

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
    with TestClient(app) as client:
        paired = client.post("/api/extensions/register",
            headers={"X-Pairing-Code": "pair-code"},
            json={"name": "Chrome", "version": "0.22.232",
                  "metadata": {"chatgpt_login_state": "ready",
                               "chatgpt_login_composer_ready": True}}).json()
        cid, token = paired["client_id"], paired["token"]
        key = client.post(
            "/api/admin/keys", headers={"Authorization": "Bearer master-key"},
            json={"name": "voice-fatal-reclaim"},
        ).json()["token"]
        app.state.registry.clients[cid].metadata.update({
            "chatgpt_login_state": "ready", "chatgpt_login_composer_ready": True,
        })
        request_id = "voicereq_fatal_reclaim_v232"
        with client.websocket_connect(f"/ws/extensions/{cid}?token={token}") as ext:
            ext.receive_json()
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={cid}",
                headers={"Authorization": f"Bearer {key}"},
            ) as live:
                live.send_json({"type": "session.start", "model": "gpt-live",
                                "request_id": request_id})
                assert ext.receive_json()["type"] == "voice.live.start"
                ext.send_json({"type": "image.progress", "kind": "voice-live",
                               "request_id": request_id, "live_event": "session.ready"})
                assert live.receive_json()["type"] == "session.ready"
                ext.send_json({"type": "image.progress", "kind": "voice-live",
                               "request_id": request_id, "live_event": "response.audio.delta",
                               "pcm_base64": base64.b64encode(b"\x01\x02" * 64).decode()})
                assert live.receive_bytes() == b"\x01\x02" * 64
                ext.send_json({"type": "image.error", "kind": "voice-live",
                               "request_id": request_id, "error": "WEBRTC_FAILED_after_audio"})
                error = live.receive_json()
                assert error["type"] == "error" and error["code"] == "GPT_LIVE_BROWSER_ERROR"
                stopped = ext.receive_json()
                assert stopped["type"] == "voice.live.stop"
                assert stopped["request_id"] == request_id
        row = app.state.telemetry.get(request_id)
        assert row["status"] == "error"
        assert row["error"] == "WEBRTC_FAILED_after_audio"
        assert row["audio_bytes"] > 0
        assert not app.state.broker.requests


def test_live_remote_closed_reclaims_lease_without_client_finish(tmp_path: Path):
    """A normal remote session.closed must release the lease without a
    client timeout or another websocket input frame."""
    app = create_app(Settings(
        CHAT2API_API_KEY="master-key", CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_DATA_DIR=tmp_path, CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
    ))
    install_voice_patch(app)
    install_live_voice_patch(app)
    with TestClient(app) as client:
        paired = client.post("/api/extensions/register",
            headers={"X-Pairing-Code": "pair-code"},
            json={"name": "Chrome", "version": "0.22.232",
                  "metadata": {"chatgpt_login_state": "ready",
                               "chatgpt_login_composer_ready": True}}).json()
        cid, token = paired["client_id"], paired["token"]
        key = client.post("/api/admin/keys",
                          headers={"Authorization": "Bearer master-key"},
                          json={"name": "voice-remote-closed"}).json()["token"]
        app.state.registry.clients[cid].metadata.update({
            "chatgpt_login_state": "ready", "chatgpt_login_composer_ready": True,
        })
        request_id = "voicereq_remote_closed_v232"
        with client.websocket_connect(f"/ws/extensions/{cid}?token={token}") as ext:
            ext.receive_json()
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={cid}",
                headers={"Authorization": f"Bearer {key}"},
            ) as live:
                live.send_json({"type": "session.start", "model": "gpt-live",
                                "request_id": request_id})
                assert ext.receive_json()["type"] == "voice.live.start"
                ext.send_json({"type": "image.progress", "kind": "voice-live",
                               "request_id": request_id, "live_event": "session.ready"})
                assert live.receive_json()["type"] == "session.ready"
                ext.send_json({"type": "image.progress", "kind": "voice-live",
                               "request_id": request_id, "live_event": "session.closed"})
                assert live.receive_json()["type"] == "session.closed"
                stopped = ext.receive_json()
                assert stopped["type"] == "voice.live.stop"
                assert stopped["request_id"] == request_id
        row = app.state.telemetry.get(request_id)
        assert row["status"] == "error"  # No audio or graceful client finish.
        assert not app.state.broker.requests
