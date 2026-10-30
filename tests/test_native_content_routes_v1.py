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


def test_probe_zip_includes_safe_full_native_layout_snapshot(tmp_path: Path):
    import io
    import json
    import zipfile
    app = create_app(Settings(CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair", CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        rid = "req_ziprich12345678"
        async def record():
            await app.state.telemetry.upsert({
                "request_id": rid, "api_key_id": "master", "status": "completed",
            })
        asyncio.run(record())
        assert app.state.native_content.publish(rid, "master", {
            "version": 1, "revision": 2,
            "root": {"tag": "div", "style": {"display": "grid",
                     "gridTemplateColumns": "1fr 1fr 1fr"},
                     "children": [{"tag": "p", "text": "用户内容"}]},
        })
        url = f"/api/admin/requests/{rid}/probes/download"
        assert client.get(url).status_code == 401
        response = client.get(url, headers={"Authorization": "Bearer test-key"})
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "native-rich/layout-snapshot.json" in archive.namelist()
            snapshot = json.loads(archive.read("native-rich/layout-snapshot.json"))
            assert snapshot["root"]["style"]["gridTemplateColumns"] == "1fr 1fr 1fr"
            assert snapshot["root"]["children"][0]["text"] == "用户内容"
            assert "assets" not in snapshot
            assert "checksums.sha256" in archive.namelist()


def test_owned_file_manifest_and_native_stream_same_revision(tmp_path: Path):
    app = create_app(Settings(CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="test-pair", CHAT2API_DATA_DIR=tmp_path))
    with TestClient(app) as client:
        rid = "req_verifiedfiles12345"
        url = "https://files.example.com/api/generated-files/pending/pgen_" + "a" * 32 + "/" + "B" * 24
        ignored = "https://other.example.com/api/generated-files/file_123456789/token123456789"
        async def record():
            await app.state.telemetry.upsert({
                "request_id": rid, "api_key_id": "master", "status": "completed",
                "response_text": f"Here are the files: [📎 plan.xlsx]({url})",
                "response_generated_files": [
                    {"filename": "plan.xlsx", "url": url, "bytes": 1234,
                     "status": "processing"},
                    # Never export unlinked/stale records from the request row.
                    {"filename": "wrong.zip", "url": ignored, "bytes": 23,
                     "status": "archived"},
                    # Model-origin unsafe URLs are never file-card targets.
                    {"filename": "unsafe.json", "url": "https://evil.example/malware",
                     "bytes": 99, "status": "ready"},
                ],
            })
        asyncio.run(record())
        app.state.native_content.publish(rid, "master", {
            "version": 1, "revision": 2, "root": {"tag": "p", "text": "Here are the files:"},
        })
        auth = {"Authorization": "Bearer test-key"}
        output = client.get(f"/v1/native-content/{rid}", headers=auth)
        assert output.status_code == 200
        assert output.json()["files"] == [{
            "filename": "plan.xlsx", "url": url, "bytes": 1234, "status": "processing",
        }]
        streamed = client.get(f"/v1/native-content/{rid}/events", headers=auth)
        assert streamed.status_code == 200
        assert '"files":' in streamed.text and "plan.xlsx" in streamed.text
        assert "wrong.zip" not in streamed.text
        assert client.get(f"/v1/native-content/{rid}").status_code == 401


def test_file_manifest_changes_are_new_sse_state_without_dom_revision():
    from app.native_content_v1 import native_delivery_fingerprint
    original = {"version": 1, "revision": 7, "assets": [], "files": []}
    ready = {**original, "files": [{
        "filename": "plan.xlsx", "url": "https://example.com/api/generated-files/pending/file/token",
        "bytes": 100, "status": "processing",
    }]}
    assert native_delivery_fingerprint(original) != native_delivery_fingerprint(ready)
    assert native_delivery_fingerprint(ready) == native_delivery_fingerprint(dict(ready))
