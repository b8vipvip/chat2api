from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_production_entry_has_one_responses_route_owner(tmp_path: Path) -> None:
    # Import the real composed production app in a child process so installer
    # side effects cannot leak into unrelated tests in the pytest process.
    script = r'''
from app.entry import app

post_routes = [
    route for route in app.routes
    if getattr(route, "path", None) == "/v1/responses"
    and "POST" in (getattr(route, "methods", None) or set())
]
get_routes = [
    route for route in app.routes
    if getattr(route, "path", None) == "/v1/responses/{response_id}"
    and "GET" in (getattr(route, "methods", None) or set())
]

assert len(post_routes) == 1, post_routes
assert post_routes[0].endpoint.__module__ == "app.responses_v108_patch"
assert post_routes[0].endpoint.__name__ == "create_response"
assert len(get_routes) == 1, get_routes
assert get_routes[0].endpoint.__module__ == "app.responses_v108_patch"
assert get_routes[0].endpoint.__name__ == "retrieve_response"
'''
    repository_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "data")
    env["CHAT2API_API_KEY"] = "responses-route-owner-test-admin"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
