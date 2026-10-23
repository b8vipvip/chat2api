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
        app.state.registry.clients[cid].metadata.update({
            "chatgpt_login_state":"ready", "chatgpt_login_composer_ready":True,
        })
        request_id="voicereq_browserstart184"
        with client.websocket_connect(f"/ws/extensions/{cid}?token={token}") as ext:
            ext.receive_json()
            with client.websocket_connect(
                f"/v1/audio/realtime?client_id={cid}",
                headers={"Authorization":"Bearer master-key"},
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
