"""Time-only combined request-log export: all retained rows from the requested instant."""
import asyncio
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runtime_contract import SERVER_RUNTIME_VERSION
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.voice_patch import install_voice_patch


def headers():
    return {"Authorization": "Bearer master-key"}


def app_v8(tmp_path: Path):
    settings = Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )
    app = create_app(settings)
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    return app


def batch(client, since):
    return client.get(
        "/api/admin/requests/logs/batch",
        params={"since": since},
        headers=headers(),
    )


def test_since_only_export_preserves_single_request_reports_and_status(tmp_path: Path):
    with TestClient(app_v8(tmp_path)) as client:
        store = client.app.state.telemetry
        for ident, status, recorded in (
            ("req_old", "completed", "2026-10-29T10:39:30+08:00"),
            ("req_start", "error", "2026-10-29T10:39:31+08:00"),
            ("req_new", "running", "2026-10-29T11:00:00+08:00"),
        ):
            asyncio.run(store.upsert({
                "request_id": ident, "status": status,
                "recorded_at": recorded, "requested_model": "gpt-6",
                "final_prompt": f"customer prompt {ident}",
                "response_text": f"answer {ident}",
            }))
        assert client.get("/api/admin/requests/logs/batch?since=2026-10-29T02:39:31Z").status_code == 401
        assert client.get("/api/admin/requests/logs/batch", headers=headers()).status_code == 200

        # Naive timestamp is Beijing, not the server's or the admin browser's timezone.
        response = batch(client, "2026-10-29 10:39:31")
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/zip")
        assert response.headers["x-chat2api-exported-count"] == "2"
        assert "since" in response.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert bundle.testzip() is None
            manifest = json.loads(bundle.read("index.json"))
            assert manifest["selection_mode"] == "since"
            assert manifest["since"] == "2026-10-29T02:39:31+00:00"
            assert manifest["time_filter_field"] == "recorded_at"
            assert "requested_limit" not in manifest
            assert manifest["exported_count"] == 2
            assert [item["request_id"] for item in manifest["requests"]] == [
                "req_new", "req_start",
            ]
            for row in manifest["requests"]:
                artifact = json.loads(bundle.read(row["filename"]))
                single = client.get(
                    f"/api/admin/requests/{row['request_id']}/log",
                    headers=headers(),
                ).json()
                assert artifact["report_type"] == "chat2api-request-diagnostic"
                assert artifact["request"] == single["request"]
                assert artifact["request"]["status"] == row["status"]
                assert artifact["worker_probe_evidence"] == single["worker_probe_evidence"]
                assert artifact["playground_diagnostics"] == single["playground_diagnostics"]
                assert artifact["http_trace"] == single["http_trace"]
                assert "extension_snapshot" in artifact
                assert artifact["request"]["final_prompt"] == f"customer prompt {row['request_id']}"
                assert artifact["request"]["response_text"] == f"answer {row['request_id']}"
                assert "final_prompt" in artifact["request"]
            readme = bundle.read("README.txt").decode("utf-8")
            assert "起始时间（含）" in readme
            assert "只读" in readme
        assert store.get("req_new")["status"] == "running"

        # Explicit offset and Z are the same UTC instant as the unzoned example.
        for raw in ("2026-10-29T10:39:31+08:00", "2026-10-29T02:39:31Z"):
            explicit = batch(client, raw)
            assert explicit.status_code == 200, explicit.text
            assert explicit.headers["x-chat2api-exported-count"] == "2"


def test_filter_includes_all_retained_newer_rows_and_rejects_invalid_dates(tmp_path: Path):
    with TestClient(app_v8(tmp_path)) as client:
        assert batch(client, "2026-10-29 00:00:00").status_code == 404
        store = client.app.state.telemetry
        for i in range(25):
            asyncio.run(store.upsert({
                "request_id": f"req_{i:03d}", "status": "completed",
                "recorded_at": "2026-10-29T03:00:00+08:00",
            }))
        response = batch(client, "2026-10-29 02:59:59")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert json.loads(bundle.read("index.json"))["exported_count"] == 25

        # Old N is no longer a selection mechanism, even if a legacy caller supplies it.
        response = client.get(
            "/api/admin/requests/logs/batch",
            params={"since": "2026-10-29 02:59:59", "limit": 1},
            headers=headers(),
        )
        assert response.headers["x-chat2api-exported-count"] == "25"
        for invalid in ("bad-date", "2026-02-30 10:39:31",
                        "2026-10-29T11:28:29+15:00",
                        "2026-10-29 25:39:31", "2026-10-29"):
            assert batch(client, invalid).status_code == 422, invalid
        assert batch(client, "2030-01-01T00:00:00+08:00").status_code == 404


