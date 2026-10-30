import asyncio
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

def test_native_sidecar_is_authenticated_and_independent_of_terminal(tmp_path: Path):
    app = create_app(Settings(CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair", CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        rid = "req_native12345678"
        async def record():
            await app.state.telemetry.upsert({
                "request_id": rid, "api_key_id": "master", "status": "completed",
                "response_media": [{"url": "https://media.example.com/api/media/med_12345678/tokenABC12345678"}],
            })
        asyncio.run(record())
        assert app.state.native_content.publish(rid, "master", {
            "version": 1, "revision": 1,
            "root": {"tag": "div", "children": [
                {"tag": "p", "text": "Hello"}, {"tag": "img", "media_index": 0},
            ]}})
        assert client.get(f"/v1/native-content/{rid}").status_code == 401
        auth = {"Authorization": "Bearer test-key"}
        out = client.get(f"/v1/native-content/{rid}", headers=auth)
        assert out.status_code == 200
        assert out.json()["root"]["children"][1] == {"tag": "img", "media_index": 0}
        assert out.json()["assets"] == [
            "https://media.example.com/api/media/med_12345678/tokenABC12345678"]
        stream = client.get(f"/v1/native-content/{rid}/events", headers=auth)
        assert stream.status_code == 200
        assert "event: native.snapshot" in stream.text
        assert "event: native.end" in stream.text
        assert app.state.telemetry.get(rid)["status"] == "completed"
