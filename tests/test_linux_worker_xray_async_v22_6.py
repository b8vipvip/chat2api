from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_xray_manifest_is_non_blocking_and_reports_preparing_state():
    source = (ROOT / "app" / "linux_worker_xray_patch.py").read_text(encoding="utf-8")
    assert "_start_refresh" in source
    assert '"state": "preparing"' in source
    assert '"ready": False' in source
    assert "threading.Thread" in source
    assert "daemon=True" in source
    assert "await asyncio.to_thread(_ensure_cache" not in source


def test_bootstrap_polls_center_xray_manifest_instead_of_hanging_on_one_request():
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert '"$SERVER/bootstrap/xray/latest.json"' in source
    assert "XRAY_READY=0" in source
    assert "sleep 2" in source
    assert ".ready == true" in source
    assert "等待中心服务器准备 Xray 缓存" in source
    assert "中心服务器未能在限定时间内准备 Xray" in source


def test_xray_upstream_timeouts_are_bounded():
    source = (ROOT / "app" / "linux_worker_xray_patch.py").read_text(encoding="utf-8")
    assert "API_TIMEOUT_SECONDS = 12" in source
    assert "ASSET_TIMEOUT_SECONDS = 90" in source
    assert "DIGEST_TIMEOUT_SECONDS = 12" in source
