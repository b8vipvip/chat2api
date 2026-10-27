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
    assert '"worker-configured-override-v58"' in scheduler
    assert '"worker-default-concurrency-v58"' in scheduler
    assert '"worker_explicit_limit"' in scheduler
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
        '"scheduler_limit_source"', '"server_admission_available"',
    ):
        assert item in manager
    assert 'item["server_admission_available"] = eligible_ready > 0' in manager
    assert "broker.create(" not in manager
    assert "broker.publish(" not in manager


def test_ready_standby_does_not_claim_capacity_automatically():
    ui = src("app/admin_window_manager_v88.js")
    assert '待机（调度容量已满）' in ui
    assert 'worker.scheduler_limit_units' in ui
    assert 'worker.scheduler_used_units' in ui
    assert 'worker.scheduler_queued_requests' in ui
    assert 'worker.standby_window_count' in ui
    assert 'capacity-v57' in ui
    assert "method: \"PUT\"" in ui
    assert '按当前${proposed}个窗口设定' in ui
    assert 'confirm(' in ui
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app/admin_window_manager_v88.js")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
