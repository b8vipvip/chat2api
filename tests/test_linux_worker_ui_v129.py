from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_remote_login_has_single_close_control_and_closes_worker_session() -> None:
    source = text("app/admin_linux_device_authority_v124.js")
    assert 'dialog("linuxWorkerLoginDialog","远程登录 ChatGPT"' in source
    assert 'data-close-dialog="${id}">关闭</button>' in source
    assert "结束登录" not in source
    assert 't.dataset.closeDialog==="linuxWorkerLoginDialog"' in source
    assert "await closeLogin()" in source
    assert 'method:"DELETE"' in source
    assert 'loginDialog?.addEventListener("cancel"' in source


def test_device_and_worker_manager_diagnostics_use_the_same_worker_endpoint() -> None:
    source = text("app/admin_linux_device_authority_v124.js")
    # The device row targets its primary Worker, while Worker Manager targets
    # each selected Worker. Both buttons flow through the same download handler.
    assert 'data-diagnostics="${esc(w.worker_id)}">诊断日志</button>' in source
    assert source.count('data-diagnostics="${esc(w.worker_id)}">诊断日志</button>') >= 2
    assert 'fetch(`/api/admin/linux-worker/${encodeURIComponent(t.dataset.diagnostics)}/diagnostics/logs`' in source
