from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_v211_static_contracts() -> None:
    patch = (ROOT / "app" / "v21_1_patch.py").read_text(encoding="utf-8")
    admin = (ROOT / "app" / "admin_v21_1.js").read_text(encoding="utf-8")
    admin_live = (ROOT / "app" / "admin_v21_5.js").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    workers = (EXTENSION / "conversation_workers_v24.js").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'PATCH_VERSION = "0.21.1"' in patch
    assert "DEFAULT_MAX_CONCURRENCY = 3" in patch
    assert 'CONFIG_FILENAME = "concurrency.json"' in patch
    assert '"mode": "per-extension"' in patch
    assert '"clients": {' in patch
    assert 'routing["worker_limit"] = limit_for(client_id)' in patch
    assert '@app.get("/api/admin/concurrency")' in patch
    assert '@app.put("/api/admin/concurrency")' in patch
    assert '@app.get("/api/admin/extensions/{client_id}/concurrency")' in patch
    assert '@app.put("/api/admin/extensions/{client_id}/concurrency")' in patch
    assert '@app.delete("/api/admin/extensions/{client_id}/concurrency")' in patch
    assert '"extension_per_client_concurrency": True' in patch
    assert '"image_request_weight": 1' in patch
    assert '"live_voice_request_weight": 1' in patch
    assert "async def create_per_extension" in patch

    # The global overview editor is intentionally removed. Per-ID controls live in
    # the extension-management table next to realtime activity.
    assert 'data-concurrency-settings-v211' in admin
    assert 'querySelectorAll("[data-concurrency-settings-v211]").forEach(node => node.remove())' in admin
    assert "按扩展 ID 独立计数" in admin
    assert "API 调用 / 并发上限" in admin
    assert 'fetch("/api/admin/concurrency"' not in admin
    assert "saveConcurrencyV211" not in admin

    assert "API 调用 / 并发上限" in admin_live
    assert "data-extension-concurrency-editor" in admin_live
    assert "data-concurrency-limit" in admin_live
    assert "data-concurrency-save" in admin_live
    assert '/api/admin/extensions/${encodeURIComponent(clientId)}/concurrency' in admin_live
    assert 'method: "PUT"' in admin_live

    assert "MAX_WORKERS_PER_KEY = 3" in workers
    assert "function workerLimit(message)" in workers
    assert "message?.routing?.worker_limit" in workers
    assert "selected.workerLimit" in workers
    assert 'extension_worker_limit_source: Number(message?.routing?.worker_limit) > 0 ? "server-routing" : "default"' in workers

    assert "from .v21_1_patch import install_v21_1_patch" in entry
    assert "from .v21_2_patch import install_v21_2_patch" in entry
    assert entry.index("install_v21_routing_patch(app)") < entry.index("install_v21_1_patch(app)")
    assert entry.index("install_v21_1_patch(app)") < entry.index("install_v21_2_patch(app)")
    assert "node --check app/admin_v21_1.js" in ci


def test_v211_runtime_config_is_persistent_and_per_extension() -> None:
    script = r'''
import asyncio
import json
import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.v21_patch import install_v21_patch
from app.v21_routing_patch import install_v21_routing_patch
from app.v21_1_patch import install_v21_1_patch


def build(data_dir):
    settings = Settings(
        CHAT2API_API_KEY="test-master-key",
        CHAT2API_PAIRING_CODE="test-pair-code",
        CHAT2API_ADMIN_USERNAME="admin",
        CHAT2API_ADMIN_PASSWORD="strong-password-for-v211-test",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=data_dir,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=30,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )
    app = create_app(settings)
    install_v21_patch(app)
    install_v21_routing_patch(app)
    install_v21_1_patch(app)
    return app


async def add_client(app, name):
    client_id, _token = await app.state.registry.register(name, "Chrome", "0.8.1", {})
    return client_id


with tempfile.TemporaryDirectory() as tmp:
    data_dir = Path(tmp)
    app = build(data_dir)
    client = TestClient(app)
    initial = client.get("/api/admin/concurrency")
    assert initial.status_code == 200, initial.text
    assert initial.json()["max_concurrency"] == 3
    assert initial.json()["mode"] == "per-extension"
    assert initial.json()["client_limits"] == {}
    assert initial.json()["request_weight"] == 1

    # The old global endpoint is retained only as the inherited default.
    changed = client.put("/api/admin/concurrency", json={"max_concurrency": 5})
    assert changed.status_code == 200, changed.text
    assert changed.json()["default_max_concurrency"] == 5

    ext_a = asyncio.run(add_client(app, "A"))
    ext_b = asyncio.run(add_client(app, "B"))
    per_a = client.put(f"/api/admin/extensions/{ext_a}/concurrency", json={"max_concurrency": 2})
    assert per_a.status_code == 200, per_a.text
    assert per_a.json()["max_concurrency"] == 2
    assert per_a.json()["source"] == "extension"

    inherited_b = client.get(f"/api/admin/extensions/{ext_b}/concurrency")
    assert inherited_b.status_code == 200, inherited_b.text
    assert inherited_b.json()["max_concurrency"] == 5
    assert inherited_b.json()["source"] == "default"

    saved = json.loads((data_dir / "concurrency.json").read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert saved["mode"] == "per-extension"
    assert saved["default_max_concurrency"] == 5
    assert saved["max_concurrency"] == 5
    assert saved["clients"][ext_a] == 2
    assert ext_b not in saved["clients"]
    assert saved["request_weight"] == 1

    broker = app.state.broker

    async def capacity_scenario():
        first = await broker.create("req_a1", ext_a)
        second = await broker.create("imgreq_a2", ext_a)
        snapshot_a = broker.capacity_snapshot(ext_a)
        assert snapshot_a["limit_units"] == 2
        assert snapshot_a["used_units"] == 2
        assert snapshot_a["active_requests"] == 2
        assert snapshot_a["limit_source"] == "extension"
        assert set(snapshot_a["request_weights"].values()) == {1}
        assert not broker.can_accept(ext_a, 1)
        assert not broker.can_accept(ext_a, 2)

        rows_b = [await broker.create(f"req_b{index}", ext_b) for index in range(5)]
        snapshot_b = broker.capacity_snapshot(ext_b)
        assert snapshot_b["limit_units"] == 5
        assert snapshot_b["active_requests"] == 5
        assert snapshot_b["limit_source"] == "default"
        assert not broker.can_accept(ext_b, 1)

        await broker.release(first.request_id)
        await broker.release(second.request_id)
        for state in rows_b:
            await broker.release(state.request_id)

    asyncio.run(capacity_scenario())

    # Reset A to inherited default without affecting B or the default itself.
    reset = client.delete(f"/api/admin/extensions/{ext_a}/concurrency")
    assert reset.status_code == 200, reset.text
    assert reset.json()["max_concurrency"] == 5
    assert reset.json()["source"] == "default"

    # Re-apply an override and verify a new app instance recovers it.
    client.put(f"/api/admin/extensions/{ext_a}/concurrency", json={"max_concurrency": 4})
    app2 = build(data_dir)
    client2 = TestClient(app2)
    recovered = client2.get("/api/admin/concurrency")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["default_max_concurrency"] == 5
    assert recovered.json()["client_limits"][ext_a] == 4
    assert app2.state.broker.max_concurrency == 5
    assert app2.state.broker.capacity_snapshot(ext_a)["limit_units"] == 4
    assert app2.state.broker.capacity_snapshot(ext_b)["limit_units"] == 5
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
