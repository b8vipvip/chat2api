from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.v21_13_patch import install_v21_13_patch


ROOT = Path(__file__).resolve().parents[1]


class RuntimeRegistry:
    async def authenticate(self, client_id: str, token: str) -> bool:
        return client_id == "ext_test" and token == "token_test"


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_runtime_config_reports_effective_persistent_window_target() -> None:
    app = FastAPI()
    app.state.registry = RuntimeRegistry()
    app.state.broker = SimpleNamespace(max_concurrency=3)
    app.state.concurrency_config = {
        "max_concurrency": 3,
        "limit_for": lambda _client_id: 3,
    }
    install_v21_13_patch(app)
    # This state is installed after v21_13 during the real application bootstrap;
    # the endpoint must resolve it at request time rather than capture startup data.
    app.state.worker_window_limits = {
        "limit_for": lambda client_id: 7 if client_id == "ext_test" else 3,
        "source_for": lambda client_id: "explicit" if client_id == "ext_test" else "concurrency",
    }

    response = TestClient(app).get(
        "/api/extensions/runtime-config",
        headers={
            "X-Extension-Client-ID": "ext_test",
            "X-Extension-Token": "token_test",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["worker_concurrency"] == 3
    assert payload["persistent_window_pool"] is True
    assert payload["persistent_window_pool_revision"] == 132
    assert payload["persistent_window_target"] == 7
    assert payload["persistent_window_target_source"] == "explicit"
    assert payload["persistent_window_idle_close_seconds"] == 0
    assert payload["route_idle_close_seconds"] == 300
    assert payload["route_idle_close_applies_to_physical_pool"] is False
    assert payload["speculative_worker_windows"] is False
    assert payload["prewarmed_worker_windows"] is True
    assert payload["window_decision_authority"] == "persistent-window-pool-v132"
    assert payload["logical_route_authority"] == "conversation-routing-v30"
    assert payload["route_window_authority"] == "conversation-routing-v30+persistent-pool-v132"


def test_v132_is_canonical_serialized_physical_window_authority() -> None:
    source = text("chrome_extension/conversation_persistent_pool_v132.js")
    assert 'persistent_window_pool: true' in source
    assert 'prewarmed_windows: true' in source
    assert 'speculative_windows: false' in source
    assert 'logical_route_authority: "conversation-routing-v30"' in source
    assert 'window_decision_authority: "persistent-window-pool-v132"' in source
    assert 'const task = serial(() => reconcileNow(reason)).finally(() => {' in source
    assert 'return serial(async () => {' in source


def test_v90_observer_directly_reports_v132_authority_without_mutating_windows() -> None:
    source = text("chrome_extension/background_window_observer_v90.js")
    assert 'const POOL_KEY = "__CHAT2API_PERSISTENT_WINDOW_POOL_V132__"' in source
    assert 'window_decision_authority: pool ? "persistent-window-pool-v132" : "conversation-routing-v30"' in source
    assert 'persistent_window_pool_revision: pool ? 132 : null' in source
    assert 'prewarmed_windows: Boolean(pool)' in source
    assert 'speculative_windows: false' in source
    assert "chrome.windows.create" not in source
    assert "chrome.windows.remove" not in source


def test_v133_guards_remain_loaded_as_compatibility_hardening() -> None:
    entry = text("chrome_extension/background_entry.js")
    assert '"conversation_persistent_pool_guard_v133.js"' in entry
    assert '"background_window_authority_v133.js"' in entry


def test_canonical_window_authority_scripts_parse() -> None:
    for path in (
        "chrome_extension/conversation_persistent_pool_v132.js",
        "chrome_extension/background_window_observer_v90.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        assert result.returncode == 0, f"{path}: {result.stderr}"
