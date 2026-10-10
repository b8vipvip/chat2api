"""Protect Windows standby truth from transient and overlapping admin refreshes."""
from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest

from app.window_manager_v88_patch import install_window_manager_v88_patch


ROOT = Path(__file__).resolve().parents[1]


def test_v157_console_does_not_confuse_unknown_with_zero_and_marks_last_verified():
    source = (ROOT / "app/admin_extension_columns.js").read_text(encoding="utf-8")
    assert "const STANDBY_GRACE_MS = 45000" in source
    assert 'status:"last-verified"' in source
    assert 'data-chat2api-standby-source=' in source
    assert "上次核验" in source
    assert "if (renderInFlight) return renderInFlight" in source
    assert "countRaw !== null && countRaw !== undefined" in source
    assert "Number(worker?.standby_window_count)" not in source
    assert 'api("/api/admin/window-manager")' in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node not available")
def test_v157_real_javascript_truth_cases():
    result = subprocess.run(["node", str(ROOT / "tests/standby_truth_ui_v157.mjs")],
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr


class Registry:
    def __init__(self):
        self.sent = 0
        self.updated = 1000
        self.report_enabled = True
        self.sockets = {"ext_test": object()}
        self.clients = {"ext_test": object()}

    def summaries(self):
        return [{
            "client_id": "ext_test", "online": True, "version":"0.22.110",
            "metadata": {
                "extension_version": "0.22.110",
                "chatgpt_login_state": "ready",
                "chatgpt_login_composer_ready": True,
                "window_manager_v88": {
                    "updated_at_ms": self.updated,
                    "active": [
                        {"window_id": i, "window_no": i, "status":"ready",
                         "route_key": "", "source":"standby-observer-v152"}
                        for i in (1, 2, 3)
                    ]
                },
            },
        }]

    async def send(self, client_id, message):
        assert client_id == "ext_test"
        assert message["type"] == "window.manager.refresh"
        self.sent += 1
        await asyncio.sleep(.14)
        if self.report_enabled:
            self.updated += 1


def test_concurrent_administrative_refreshes_share_one_real_probe():
    async def scenario():
        registry = Registry()
        app = FastAPI()
        app.state.registry = registry
        app.state.admin_sessions = SimpleNamespace(authenticate=lambda _: True)
        install_window_manager_v88_patch(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://testserver") as client:
            first = asyncio.create_task(client.get("/api/admin/window-manager"))
            await asyncio.sleep(.025)
            second = asyncio.create_task(client.get("/api/admin/window-manager"))
            a, b = await asyncio.gather(first, second)
            assert a.status_code == b.status_code == 200
            assert registry.sent == 1, "both readers must share the same in-flight proof"
            for result in (a.json(), b.json()):
                item = result["workers"][0]
                assert item["live_verified"] is True
                assert item["standby_window_count"] == 3
                assert item["truth_status"] == "verified"
            # No stale proof is accepted on a later independent refresh.
            registry.report_enabled = False
            response = await client.get("/api/admin/window-manager")
            assert response.status_code == 200
            item = response.json()["workers"][0]
            assert item["live_verified"] is False
            assert item["truth_status"] == "refresh-timeout"
            assert item["standby_window_count"] is None
            assert registry.sent == 2
    asyncio.run(scenario())
