from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_remote_login_has_single_close_control_and_closes_worker_session() -> None:
    source = text("app/admin_linux_device_authority_v124.js")
    assert 'dialog("linuxWorkerLoginDialog","远程实时画面"' in source
    assert 'data-close-dialog="${id}">关闭</button>' in source
    assert "结束登录" not in source
    assert 't.dataset.closeDialog==="linuxWorkerLoginDialog"' in source
    assert "await closeLogin()" in source
    assert 'method:"DELETE"' in source
    assert 'loginDialog?.addEventListener("cancel"' in source


def test_device_and_worker_manager_diagnostics_use_the_same_worker_endpoint() -> None:
    source = text("app/admin_linux_device_authority_v124.js")
    # Per-Worker diagnostics are owned only by the Worker list, never by device rows.
    worker_list = source.split("function renderWorkers()", 1)[1].split("function renderManager()", 1)[0]
    device_rows = source.split("function renderDevices()", 1)[1].split("function renderWorkers()", 1)[0]
    assert 'data-diagnostics="${esc(id)}">诊断日志</button>' in worker_list
    assert "data-diagnostics" not in device_rows
    assert 'fetch(`/api/admin/linux-worker/${encodeURIComponent(t.dataset.diagnostics)}/diagnostics/logs`' in source
