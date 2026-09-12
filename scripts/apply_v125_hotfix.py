from pathlib import Path


def require_once(text: str, needle: str, label: str) -> None:
    count = text.count(needle)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly once, found {count}: {needle[:120]!r}")


authority = Path("app/linux_worker_device_authority_v124_patch.py")
text = authority.read_text(encoding="utf-8")
old_server = '''def _server_url(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    scheme = forwarded or request.url.scheme
    host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",", 1)[0].strip()
    return f"{scheme}://{host}".rstrip("/")
'''
new_server = '''def _server_url(app: FastAPI, request: Request) -> str:
    """Resolve the externally reachable control-plane origin for install commands.

    The admin console is commonly HTTPS behind an HTTP reverse proxy. Never let
    that internal hop leak into generated Worker commands: prefer an explicit
    CHAT2API_PUBLIC_URL, then the browser's same-host HTTPS origin/referrer, then
    forwarded headers. For a non-loopback host with no proxy metadata, HTTPS is
    the safe default because the bootstrap installer rejects insecure HTTP.
    """
    settings = getattr(app.state, "settings", None)
    configured = str(getattr(settings, "public_url", "") or "").strip().rstrip("/")
    if configured:
        return configured

    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    request_host = str(request.headers.get("host") or "").split(",", 1)[0].strip()
    public_host = forwarded_host or request_host

    def browser_origin(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            from urllib.parse import urlsplit
            parsed = urlsplit(raw)
        except Exception:
            return ""
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        if public_host and parsed.netloc.lower() != public_host.lower():
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    origin = browser_origin(str(request.headers.get("origin") or ""))
    if origin:
        return origin.rstrip("/")
    referer = browser_origin(str(request.headers.get("referer") or ""))
    if referer:
        return referer.rstrip("/")

    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    if forwarded_proto in {"http", "https"} and public_host:
        return f"{forwarded_proto}://{public_host}".rstrip("/")

    host_only = public_host.rsplit(":", 1)[0].strip("[]").lower()
    if public_host and host_only not in {"localhost", "127.0.0.1", "::1"}:
        return f"https://{public_host}".rstrip("/")

    return f"{request.url.scheme}://{public_host or request.url.netloc}".rstrip("/")


def _repair_install_command_origin(command: str, public_url: str) -> str:
    """Rewrite only the control-plane origin in a previously generated command.

    v0.22.80 could persist an http:// origin when TLS terminated at the reverse
    proxy. Preserve enrollment/pairing secrets and every other argument while
    repairing the bootstrap URL and --server value after the server upgrades.
    """
    value = str(command or "").strip()
    if not value:
        return ""
    base = str(public_url or "").strip().rstrip("/")
    if not base:
        return value
    quoted_server = shlex.quote(base)
    quoted_bootstrap = shlex.quote(base + "/bootstrap/linux-worker.sh")
    value = re.sub(
        r"(?<=curl -fsSL )\S+/bootstrap/linux-worker\.sh",
        lambda _match: quoted_bootstrap,
        value,
        count=1,
    )
    value = re.sub(
        r"(--server\s+)\S+",
        lambda match: match.group(1) + quoted_server,
        value,
        count=1,
    )
    return value
'''
require_once(text, old_server, "authority old _server_url")
text = text.replace(old_server, new_server, 1)
require_once(text, "server = _server_url(request)", "authority create_install resolver call")
text = text.replace("server = _server_url(request)", "server = _server_url(app, request)", 1)
old_list = '''    @app.get("/api/admin/linux-devices")
    async def list_linux_devices(request: Request) -> dict[str, Any]:
        admin(request)
        return {"data": _device_rows(app), "authority": "linux-device-v124", "revision": PATCH_REVISION}
'''
new_list = '''    @app.get("/api/admin/linux-devices")
    async def list_linux_devices(request: Request) -> dict[str, Any]:
        admin(request)
        rows = _device_rows(app)
        public_url = _server_url(app, request)
        for device in rows:
            install_id = str(device.get("install_id") or "")
            command = str(device.get("install_command") or "")
            repaired = _repair_install_command_origin(command, public_url)
            if install_id and repaired and repaired != command:
                _persist_install_fields(installs, install_id, install_command=repaired)
                device["install_command"] = repaired
            for slot in device.get("slot_installations", []):
                slot_install_id = str(slot.get("install_id") or "")
                slot_command = str(slot.get("command") or "")
                repaired_slot = _repair_install_command_origin(slot_command, public_url)
                if slot_install_id and repaired_slot and repaired_slot != slot_command:
                    _persist_install_fields(installs, slot_install_id, install_command=repaired_slot)
                    slot["command"] = repaired_slot
        return {"data": rows, "authority": "linux-device-v124", "revision": PATCH_REVISION}
'''
require_once(text, old_list, "authority list route")
text = text.replace(old_list, new_list, 1)
authority.write_text(text, encoding="utf-8")

