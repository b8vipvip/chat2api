"""v221: browser-ready windows and server admission permits are separate facts."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def src(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_scheduler_remains_single_authority_and_exposes_explicit_limit_source():
    scheduler = src("app/capacity_scheduler_v58.py")
    assert 'broker.create = create' in scheduler
    assert 'broker.capacity_snapshot = capacity_snapshot' in scheduler
    assert '"configured-max-concurrency-v239"' in scheduler
    assert 'return worker_baseline_total(client_id)' in scheduler
    assert '"standby_admission_permits": 0' in scheduler
    assert "worker_baseline_total" in scheduler
    assert '"worker_explicit_limit"' in scheduler
    assert 'sessions.authenticate(request.cookies.get(SESSION_COOKIE))' in scheduler
    assert 'Administrator login required' in scheduler
    assert '"queued_requests": queue_depth(client_id)' in scheduler
    # Physical windows cannot silently override the administrator's cap.
    assert 'row.get("max_concurrency")' in scheduler
    assert 'worker_limit(client_id)' in scheduler


def test_window_manager_reads_broker_snapshot_and_does_not_claim_requests():
    manager = src("app/window_manager_v88_patch.py")
    assert 'broker.capacity_snapshot(client_id)' in manager
    for item in (
        '"scheduler_limit_units"', '"scheduler_used_units"',
        '"scheduler_available_units"', '"scheduler_queued_requests"',
        '"scheduler_limit_source"', '"scheduler_baseline_total"',
    ):
        assert item in manager
    assert 'server_admission_available' not in manager
    assert "broker.create(" not in manager
    assert "broker.publish(" not in manager


def test_ready_window_and_baseline_total_use_one_consistent_status():
    ui = src("app/admin_window_manager_v88.js")
    assert 'if (value === "ready") return "可接待"' in ui
    assert '待机（调度容量已满）' not in ui
    assert '常态窗口总数' in ui
    assert '备用（并发已满，暂不可调用）' in ui
    assert 'worker_capacity_full' in ui
    assert '新请求必须排队，不能突破 N 个并发' in ui
    assert 'worker.scheduler_baseline_total' in ui
    assert 'worker.scheduler_queued_requests' in ui
    assert 'worker.standby_window_count' in ui
    assert '更新并发额度' not in ui
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app/admin_window_manager_v88.js")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
