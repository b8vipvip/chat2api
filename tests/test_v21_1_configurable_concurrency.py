from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_v211_static_contracts() -> None:
    patch = (ROOT / "app" / "v21_1_patch.py").read_text(encoding="utf-8")
    admin = (ROOT / "app" / "admin_v21_1.js").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    workers = (EXTENSION / "conversation_workers_v24.js").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'PATCH_VERSION = "0.21.1"' in patch
    assert "DEFAULT_MAX_CONCURRENCY = 3" in patch
    assert 'CONFIG_FILENAME = "concurrency.json"' in patch
    assert 'v21_patch.request_weight = lambda _request_id: 1' in patch
    assert "live_voice_patch.LIVE_CAPACITY_WEIGHT = 1" in patch
    assert 'routing["worker_limit"] = int(runtime["max_concurrency"])' in patch
    assert '@app.get("/api/admin/concurrency")' in patch
    assert '@app.put("/api/admin/concurrency")' in patch
    assert '"extension_configurable_concurrency": True' in patch
    assert '"image_request_weight": 1' in patch
    assert '"live_voice_request_weight": 1' in patch

    assert 'const VERSION = "0.21.1"' in admin
    assert "并发设置" in admin
    assert "单扩展最大并发" in admin
    assert 'fetch("/api/admin/concurrency"' in admin
    assert 'method: "PUT"' in admin
    assert "所有任务统一按 1 个并发计数" in admin
    assert "同时运行 3 个图片任务、3 个 GPT Live Session" in admin

    assert "MAX_WORKERS_PER_KEY = 3" in workers
    assert "function workerLimit(message)" in workers
    assert "message?.routing?.worker_limit" in workers
    assert "selected.workerLimit" in workers
    assert 'extension_worker_limit_source: Number(message?.routing?.worker_limit) > 0 ? "server-routing" : "default"' in workers

    assert "from .v21_1_patch import install_v21_1_patch" in entry
    assert entry.index("install_v21_routing_patch(app)") < entry.index("install_v21_1_patch(app)")
    assert "node --check app/admin_v21_1.js" in ci


def test_v211_runtime_config_is_persistent_and_all_request_types_count_one() -> None:
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

with tempfile.TemporaryDirectory() as tmp:
    data_dir = Path(tmp)
    app = build(data_dir)
    client = TestClient(app)
    initial = client.get("/api/admin/concurrency")
    assert initial.status_code == 200, initial.text
    assert initial.json()["max_concurrency"] == 3
    assert initial.json()["request_weight"] == 1

    changed = client.put("/api/admin/concurrency", json={"max_concurrency": 5})
    assert changed.status_code == 200, changed.text
    assert changed.json()["max_concurrency"] == 5
    saved = json.loads((data_dir / "concurrency.json").read_text(encoding="utf-8"))
    assert saved["max_concurrency"] == 5
    assert saved["request_weight"] == 1

    broker = app.state.broker

    async def capacity_scenario():
        request_ids = [
            "req_text",
            "imgreq_image",
            "voicereq_voice",
            "live_voice",
            "req_vision",
        ]
        states = [await broker.create(request_id, "ext-one") for request_id in request_ids]
        snapshot = broker.capacity_snapshot("ext-one")
        assert snapshot["limit_units"] == 5
        assert snapshot["used_units"] == 5
        assert snapshot["active_requests"] == 5
        assert set(snapshot["request_weights"].values()) == {1}
        assert not broker.can_accept("ext-one", 1)
        assert not broker.can_accept("ext-one", 2)
        for state in states:
            await broker.release(state.request_id)
        assert broker.client_used_units("ext-one") == 0

    asyncio.run(capacity_scenario())

    # A new app instance must recover the persisted value without another PUT.
    app2 = build(data_dir)
    client2 = TestClient(app2)
    recovered = client2.get("/api/admin/concurrency")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["max_concurrency"] == 5
    assert app2.state.broker.max_concurrency == 5
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
