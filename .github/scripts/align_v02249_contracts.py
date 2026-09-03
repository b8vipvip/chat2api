from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Align the server-side runtime config with the requested two-minute affinity.
path = ROOT / "app" / "v21_13_patch.py"
text = path.read_text(encoding="utf-8")
old = "ROUTE_IDLE_CLOSE_SECONDS = 10 * 60"
new = "ROUTE_IDLE_CLOSE_SECONDS = 2 * 60"
if text.count(old) != 1:
    raise SystemExit(f"v21_13 idle-close anchor count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

# Current-release contract tests intentionally follow the newest formal runtime
# and Worker bundle. Advancing the release must advance those expectations too.
for path in (ROOT / "tests").iterdir():
    if not path.is_file() or path.suffix not in {".py", ".mjs", ".js"}:
        continue
    text = path.read_text(encoding="utf-8")
    text = text.replace('0.22.48', '0.22.49')
    text = text.replace('0.8.19', '0.8.20')
    text = text.replace('IDLE_CLOSE_MS = 300000', 'IDLE_CLOSE_MS = 2 * 60 * 1000')
    path.write_text(text, encoding="utf-8")

# Host-side CDP pruning is now an explicit recovery opt-in, never a normal
# launcher step, because it cannot distinguish live MV3 request windows.
path = ROOT / "tests" / "test_linux_worker_tab_init.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    'def test_launcher_discards_only_window_restore_state_and_runs_one_shot_cdp_cleanup():',
    'def test_launcher_discards_restore_state_without_pruning_live_extension_windows_by_default():',
)
text = text.replace(
    "    assert '--keep 1 --wait 45' in text\n",
    "    assert 'CHAT2API_TAB_INIT_PRUNE:-0' in text\n    assert '--keep 1 --wait 8' in text\n    assert '--keep 1 --wait 45' not in text\n",
)
path.write_text(text, encoding="utf-8")

# Reserve VM contract: reserve=3 means three *spares*. After a request claims a
# reserve slot the pool replenishes it, so one routed + three spares = four live
# managed windows. Once that route closes, physical total returns to three.
path = ROOT / "tests" / "reserve_pool_v29.mjs"
text = path.read_text(encoding="utf-8")
text = text.replace(
    'assert.equal(snapshot.total, 3, "claiming a reserve window must not increase total");',
    'assert.equal(snapshot.total, 4, "one routed request plus three reserve spares should keep four managed windows");',
)
text = text.replace(
    'route.close_after = route.last_active_at + 5 * 60 * 1000;',
    'route.close_after = route.last_active_at + 5 * 60 * 1000;',
)
text = text.replace(
    'assert.ok(Math.abs(route.close_after - (route.last_active_at + 10 * 60 * 1000)) <= 1000, "idle deadline must be extended to ten minutes");',
    'assert.ok(Math.abs(route.close_after - (route.last_active_at + 2 * 60 * 1000)) <= 1000, "idle deadline must be normalized to two minutes");',
)
text = text.replace(
    'assert.ok(alarms.has(`chat2api-route-close:${route.window_id}`), "ten-minute route close alarm should replace historical deadline");',
    'assert.ok(alarms.has(`chat2api-route-close:${route.window_id}`), "two-minute route close alarm should replace historical deadline");',
)
text = text.replace('assert.equal(latest.metadata.reserve_window_idle_close_seconds, 600);', 'assert.equal(latest.metadata.reserve_window_idle_close_seconds, 120);')
path.write_text(text, encoding="utf-8")

# Static telemetry contract follows the same spare semantics and two-minute
# route lifetime.
path = ROOT / "tests" / "test_reserve_window_telemetry_v29.py"
text = path.read_text(encoding="utf-8")
text = text.replace('ROUTE_IDLE_CLOSE_SECONDS == 600', 'ROUTE_IDLE_CLOSE_SECONDS == 120')
text = text.replace("'const ROUTE_IDLE_CLOSE_MS = 10 * 60 * 1000'", "'const ROUTE_IDLE_CLOSE_MS = 2 * 60 * 1000'")
text = text.replace("'state.target - snapshot.total - warmOpening'", "'state.target - spareTotal - warmOpening'")
text = text.replace("'if (snapshot.total > state.target && state.reserveSlots.size)'", "'if (spareTotal > state.target && state.reserveSlots.size)'")
text = text.replace('def test_reserve_pool_reuses_spares_and_extends_route_idle_close_to_ten_minutes():', 'def test_reserve_pool_reuses_spares_and_normalizes_route_idle_close_to_two_minutes():')
path.write_text(text, encoding="utf-8")

# Strengthen the new release regression with the server-side affinity contract.
path = ROOT / "tests" / "test_v02249_linux_response_lifecycle.py"
text = path.read_text(encoding="utf-8")
needle = '''def test_conversation_affinity_is_two_minutes():
    assert "const IDLE_CLOSE_MS = 2 * 60 * 1000;" in text("chrome_extension/conversation_routing.js")
'''
replacement = '''def test_conversation_affinity_is_two_minutes():
    assert "const IDLE_CLOSE_MS = 2 * 60 * 1000;" in text("chrome_extension/conversation_routing.js")
    assert "ROUTE_IDLE_CLOSE_SECONDS = 2 * 60" in text("app/v21_13_patch.py")
'''
if needle not in text:
    raise SystemExit("v0.22.49 affinity regression anchor missing")
path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
