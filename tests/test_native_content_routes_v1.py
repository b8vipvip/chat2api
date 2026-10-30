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


def test_verified_owned_url_takes_priority_over_original_source(tmp_path: Path):
    app = create_app(Settings(CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair", CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        rid = "req_ownedmedia12345"
        async def record():
            await app.state.telemetry.upsert({
                "request_id": rid, "api_key_id": "master", "status": "completed",
                "response_media": [{
                    "url": "https://chatgpt.com/upstream-internal-image",
                    "owned_url": "https://media.example.com/api/media/med_owned12345/assetTOKEN1234567",
                }],
            })
        asyncio.run(record())
        app.state.native_content.publish(rid, "master", {
            "version": 1, "revision": 3, "root": {"tag": "img", "media_index": 0},
        })
        response = client.get(f"/v1/native-content/{rid}",
                              headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 200
        assert response.json()["assets"] == [
            "https://media.example.com/api/media/med_owned12345/assetTOKEN1234567"]


def test_media_ready_updates_same_native_revision():
    from app.native_content_v1 import native_delivery_fingerprint, native_layout_summary
    early = {"version": 1, "revision": 4,
             "root": {"tag": "div", "style": {"display": "grid"},
                      "children": [{"tag": "img", "media_index": 0}]}, "assets": []}
    ready = {**early, "assets": ["https://media.example.com/api/media/med_12345678/assetTOKEN1234567"]}
    assert native_delivery_fingerprint(early) != native_delivery_fingerprint(ready)
    assert native_delivery_fingerprint(ready) == native_delivery_fingerprint(ready.copy())
    summary = native_layout_summary(early, 0)
    assert summary["images"] == 1 and summary["grid"] == 1
    assert summary["media_mapping_complete"] is False
    assert "assetTOKEN" not in str(summary)


def test_privileged_native_layout_evidence_does_not_expose_asset_tokens(tmp_path: Path):
    app = create_app(Settings(CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair", CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        rid = "req_layout12345678"
        async def record():
            await app.state.telemetry.upsert({
                "request_id": rid, "api_key_id": "master", "status": "completed",
                "response_media": [{
                    "id": "med_12345678", "mime": "image/png",
                    "width": 512, "height": 512,
                    "url": "https://media.example.com/api/media/med_12345678/secretToken1234567",
                }],
            })
        asyncio.run(record())
        app.state.native_content.publish(rid, "master", {
            "version": 1, "revision": 5,
            "root": {"tag": "div", "style": {"display": "grid"},
                     "children": [{"tag": "img", "media_index": 0}]},
        })
        route = f"/api/admin/requests/{rid}/native-content"
        assert client.get(route).status_code == 401
        out = client.get(route, headers={"Authorization": "Bearer test-key"})
        assert out.status_code == 200
        payload = out.json()
        assert payload["layout_summary"]["media_mapping_complete"] is True
        assert payload["snapshot"]["root"]["children"][0]["media_index"] == 0
        assert payload["media_metadata"][0]["width"] == 512
        assert "secretToken" not in out.text
        assert out.headers["cache-control"] == "private, no-store"
