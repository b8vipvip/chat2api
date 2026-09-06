from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _served_admin_probe() -> str:
    code = r'''
from fastapi.testclient import TestClient
from app.entry import app

page = TestClient(app).get('/admin')
assert page.status_code == 200, page.status_code
print(page.text)
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_served_request_history_has_no_legacy_detail_panel() -> None:
    page = _served_admin_probe()
    assert '<div class="panel detail">' not in page
    assert 'id="rqDetail"' not in page
    assert 'async function requestDetail(' not in page
    assert 'window.requestDetail' not in page
    assert '<section class="view" id="view-requests"><div><div class="panel">' in page


def test_request_history_rows_keep_only_explicit_action_owners() -> None:
    page = _served_admin_probe()
    start = page.index("async function loadRequests()")
    end = page.index("\nrequestHistoryEnsureControls();\n$('rqGo').onclick=loadRequests;", start)
    loader = page[start:end]

    assert 'requestDetail(' not in loader
    assert "tr.className='clickable'" not in loader
    assert "tr.addEventListener('click'" not in loader
    assert "requestHistoryButton(tr,'查看对话'" in loader
    assert "requestHistoryButton(tr,'下载日志'" in loader
