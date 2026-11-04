"""Independent Windows DOM collector updates must be fixed-source and digest verified."""
import base64
import hashlib

import pytest

from app.windows_native_page_tool_release import (
    RELEASE_BRANCH, RELEASE_PATH, RELEASE_REPO,
    WindowsCollectorReleaseUnavailable,
    parse_windows_collector_release,
)


def envelope(body):
    raw = body.encode("utf-8")
    return {
        "type": "file", "path": RELEASE_PATH, "encoding": "base64",
        "size": len(raw),
        "sha": hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(),
        "content": base64.b64encode(raw).decode(),
    }


def test_verified_windows_release_pins_path_blob_sha_version_and_sha256():
    source = '(()=>{ const DIAGNOSTIC_TOOL_VERSION="1.0.0"; return {ok:true}; })()'
    code, manifest = parse_windows_collector_release(envelope(source))
    assert code == source
    assert manifest["version"] == "1.0.0"
    assert manifest["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert manifest["repository"] == RELEASE_REPO
    assert manifest["release_branch"] == RELEASE_BRANCH
    assert manifest["execution"] == "chrome.userScripts.execute/USER_SCRIPT"


@pytest.mark.parametrize("mutate", [
    lambda v: {**v, "path": "scripts/arbitrary.js"},
    lambda v: {**v, "sha": "0"*40},
    lambda v: {**v, "size": 1},
    lambda v: {**v, "content": "***"},
    lambda v: {**v, "encoding": "utf8"},
])
def test_release_rejects_untrusted_envelope(mutate):
    source = '(()=>{ const DIAGNOSTIC_TOOL_VERSION="1.0.0"; return {ok:true}; })()'
    with pytest.raises(WindowsCollectorReleaseUnavailable):
        parse_windows_collector_release(mutate(envelope(source)))


@pytest.mark.parametrize("source", [
    "(()=>{ return {ok:true}; })()",
    '(()=>{ const DIAGNOSTIC_TOOL_VERSION="1.0.0"; return 1; })();',
    'const DIAGNOSTIC_TOOL_VERSION="1.0.0";',
])
def test_release_refuses_missing_version_or_invalid_entrypoint(source):
    with pytest.raises(WindowsCollectorReleaseUnavailable):
        parse_windows_collector_release(envelope(source))


def test_fixed_github_raw_release_parser_matches_content_hashes():
    from app.windows_native_page_tool_release import parse_windows_collector_raw
    source = b'(()=>{ const DIAGNOSTIC_TOOL_VERSION="1.0.2"; return {ok:true}; })()'
    code, manifest = parse_windows_collector_raw(source)
    assert code.encode("utf-8") == source
    assert manifest["version"] == "1.0.2"
    assert manifest["source"] == "github-raw-fixed-release-branch"
    assert manifest["sha256"] == hashlib.sha256(source).hexdigest()


@pytest.mark.asyncio
async def test_rest_403_uses_fixed_raw_source(monkeypatch):
    import httpx
    from app import windows_native_page_tool_release as module
    raw = b'(()=>{ const DIAGNOSTIC_TOOL_VERSION="1.0.2"; return {ok:true}; })()'
    requests = []
    def respond(request):
        requests.append(str(request.url))
        if str(request.url) == module.RELEASE_API:
            return httpx.Response(403)
        assert str(request.url) == module.RELEASE_RAW
        return httpx.Response(200, content=raw)
    original = httpx.AsyncClient
    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(respond)
        return original(*args, **kwargs)
    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    release = module.WindowsCollectorReleaseSource()
    source, manifest = await release.load(force=True)
    assert source.encode() == raw
    assert manifest["version"] == "1.0.2"
    assert manifest["api_fallback_reason"] == "github_windows_release_http_403"
    assert requests == [module.RELEASE_API, module.RELEASE_RAW]
    await release.load(force=True)
    assert requests[-1] == module.RELEASE_RAW
    assert len(requests) == 3