def test_request_history_exposes_only_pasteable_start_time_control():
    source = (Path(__file__).resolve().parents[1] / "app" /
              "request_history_v94_patch.py").read_text(encoding="utf-8")
    assert "rqBatchExportLogs" in source
    assert "rqBatchExportSince" in source
    assert "rqBatchExportCurrentVersion" in source
    assert "仅合并打包导出当前版本的请求日志" in source
    assert "query.set('current_version_only','true')" in source
    assert "requestHistoryParseExportSince(value)" in source
    assert "since.type='text'" in source
    assert "since.type='datetime-local'" not in source
    assert "rqBatchExportLimit" not in source
    assert "if(parsed)query.set('since',parsed.iso)" in source
    assert "if(hasTime && !parsed)" in source
    assert "since.disabled=versionOnly.checked" not in source
    assert "const query=requestHistoryFilterParams()" in source
    assert "合并打包导出请求日志" in source
    assert "requestHistoryDownload(" in source
    assert "rqBatchExportTimeHint" not in source
    assert "rqDownloadDiagnostics" not in source
    assert "下载诊断日志包" not in source

def test_batch_export_current_version_is_creation_version_and_legacy_is_excluded(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHAT2API_BUILD_COMMIT", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with TestClient(app_v8(tmp_path)) as client:
        store = client.app.state.telemetry
        since = "2026-10-29 00:00:00"
        for ident in ("req_current", "req_old", "req_unmarked"):
            asyncio.run(store.upsert({
                "request_id": ident,
                "recorded_at": "2026-10-29T10:39:31+08:00",
                "status": "running",
                "requested_model": "gpt-6",
            }))
        asyncio.run(store.update("req_current", {"status": "completed",
                             "request_server_version": "spoofed-version",
                             "request_server_commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}))
        assert store.get("req_current")["request_server_version"] == SERVER_RUNTIME_VERSION
        assert store.get("req_current")["request_server_commit"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

        # Older deployment and unversioned historical records are not
        # evidence of creation under this release.
        for ident, commit in (("req_old", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"), ("req_unmarked", None)):
            old = store.get(ident)
            # They deliberately share the current formal release version.
            assert old["request_server_version"] == SERVER_RUNTIME_VERSION
            if commit is None:
                old.pop("request_server_commit", None)
            else:
                old["request_server_commit"] = commit
            store._remember(old)
        asyncio.run(store.update("req_unmarked", {"status": "completed"}))
        assert "request_server_commit" not in store.get("req_unmarked")

        response = client.get("/api/admin/requests/logs/batch",
                              params={"since": since, "current_version_only": "true"},
                              headers=headers())
        assert response.status_code == 200, response.text
        assert response.headers["x-chat2api-exported-count"] == "1"
        assert response.headers["x-chat2api-version-scope"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert "current-version" in response.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            manifest = json.loads(bundle.read("index.json"))
            assert manifest["selection_mode"] == "current_version"
            assert manifest["selected_server_version"] == SERVER_RUNTIME_VERSION
            assert manifest["current_version_only"] is True
            assert manifest["request_version_field"] == "request_server_commit"
            assert manifest["selected_server_commit"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            assert [row["request_id"] for row in manifest["requests"]] == ["req_current"]
            report = json.loads(bundle.read(manifest["requests"][0]["filename"]))
            assert report["request"]["request_server_version"] == SERVER_RUNTIME_VERSION

        all_rows = batch(client, since)
        assert all_rows.headers["x-chat2api-exported-count"] == "3"
        assert all_rows.headers["x-chat2api-version-scope"] == "all"

        other = client.get("/api/admin/requests/logs/batch",
                           params={"since": "2026-10-29 10:39:32",
                                   "current_version_only": "true"},
                           headers=headers())
        # Version and a provided start timestamp are now intersected.
        assert other.status_code == 404

def test_current_version_without_since_includes_all_retained_current_rows(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHAT2API_BUILD_COMMIT", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with TestClient(app_v8(tmp_path)) as client:
        store = client.app.state.telemetry
        for name, commit in (("req_v1", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                             ("req_v2", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
                             ("req_legacy", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")):
            asyncio.run(store.upsert({
                "request_id": name, "recorded_at": "2026-10-29T07:00:00+08:00",
                "status": "completed",
            }))
            if commit != "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":
                row = store.get(name)
                row["request_server_commit"] = commit
                store._remember(row)
        response = client.get("/api/admin/requests/logs/batch",
                              params={"current_version_only": "true"},
                              headers=headers())
        assert response.status_code == 200, response.text
        assert response.headers["x-chat2api-exported-count"] == "2"
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            manifest = json.loads(bundle.read("index.json"))
            assert manifest["selection_mode"] == "current_version"
            assert manifest["since"] is None
            assert manifest["time_filter_field"] is None
        for since, expected in (("bad-date", 422), ("2030-01-01T00:00:00Z", 404)):
            response = client.get("/api/admin/requests/logs/batch",
                                  params={"current_version_only": "true", "since": since},
                                  headers=headers())
            assert response.status_code == expected
        # Without a timestamp and without a version scope, export all retained rows.
        assert client.get("/api/admin/requests/logs/batch",
                          headers=headers()).headers["x-chat2api-exported-count"] == "3"




def test_filter_parity_between_history_table_and_full_retained_zip(tmp_path: Path, monkeypatch):
    """Export all matched rows, not merely the first 100 displayed rows."""
    monkeypatch.setenv("CHAT2API_BUILD_COMMIT", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with TestClient(app_v8(tmp_path)) as client:
        store = client.app.state.telemetry
        for i in range(130):
            row = {
                "request_id": f"req_match_{i:03d}",
                "recorded_at": "2026-10-29T17:28:00+08:00",
                "api_key_name": "mv3c", "status": "completed",
                "requested_model": "gpt-6",
            }
            asyncio.run(store.upsert(row))
        for ident, time, commit, key_name in (
            ("req_older", "2026-10-29T17:27:48+08:00", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "mv3c"),
            ("req_other_key", "2026-10-29T17:28:00+08:00", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "another"),
            ("req_other_version", "2026-10-29T17:28:00+08:00", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "mv3c"),
        ):
            asyncio.run(store.upsert({
                "request_id": ident, "recorded_at": time,
                "status": "completed", "requested_model": "gpt-6",
                "api_key_name": key_name,
            }))
            if commit != "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa":
                row = store.get(ident)
                row["request_server_commit"] = commit
                store._remember(row)
        filters = {
            "q": "mv3c", "status": "completed", "model": "gpt-6",
            "current_version_only": "true", "since": "2026-10-29 17:27:49",
        }
        listed = client.get("/api/admin/requests",
                            params={**filters, "limit": 100}, headers=headers())
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 130
        assert len(listed.json()["data"]) == 100

        exported = client.get("/api/admin/requests/logs/batch",
                              params=filters, headers=headers())
        assert exported.status_code == 200, exported.text
        assert exported.headers["x-chat2api-exported-count"] == "130"
        with zipfile.ZipFile(io.BytesIO(exported.content)) as bundle:
            manifest = json.loads(bundle.read("index.json"))
            assert manifest["exported_count"] == 130
            assert manifest["current_version_only"] is True
            assert manifest["time_filter_field"] == "recorded_at"
            assert manifest["filters"] == {
                "q": "mv3c", "status": "completed", "model": "gpt-6",
            }
            assert {v["request_id"] for v in manifest["requests"]} == {
                f"req_match_{i:03d}" for i in range(130)
            }


def test_current_sha_changes_after_hotfix_while_formal_version_is_unchanged(tmp_path: Path, monkeypatch):
    old_commit = "1" * 40
    current_commit = "2" * 40
    monkeypatch.setenv("CHAT2API_BUILD_COMMIT", old_commit)
    with TestClient(app_v8(tmp_path)) as client:
        store = client.app.state.telemetry
        asyncio.run(store.upsert({
            "request_id": "before_hotfix", "status": "running",
            "recorded_at": "2026-10-29T10:39:31+08:00",
        }))
        monkeypatch.setenv("CHAT2API_BUILD_COMMIT", current_commit)
        asyncio.run(store.upsert({
            "request_id": "after_hotfix", "status": "completed",
            "recorded_at": "2026-10-29T10:40:31+08:00",
        }))
        # Late status writes must preserve the creation SHA, regardless of
        # the actual current runtime or a forged incoming field.
        asyncio.run(store.update("before_hotfix", {
            "status": "completed", "request_server_commit": current_commit,
            "request_server_version": "spoofed",
        }))
        before = store.get("before_hotfix")
        after = store.get("after_hotfix")
        assert before["request_server_version"] == after["request_server_version"] == SERVER_RUNTIME_VERSION
        assert before["request_server_commit"] == old_commit
        assert after["request_server_commit"] == current_commit

        filters = {"current_version_only": "true"}
        listing = client.get("/api/admin/requests", params=filters, headers=headers())
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert listing.json()["current_version_commit"] == current_commit
        assert [r["request_id"] for r in listing.json()["data"]] == ["after_hotfix"]

        export = client.get("/api/admin/requests/logs/batch", params=filters,
                            headers=headers())
        assert export.status_code == 200
        assert export.headers["x-chat2api-version-scope"] == current_commit
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            manifest = json.loads(archive.read("index.json"))
            assert manifest["request_version_field"] == "request_server_commit"
            assert manifest["selected_server_commit"] == current_commit
            assert [r["request_id"] for r in manifest["requests"]] == ["after_hotfix"]
            assert manifest["requests"][0]["request_server_commit"] == current_commit
            report = json.loads(archive.read(manifest["requests"][0]["filename"]))
            assert report["request"]["request_server_commit"] == current_commit

        monkeypatch.setenv("CHAT2API_BUILD_COMMIT", old_commit)
        earlier = client.get("/api/admin/requests", params=filters, headers=headers())
        assert [r["request_id"] for r in earlier.json()["data"]] == ["before_hotfix"]


def test_unknown_runtime_and_old_unstamped_requests_are_not_misattributed(tmp_path: Path, monkeypatch):
    from app import runtime_code_identity

    monkeypatch.setattr(runtime_code_identity, "_RUNTIME_STAMP", tmp_path / "no-stamp")
    monkeypatch.delenv("CHAT2API_BUILD_COMMIT", raising=False)
    with TestClient(app_v8(tmp_path)) as client:
        store = client.app.state.telemetry
        asyncio.run(store.upsert({
            "request_id": "legacy", "status": "completed",
            "request_server_commit": "f" * 40,
        }))
        assert "request_server_commit" not in store.get("legacy")
        assert store.get("legacy")["request_server_version"] == SERVER_RUNTIME_VERSION

        for uri in ("/api/admin/requests", "/api/admin/requests/logs/batch"):
            result = client.get(uri, params={"current_version_only": "true"},
                                headers=headers())
            assert result.status_code == 409
            assert "unverified" in result.json()["detail"]

        valid_commit = "a" * 40
        monkeypatch.setenv("CHAT2API_BUILD_COMMIT", valid_commit)
        asyncio.run(store.upsert({
            "request_id": "current", "status": "completed",
        }))
        data = client.get("/api/admin/requests",
                          params={"current_version_only": "true"},
                          headers=headers()).json()
        assert data["total"] == 1
        assert [r["request_id"] for r in data["data"]] == ["current"]
        assert client.get("/api/admin/requests",
                          headers=headers()).json()["total"] == 2


def test_effective_container_hotfix_marker_beats_image_sha_and_fails_closed(tmp_path: Path, monkeypatch):
    from app import runtime_code_identity

    marker = tmp_path / "runtime.sha"
    monkeypatch.setattr(runtime_code_identity, "_RUNTIME_STAMP", marker)
    monkeypatch.setenv("CHAT2API_BUILD_COMMIT", "a" * 40)
    assert runtime_code_identity.runtime_code_sha() == "a" * 40
    marker.write_text("b" * 40 + "\n", encoding="ascii")
    assert runtime_code_identity.runtime_code_sha() == "b" * 40
    marker.write_text("not-a-commit\n", encoding="ascii")
    assert runtime_code_identity.runtime_code_sha() == ""
    marker.write_text("B" * 40, encoding="ascii")
    assert runtime_code_identity.runtime_code_sha() == ""


def test_request_logs_separate_terminal_file_snapshot_from_live_archive_status(tmp_path):
    with TestClient(app_v8(tmp_path)) as client:
        class Archive:
            def list_for_request(self, request_id, owner):
                assert request_id == "req_live_files_001"
                assert owner == "key-A"
                return [{"pending_id": "pgen_test", "filename": "result.json",
                         "bytes": 14, "sha256": "a" * 64, "status": "ready"}]
        client.app.state.deferred_generated_files = Archive()
        store = client.app.state.telemetry
        asyncio.run(store.upsert({
            "request_id": "req_live_files_001",
            "api_key_id": "key-A", "status": "completed",
            "response_generated_files": [{"filename": "result.json", "status": "processing"}],
        }))
        single = client.get("/api/admin/requests/req_live_files_001/log", headers=headers())
        assert single.status_code == 200
        report = single.json()
        assert report["request"]["response_generated_files"][0]["status"] == "processing"
        assert report["generated_file_live_status"]["state"] == "checked"
        assert report["generated_file_live_status"]["files"][0]["status"] == "ready"
        assert "token" not in str(report["generated_file_live_status"])
        bulk = client.get("/api/admin/requests/logs/batch", headers=headers())
        assert bulk.status_code == 200
        with zipfile.ZipFile(io.BytesIO(bulk.content)) as bundle:
            index = json.loads(bundle.read("index.json"))
            exported = json.loads(bundle.read(index["requests"][0]["filename"]))
            assert exported["generated_file_live_status"]["files"][0]["status"] == "ready"
            assert exported["request"]["response_generated_files"][0]["status"] == "processing"
