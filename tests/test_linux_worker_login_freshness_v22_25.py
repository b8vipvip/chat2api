from types import SimpleNamespace

from fastapi import FastAPI

from app.linux_worker_login_freshness_patch import (
    PATCH_VERSION,
    _bridge_login_observation,
    install_linux_worker_login_freshness_patch,
)
from app.linux_worker_login_sessions import LoginSessionStore


OPEN_PATH = "/api/admin/linux-workers/{worker_id}/login-session"
FRAME_PATH = "/api/admin/linux-workers/{worker_id}/login-session/frame"


def test_bridge_login_observation_uses_current_bridge_timestamp_and_composer_flag():
    worker = {
        "chatgpt_status": "ready",
        "metadata": {
            "bridge": {
                "login_state": "login_required",
                "composer_ready": False,
                "login_checked_at_ms": "123456",
            }
        },
    }
    assert _bridge_login_observation(worker) == {
        "state": "login_required",
        "composer_ready": False,
        "checked_at_ms": 123456,
    }


def test_login_freshness_patch_replaces_only_open_and_frame_routes():
    app = FastAPI()

    @app.post(OPEN_PATH)
    async def old_open(worker_id: str):
        return {"worker_id": worker_id}

    @app.get(FRAME_PATH)
    async def old_frame(worker_id: str):
        return {"worker_id": worker_id}

    @app.post("/api/admin/linux-workers/{worker_id}/login-session/input")
    async def input_route(worker_id: str):
        return {"worker_id": worker_id}

    app.state.linux_worker_control_plane_installed = True
    app.state.worker_login_sessions = LoginSessionStore()
    app.state.linux_workers = SimpleNamespace(data={"workers": {}})

    async def send_worker_command(*_args, **_kwargs):
        return {"result": {"ok": True}}

    app.state.send_linux_worker_command = send_worker_command
    install_linux_worker_login_freshness_patch(app)

    routes = {
        (route.path, method): route.endpoint.__name__
        for route in app.router.routes
        for method in (getattr(route, "methods", set()) or set())
    }
    assert routes[(OPEN_PATH, "POST")] == "open_worker_login_session_fresh"
    assert routes[(FRAME_PATH, "GET")] == "worker_login_frame_fresh"
    assert routes[("/api/admin/linux-workers/{worker_id}/login-session/input", "POST")] == "input_route"
    assert app.state.linux_worker_login_freshness_installed is True
    assert PATCH_VERSION == "0.22.25"
