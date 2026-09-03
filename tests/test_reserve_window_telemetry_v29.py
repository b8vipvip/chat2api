from pathlib import Path
from types import SimpleNamespace
import json
import re
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.v21_13_patch import (
    MAX_RESERVE_WINDOW_TARGET,
    ROUTE_IDLE_CLOSE_SECONDS,
    install_v21_13_patch,
)


ROOT = Path(__file__).resolve().parents[1]


def _semver(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    assert match, value
    return tuple(map(int, match.groups()))


class _Registry:
    async def authenticate(self, client_id: str, token: str) -> bool:
        return client_id == "ext_test" and token == "token_test"


def test_extension_runtime_config_uses_live_concurrency_as_reserve_target():
    app = FastAPI()
    app.state.registry = _Registry()
    app.state.broker = SimpleNamespace(max_concurrency=3)
    app.state.concurrency_config = {"max_concurrency": 10}
    install_v21_13_patch(app)
    client = TestClient(app)

    assert client.get("/api/extensions/runtime-config").status_code == 401
    response = client.get(
        "/api/extensions/runtime-config",
        headers={
            "X-Extension-Client-ID": "ext_test",
            "X-Extension-Token": "token_test",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["reserve_window_target"] == 10
    assert payload["route_idle_close_seconds"] == ROUTE_IDLE_CLOSE_SECONDS == 120
    assert payload["max_reserve_window_target"] == MAX_RESERVE_WINDOW_TARGET == 32


def test_reserve_pool_is_loaded_between_warm_pool_and_worker_router():
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    warm = entry.index('"conversation_warm_pool_v2.js"')
    external = entry.index('"background_external_warm_v28.js"')
    reserve = entry.index('"background_reserve_pool_v29.js"')
    workers = entry.index('"conversation_workers_v25.js"')
    assert warm < external < reserve < workers


def test_reserve_pool_tracks_real_managed_windows_and_active_subset():
    source = (ROOT / "chrome_extension" / "background_reserve_pool_v29.js").read_text(encoding="utf-8")
    for token in (
        'const ROUTE_IDLE_CLOSE_MS = 5 * 60 * 1000',
        'const MAX_TARGET = 32',
        'reserve_window_total: snapshot.total',
        'reserve_window_active: snapshot.active',
        'reserve_window_target: snapshot.target',
        'reserve_window_idle_close_seconds: ROUTE_IDLE_CLOSE_MS / 1000',
        'const managed = new Set()',
        'const active = new Set()',
        'for (const slot of state.reserveSlots.values())',
        'for (const slot of pool?.warmSlots?.values?.() || [])',
        'for (const route of Object.values(router?.routes || {}))',
        'if (route.inflight_request_id) active.add(route.window_id)',
        'managed.add(route.window_id)',
        'state.target - spareTotal - warmOpening',
        'if (spareTotal > state.target && state.reserveSlots.size)',
    ):
        assert token in source

    assert "chatTabs()" not in source
    assert "tab_count" not in source


def test_reserve_pool_reuses_spares_and_normalizes_route_idle_close_to_two_minutes():
    source = (ROOT / "chrome_extension" / "background_reserve_pool_v29.js").read_text(encoding="utf-8")
    for token in (
        'if (Number(warmPool?.warmSlots?.size || 0) > 0) return null',
        'state.reserveSlots.delete(selectedKey)',
        'router.routes[key] = route',
        'route.last_rotation_reason = "reserve-pool-v29-claim"',
        'const expected = lastActive + ROUTE_IDLE_CLOSE_MS',
        'await chrome.alarms.create(`${ROUTE_ALARM_PREFIX}${route.window_id}`',
        'changes[ROUTE_STORAGE_KEY]',
        'chrome.windows.onRemoved.addListener',
        'scheduleReconcile(300)',
    ):
        assert token in source


def test_realtime_window_refresh_is_compacted_into_worker_settings_column():
    health = (ROOT / "app" / "admin_v21_6.js").read_text(encoding="utf-8")
    worker = (ROOT / "app" / "admin_v21_5.js").read_text(encoding="utf-8")

    assert '["reserve_windows", "实时窗口"]' not in health
    assert 'data-live-window-refresh' not in health
    assert 'renderReserveWindowCell' not in health
    assert 'th.dataset.chat2apiColumnKey = "worker_settings"' in worker
    assert 'th.textContent = "并发设置"' in worker
    assert 'data-chat2api-structural-owner="worker-settings-v59"' in worker
    assert 'platformHeader.textContent = "Worker 窗口"' not in worker
    assert 'data-worker-refresh' in worker
    assert '/windows/refresh' in worker
    assert '窗口已刷新：总数 ${Number(snap.total || 0)}' in worker
    assert worker.index('data-worker-save') < worker.index('data-worker-refresh')
    assert 'data-worker-reserve' in worker


def test_reserve_versions_match_manifest_and_runtime_contract():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    server = re.search(r'SERVER_RUNTIME_VERSION = "([0-9.]+)"', runtime)
    protocol = re.search(r'CHROME_BRIDGE_VERSION = "([0-9.]+)"', runtime)
    bundle = re.search(r'CHROME_BRIDGE_BUNDLE_VERSION = "([0-9.]+)"', runtime)
    assert server and protocol and bundle
    assert _semver(server.group(1)) >= (0, 21, 13)
    assert protocol.group(1) == "0.8.1"
    assert _semver(manifest["version"]) >= (0, 8, 0)
    assert bundle.group(1) == manifest["version"]


def test_reserve_pool_javascript_syntax():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "chrome_extension" / "background_reserve_pool_v29.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
