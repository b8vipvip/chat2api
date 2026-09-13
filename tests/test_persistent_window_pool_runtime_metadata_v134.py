from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI

from app.worker_limits_clipboard_v121_patch import install_worker_limits_clipboard_v121_patch


class FakeRegistry:
    def __init__(self) -> None:
        self.clients = {
            "ext_test": SimpleNamespace(
                connection_enabled=True,
                metadata={"max_concurrency": 3},
            )
        }
        self.sockets = {}

    def summaries(self) -> list[dict]:
        return [{"client_id": "ext_test", "capacity": {}}]

    async def send(self, _client_id: str, _payload: dict) -> None:
        return None


class FakeSessions:
    def authenticate(self, _cookie: str | None) -> bool:
        return True


class FakeLoginSessions:
    def require(self, _worker_id: str, _ticket: str, *, touch: bool = False) -> None:
        return None


def make_app(tmp_path) -> FastAPI:
    app = FastAPI()
    app.state.registry = FakeRegistry()
    app.state.settings = SimpleNamespace(data_dir=str(tmp_path))
    app.state.admin_sessions = FakeSessions()
    app.state.linux_workers = SimpleNamespace(data={"workers": {}})
    app.state.worker_login_sessions = FakeLoginSessions()
    app.state.send_linux_worker_command = lambda *args, **kwargs: None
    app.state.concurrency_config = {"max_concurrency": 3, "limit_for": lambda _client_id: 3}
    install_worker_limits_clipboard_v121_patch(app)
    return app


def test_v121_window_target_metadata_matches_persistent_pool(tmp_path) -> None:
    app = make_app(tmp_path)
    payload = app.state.worker_window_limits["payload_for"]("ext_test")

    assert payload["max_concurrency"] == 3
    assert payload["max_windows"] == 3
    assert payload["inherits_concurrency"] is True
    assert payload["policy"] == "persistent-prewarmed-total-window-pool-v132"
    assert payload["window_decision_authority"] == "persistent-window-pool-v132"
    assert payload["route_window_authority"] == "conversation-routing-v30+persistent-pool-v132"
    assert payload["prewarmed_windows"] is True
    assert payload["speculative_windows"] is False

    summary = app.state.registry.summaries()[0]
    assert summary["max_windows"] == 3
    assert summary["capacity"]["window_policy"] == "persistent-prewarmed-total-window-pool-v132"
    assert summary["capacity"]["window_decision_authority"] == "persistent-window-pool-v132"
    assert summary["capacity"]["prewarmed_windows"] is True
    assert summary["capacity"]["speculative_windows"] is False


def test_v121_source_no_longer_advertises_on_demand_hard_cap() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "worker_limits_clipboard_v121_patch.py").read_text(encoding="utf-8")
    assert "on-demand-hard-cap-no-warm-pool" not in source
    assert "persistent-prewarmed-total-window-pool-v132" in source