runtime = Path("app/runtime_contract.py")
text = runtime.read_text(encoding="utf-8")
old_versions = '''SERVER_RUNTIME_VERSION = "0.22.80"
PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.79"
PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.78"
PREVIOUS_PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.77"
PREVIOUS_PREVIOUS_PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.76"'''
new_versions = '''SERVER_RUNTIME_VERSION = "0.22.81"
PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.80"
PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.79"
PREVIOUS_PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.78"
PREVIOUS_PREVIOUS_PREVIOUS_PREVIOUS_SERVER_RUNTIME_VERSION = "0.22.77"'''
require_once(text, old_versions, "runtime version block")
text = text.replace(old_versions, new_versions, 1)
old_revision = 'v124-runtime-activation-fix-release-v02280"'
require_once(text, old_revision, "runtime revision marker")
text = text.replace(
    old_revision,
    'v124-runtime-activation-fix-release-v02280-linux-installer-public-https-v125-release-v02281"',
    1,
)
feature_marker = '            "linux_worker_extension_autopair_v124": True,\n'
require_once(text, feature_marker, "runtime v124 feature marker")
text = text.replace(feature_marker, feature_marker + '            "linux_worker_public_https_origin_v125": True,\n', 1)
runtime.write_text(text, encoding="utf-8")

for p in Path("tests").rglob("*"):
    if not p.is_file() or p.suffix not in {".py", ".js", ".mjs"}:
        continue
    t = p.read_text(encoding="utf-8")
    if "0.22.80" in t:
        p.write_text(t.replace("0.22.80", "0.22.81"), encoding="utf-8")

url_test = Path("tests/test_linux_device_public_url_v125.py")
test_text = url_test.read_text(encoding="utf-8")
test_text = test_text.replace(
    "from app.linux_worker_device_authority_v124_patch import _server_url",
    "from app.linux_worker_device_authority_v124_patch import _repair_install_command_origin, _server_url",
)
append = '''\n\ndef test_pending_v02280_command_is_repaired_without_rotating_secrets():\n    original = (\n        "curl -fsSL http://chat2api.mv3.cn/bootstrap/linux-worker.sh | "\n        "sudo bash -s -- --server http://chat2api.mv3.cn --enroll-code BGMK-GAZE-H2U3 "\n        "--pairing-code pair-secret-value --device-name TX03"\n    )\n    repaired = _repair_install_command_origin(original, "https://chat2api.mv3.cn")\n    assert repaired.startswith("curl -fsSL https://chat2api.mv3.cn/bootstrap/linux-worker.sh | ")\n    assert "--server https://chat2api.mv3.cn" in repaired\n    assert "--enroll-code BGMK-GAZE-H2U3" in repaired\n    assert "--pairing-code pair-secret-value" in repaired\n    assert "http://chat2api.mv3.cn" not in repaired\n\n\ndef test_pending_slot_command_repairs_server_origin_only():\n    original = (\n        "sudo bash /opt/chat2api-worker/scripts/linux_worker_slot_install_reported.sh "\n        "--server http://chat2api.mv3.cn --enroll-code ABCD-EFGH-IJKL --slot 2 "\n        "--pairing-code pair-slot-secret --device-name TX03"\n    )\n    repaired = _repair_install_command_origin(original, "https://chat2api.mv3.cn")\n    assert "--server https://chat2api.mv3.cn" in repaired\n    assert "--slot 2" in repaired\n    assert "--pairing-code pair-slot-secret" in repaired\n'''
if "test_pending_v02280_command_is_repaired_without_rotating_secrets" not in test_text:
    test_text += append
url_test.write_text(test_text, encoding="utf-8")
