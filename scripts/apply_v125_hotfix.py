from pathlib import Path


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise SystemExit(f"{path}: expected {count} occurrences, found {actual}: {old[:120]!r}")
    p.write_text(text.replace(old, new, count), encoding="utf-8")


authority = Path("app/linux_worker_device_authority_v124_patch.py")
text = authority.read_text(encoding="utf-8")
old = '''def _server_url(request: Request) -> str:
    forwarded = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    scheme = forwarded or request.url.scheme
    host = str(request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",", 1)[0].strip()
    return f"{scheme}://{host}".rstrip("/")
'''
new = '''def _server_url(app: FastAPI, request: Request) -> str:
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
'''
if text.count(old) != 1:
    raise SystemExit("old _server_url block not found exactly once")
text = text.replace(old, new, 1)
if text.count("server = _server_url(request)") != 1:
    raise SystemExit("expected one create_install _server_url call")
text = text.replace("server = _server_url(request)", "server = _server_url(app, request)", 1)
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
if text.count(old_versions) != 1:
    raise SystemExit("runtime version block did not match 0.22.80")
text = text.replace(old_versions, new_versions, 1)
if 'v124-runtime-activation-fix-release-v02280"' not in text:
    raise SystemExit("runtime revision marker missing")
text = text.replace(
    'v124-runtime-activation-fix-release-v02280"',
    'v124-runtime-activation-fix-release-v02280-linux-installer-public-https-v125-release-v02281"',
    1,
)
marker = '            "linux_worker_extension_autopair_v124": True,\n'
if marker not in text:
    raise SystemExit("runtime v124 feature marker missing")
text = text.replace(marker, marker + '            "linux_worker_public_https_origin_v125": True,\n', 1)
runtime.write_text(text, encoding="utf-8")

smoke = Path(".github/workflows/production-image-smoke.yml")
text = smoke.read_text(encoding="utf-8")
if text.count("payload['server']['runtime_version'] == '0.22.80'") != 1:
    raise SystemExit("production smoke runtime expectation did not match 0.22.80")
text = text.replace("payload['server']['runtime_version'] == '0.22.80'", "payload['server']['runtime_version'] == '0.22.81'", 1)
marker = "          assert payload['features']['linux_worker_device_authority_v124'] is True\n"
if marker not in text:
    raise SystemExit("production smoke v124 feature assertion missing")
text = text.replace(marker, marker + "          assert payload['features']['linux_worker_public_https_origin_v125'] is True\n", 1)
smoke.write_text(text, encoding="utf-8")

for p in Path("tests").rglob("*"):
    if not p.is_file() or p.suffix not in {".py", ".js", ".mjs"}:
        continue
    t = p.read_text(encoding="utf-8")
    if "0.22.80" in t:
        p.write_text(t.replace("0.22.80", "0.22.81"), encoding="utf-8")
