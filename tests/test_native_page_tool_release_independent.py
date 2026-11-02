"""Standalone release channel: no server rebuild for future diagnostic-only releases."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import sys

import pytest

from app import native_page_tool_release as channel
from scripts import native_page_tool_control as worker


def script(version="1.2.3"):
    return ('VERSION = "'+version+'"\n'
            'if __name__ == "__main__":\n'
            '    import sys\n'
            '    if "--version" in sys.argv: print(VERSION)\n').encode()


def github_data(body, *, path=channel.RELEASE_PATH):
    return {
        "type":"file","path":path,"encoding":"base64",
        "size":len(body),
        "sha":hashlib.sha1(f"blob {len(body)}\0".encode()+body).hexdigest(),
        "content":base64.b64encode(body).decode(),
    }


def test_release_artifact_is_fixed_to_dedicated_branch_and_integrity_checked():
    assert channel.RELEASE_BRANCH=="native-page-diagnostic-releases"
    assert channel.RELEASE_PATH=="scripts/native_page_diagnostic_v1.py"
    assert channel.RELEASE_API.startswith("https://api.github.com/repos/b8vipvip/chat2api/contents/")
    assert channel.RELEASE_API.endswith("?ref=native-page-diagnostic-releases")
    raw=script()
    result,meta=channel.parse_diagnostic_release(github_data(raw))
    assert raw==result
    assert meta["version"]=="1.2.3"
    assert meta["sha256"]==hashlib.sha256(raw).hexdigest()
    assert meta["source"]=="github-dedicated-release-branch"
    for key,value in (
        ("type","symlink"),("path","scripts/other.py"),
        ("sha","0"*40),("size",len(raw)+1),("encoding","utf-8")
    ):
        invalid=github_data(raw);invalid[key]=value
        with pytest.raises(channel.DiagnosticReleaseUnavailable):
            channel.parse_diagnostic_release(invalid)
    for bad in (b'VERSION = "1.2.3"\ndef nope(:\n',
                b"print('no version')\n"):
        with pytest.raises(channel.DiagnosticReleaseUnavailable):
            channel.parse_diagnostic_release(github_data(bad))


def test_remote_release_lookup_is_cached_and_never_uses_untrusted_url(monkeypatch):
    hits=[]
    body=script()
    response_body=github_data(body)
    class FakeResponse:
        status_code=200
        content=json.dumps(response_body).encode()
        def json(self):return response_body
    class FakeClient:
        def __init__(self,**options):
            assert options["follow_redirects"] is False
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url,*,headers):
            hits.append(url)
            assert url==channel.RELEASE_API
            return FakeResponse()
    monkeypatch.setattr(channel.httpx,"AsyncClient",FakeClient)
    async def check():
        source=channel.DiagnosticReleaseSource()
        a=await source.load()
        b=await source.load()
        assert a==b
        assert a[0]==body
        assert len(hits)==1
        await source.load(force=True)
        assert len(hits)==2
    asyncio.run(check())


def test_worker_can_install_new_tool_from_release_without_restarting_server(monkeypatch,tmp_path):
    current=tmp_path/"current.py"
    current.write_bytes(script("1.2.2"))
    installed=tmp_path/"native_page_diagnostic_v1.py"
    installed.write_bytes(script("1.2.2"))
    monkeypatch.setattr(worker,"TOOL",current)
    monkeypatch.setattr(worker,"INSTALLED",installed)
    monkeypatch.setattr(worker,"ROOT",tmp_path/"native_pages")
    new=script("1.2.3")
    digest=hashlib.sha256(new).hexdigest()
    metadata={"version":"1.2.3","sha256":digest,"bytes":len(new),
              "source":"github-dedicated-release-branch",
              "repository":"b8vipvip/chat2api",
              "release_branch":"native-page-diagnostic-releases"}
    def open_url(url,timeout=10):
        assert url.startswith("https://server.example/bootstrap/native-page-diagnostic.")
        if url.endswith(".json"):return io.BytesIO(json.dumps(metadata).encode())
        assert url.endswith(".py?sha256="+digest)
        return io.BytesIO(new)
    monkeypatch.setattr(worker,"urlopen",open_url)
    w="wrk_abc123456"
    first=worker.execute("native_page_update",{},w,1,"https://server.example")
    assert first["ok"] is True and first["updated"] is True,first
    assert installed.read_bytes()==new and first["version"]=="1.2.3"
    second=worker.execute("native_page_update",{},w,1,"https://server.example")
    assert second["ok"] is True and second["updated"] is False
    old=script("1.2.1")
    oldsha=hashlib.sha256(old).hexdigest()
    metadata.update(version="1.2.1",sha256=oldsha,bytes=len(old))
    def downgrade(url,timeout=10):
        if url.endswith(".json"):return io.BytesIO(json.dumps(metadata).encode())
        return io.BytesIO(old)
    monkeypatch.setattr(worker,"urlopen",downgrade)
    bad=worker.execute("native_page_update",{},w,1,"https://server.example")
    assert bad["ok"] is False
    assert installed.read_bytes()==new


def test_worker_rejects_different_code_with_same_version(monkeypatch,tmp_path):
    code=script("1.2.3")
    existing=tmp_path/"native_page_diagnostic_v1.py"
    existing.write_bytes(code)
    monkeypatch.setattr(worker,"TOOL",existing)
    monkeypatch.setattr(worker,"INSTALLED",existing)
    monkeypatch.setattr(worker,"ROOT",tmp_path/"native_pages")
    changed=code+b"# altered without version increment\n"
    manifest={"version":"1.2.3","sha256":hashlib.sha256(changed).hexdigest(),
              "bytes":len(changed),"source":"github-dedicated-release-branch",
              "repository":"b8vipvip/chat2api",
              "release_branch":"native-page-diagnostic-releases"}
    def open_url(url,timeout=10):
        if url.endswith(".json"):return io.BytesIO(json.dumps(manifest).encode())
        return io.BytesIO(changed)
    monkeypatch.setattr(worker,"urlopen",open_url)
    result=worker.execute("native_page_update",{},"wrk_abc123456",1,"https://server.example")
    assert result["ok"] is False
    assert existing.read_bytes()==code


def test_worker_toolbar_groups_version_check_beside_tool_update():
    root=Path(__file__).resolve().parents[1]
    panel=(root/"app"/"worker_probe_ui_patch.py").read_text()
    x=panel.index('class="native-tool-release-actions"')
    close=panel.index("</div>",x)
    toolbar=panel[x:close]
    assert 'id="nativePageUpdate"' in toolbar
    assert 'id="nativePageVersion"' in toolbar
    assert toolbar.index('id="nativePageUpdate"') < toolbar.index('id="nativePageVersion"')
    assert 'id="nativePageStart"' not in toolbar
    backend=(root/"app"/"native_page_diagnostic_api.py").read_text()
    assert "from .native_page_tool_release import" in backend
    assert 'DiagnosticReleaseSource()' in backend
    assert 'await latest_release()' in backend
    assert 'sha256:str=""' in backend


def test_native_page_results_show_compact_status_without_raw_error():
    """The result table is a summary; the exported JSON retains full evidence."""
    root=Path(__file__).resolve().parents[1]
    js=(root/"app"/"admin_worker_tools_v1.js").read_text()
    ui=(root/"app"/"worker_probe_ui_patch.py").read_text()
    assert '<th>状态/结果</th>' in ui
    assert '<th>状态/阶段</th>' not in ui
    assert 'stateLabel + " · " + resultLabel' in js
    assert '证据已生成' in js and '未获取回复' in js
    assert 'stateCell.title=issue' not in js
    assert 'stateLabel + stageLabel' not in js
    assert '"/log"' in js or '"+ "/log"' in js or '"/log");' in js
