from types import SimpleNamespace

from fastapi import FastAPI
from starlette.requests import Request

from app.config import Settings
from app.linux_worker_device_authority_v124_patch import _repair_install_command_origin, _server_url


def _request(*, scheme="http", host="chat2api.mv3.cn", headers=None):
    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    if not any(key == b"host" for key, _ in raw_headers):
        raw_headers.append((b"host", host.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": scheme,
        "path": "/api/admin/linux-devices",
        "raw_path": b"/api/admin/linux-devices",
        "query_string": b"",
        "server": (host.split(":", 1)[0], 80 if scheme == "http" else 443),
        "client": ("127.0.0.1", 12345),
        "headers": raw_headers,
    }
    return Request(scope)


def _app(public_url=""):
    app = FastAPI()
    app.state.settings = Settings(CHAT2API_PUBLIC_URL=public_url)
    return app


def test_configured_public_url_is_authoritative():
    request = _request(headers={"origin": "https://wrong.example"})
    assert _server_url(_app("https://chat2api.mv3.cn"), request) == "https://chat2api.mv3.cn"


def test_same_origin_browser_https_beats_internal_http_reverse_proxy_scheme():
    request = _request(
        scheme="http",
        headers={"origin": "https://chat2api.mv3.cn", "referer": "https://chat2api.mv3.cn/admin#linux-workers"},
    )
    assert _server_url(_app(), request) == "https://chat2api.mv3.cn"


def test_forwarded_proto_and_host_are_used_when_browser_origin_is_absent():
    request = _request(
        scheme="http",
        host="127.0.0.1:8765",
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "chat2api.mv3.cn"},
    )
    assert _server_url(_app(), request) == "https://chat2api.mv3.cn"


def test_public_host_defaults_to_https_when_proxy_drops_forwarded_proto():
    request = _request(scheme="http", host="chat2api.mv3.cn")
    assert _server_url(_app(), request) == "https://chat2api.mv3.cn"


def test_loopback_keeps_request_scheme_for_local_development():
    request = _request(scheme="http", host="127.0.0.1:8765")
    assert _server_url(_app(), request) == "http://127.0.0.1:8765"


def test_pending_v02280_command_is_repaired_without_rotating_secrets():
    original = (
        "curl -fsSL http://chat2api.mv3.cn/bootstrap/linux-worker.sh | "
        "sudo bash -s -- --server http://chat2api.mv3.cn --enroll-code BGMK-GAZE-H2U3 "
        "--pairing-code pair-secret-value --device-name TX03"
    )
    repaired = _repair_install_command_origin(original, "https://chat2api.mv3.cn")
    assert repaired.startswith("curl -fsSL https://chat2api.mv3.cn/bootstrap/linux-worker.sh | ")
    assert "--server https://chat2api.mv3.cn" in repaired
    assert "--enroll-code BGMK-GAZE-H2U3" in repaired
    assert "--pairing-code pair-secret-value" in repaired
    assert "http://chat2api.mv3.cn" not in repaired


def test_pending_slot_command_repairs_server_origin_only():
    original = (
        "sudo bash /opt/chat2api-worker/scripts/linux_worker_slot_install_reported.sh "
        "--server http://chat2api.mv3.cn --enroll-code ABCD-EFGH-IJKL --slot 2 "
        "--pairing-code pair-slot-secret --device-name TX03"
    )
    repaired = _repair_install_command_origin(original, "https://chat2api.mv3.cn")
    assert "--server https://chat2api.mv3.cn" in repaired
    assert "--slot 2" in repaired
    assert "--pairing-code pair-slot-secret" in repaired
