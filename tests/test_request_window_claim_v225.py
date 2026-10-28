"""v225: the admission lease must identify the same Chrome window actually claimed."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def test_request_scoped_route_key_matches_pool_and_restore() -> None:
    for path in (
        "chrome_extension/conversation_persistent_pool_v132.js",
        "chrome_extension/conversation_persistent_route_restore_v132.js",
        "chrome_extension/conversation_routing.js",
    ):
        src = read(path)
        assert "::request::" in src
        assert "${requestId}" in src
    route = read("chrome_extension/conversation_routing.js")
    assert "route.request_scoped = isolated" in route
    assert '["chat.request", "image.request", "voice.request"]' in route
    assert "retry-same-request-window" in route
    assert "MIN_CHROME_ALARM_MS = 30 * 1000" in route
    assert "closeDueWindow(windowId, closeAt)" in route

def test_server_claim_is_bound_to_physical_verified_spare_id() -> None:
    scheduler = read("app/capacity_scheduler_v58.py")
    pool = read("chrome_extension/conversation_persistent_pool_v132.js")
    assert '"worker_verified_spare_window_claim": pool_window_claims.get(' in scheduler
    assert "retired_window_claims" in scheduler
    assert "retired.intersection_update(verified_ids)" in scheduler
    assert "row.window_id === claimedId" in pool
    assert "worker_verified_window_claim_stale" in pool

def test_real_route_isolation_harness() -> None:
    run = subprocess.run(
        ["node", str(ROOT / "tests/route_settlement_windows_log_reset_v130.mjs")],
        cwd=ROOT, capture_output=True, text=True, timeout=25,
    )
    assert run.returncode == 0, run.stderr + run.stdout
