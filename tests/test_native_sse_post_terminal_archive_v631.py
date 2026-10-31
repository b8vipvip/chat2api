"""Request terminal and deferred file archive state are independent."""
import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.generated_file_deferred_v247 import DeferredGeneratedFiles
from app.main import create_app


def test_native_sidecar_stream_tracks_ready_after_completed_request(tmp_path, monkeypatch):
    app = create_app(Settings(CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair", CHAT2API_DATA_DIR=tmp_path))
    rid = "req_latearchive0012345"
    pending_id = "pgen_" + "d" * 32
    token = "E" * 24
    url = f"https://files.example.com/api/generated-files/pending/{pending_id}/{token}"

    async def record():
        await app.state.telemetry.upsert({
            "request_id": rid, "api_key_id": "master", "status": "completed",
            "response_text": f"[📎 plan.xlsx]({url})",
            "response_generated_files": [{"filename": "plan.xlsx", "url": url,
                "bytes": 321, "status": "processing"}],
        })

    with TestClient(app) as client:
        asyncio.run(record())
        assert app.state.native_content.publish(rid, "master", {
            "version": 1, "revision": 4,
            "root": {"tag": "p", "text": "File delivery continues"},
        })
        polls = []

        def file_state(self, file_id, access_token):
            assert file_id == pending_id and access_token == token
            polls.append(1)
            return {"status": "processing" if len(polls) == 1 else "ready"}

        monkeypatch.setattr(DeferredGeneratedFiles, "status", file_state)
        response = client.get(f"/v1/native-content/{rid}/events",
                              headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 200
        assert len(polls) >= 2
        assert response.text.count("event: native.snapshot") == 2
        assert '"status": "processing"' in response.text
        assert '"status": "ready"' in response.text
        assert "event: native.end" in response.text
        assert "_pending_archive_pollable" not in response.text
        assert app.state.telemetry.get(rid)["status"] == "completed"
