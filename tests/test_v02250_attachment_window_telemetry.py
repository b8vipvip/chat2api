from __future__ import annotations

from pathlib import Path

from fastapi.responses import Response

from app.attachment_download_v82_patch import attachment_download_headers
from app.runtime_contract import CHROME_BRIDGE_BUNDLE_VERSION, SERVER_RUNTIME_VERSION, version_contract_payload
from fastapi import FastAPI


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_unicode_attachment_headers_are_http_safe_and_keep_extension() -> None:
    headers = attachment_download_headers("视觉测试图片-中文.png")
    # This is the production failure boundary: Starlette encodes raw header
    # values as latin-1. Every emitted header must therefore be encodable.
    for value in headers.values():
        value.encode("latin-1")
    response = Response(b"png", media_type="image/png", headers=headers)
    assert response.headers["x-chat2api-attachment-revision"] == "82"
    assert response.headers["x-chat2api-filename-encoding"] == "percent-utf8"
    assert response.headers["x-chat2api-filename"].endswith(".png")
    assert "%" in response.headers["x-chat2api-filename"]
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert 'filename="attachment.png"' in response.headers["content-disposition"]


def test_extension_download_route_is_replaced_by_v82_boundary() -> None:
    source = text("app/attachment_download_v82_patch.py")
    entry = text("app/entry.py")
    assert '_EXTENSION_FILE_PATH = "/api/extensions/files/{file_id}"' in source
    assert "app.router.routes[:]" in source
    assert "X-Chat2API-Filename-Encoding" in source
    assert "install_attachment_download_v82_patch(app)" in entry


def test_worker_window_denominator_never_falls_back_to_concurrency_limit() -> None:
    source = text("app/admin_worker_presentation_v66.js")
    assert "liveWindowTruth" in source
    assert "truth.byClient.get(clientId)" in source
    assert "physical > 0 ? physical : limit" not in source
    assert 'data-chat2api-live-window-count="1"' in source
    assert "#22c55e" in source
    assert "liveVerified" in source
    assert "physicalKnown = physicalRaw !== undefined" in source  # legacy fallback only


def test_runtime_identity_and_v82_features_are_current() -> None:
    assert SERVER_RUNTIME_VERSION == "0.22.59"
    assert CHROME_BRIDGE_BUNDLE_VERSION == "0.8.27"
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["features"]["unicode_attachment_download_v82"] is True
    assert payload["features"]["worker_live_window_count_v82"] is True
    assert payload["features"]["runtime_version_observability_v82"] is True
    runtime_source = text("app/runtime_contract.py")
    assert 'payload["server_version"] = SERVER_RUNTIME_VERSION' in runtime_source
    assert 'payload["server_version"] = PATCH_VERSION' not in text("app/v21_1_patch.py")
