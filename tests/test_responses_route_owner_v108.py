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


def test_production_responses_web_search_and_image_tool_use_v108_owner(tmp_path: Path) -> None:
    # Exercise the actual composed app.entry:app route table while replacing
    # authentication and external Worker/image execution boundaries with
    # deterministic in-process fakes. No upstream account or browser session is used.
    script = r'''
import uuid
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from app.entry import app
from app.api_keys import ApiPrincipal
import app.responses_v108_patch as responses
import app.responses_image_tool_v182 as image_tool

principal = ApiPrincipal(
    key_id="route-owner-e2e-principal",
    name="route-owner-e2e",
    kind="managed",
    scopes=("chat", "models", "images"),
)

async def fake_authenticate(app_arg, request, allow_images_only=False):
    return principal

responses._authenticate = fake_authenticate
dispatch_calls = []
image_calls = []

async def fake_dispatch(app_arg, request, body, principal, prompt, native_tools, **kwargs):
    assert native_tools == ["web_search"], native_tools
    request_id = "route-owner-" + uuid.uuid4().hex
    client_id = "route-owner-test-client"
    state = await app_arg.state.broker.create(request_id, client_id)
    dispatch_calls.append({"request_id": request_id, "native_tools": list(native_tools)})
    return state, request_id, client_id, 30

async def fake_collect(state, timeout, include_sources):
    return "v108 production route reached", [{
        "type": "web_search_call",
        "id": "ws_route_owner_test",
        "status": "completed",
        "action": {"type": "search", "query": "route owner regression"},
    }]

async def fake_archive(app_arg, request, principal, request_id, raw_text):
    return raw_text, []

async def fake_image_tool(app_arg, request, body, principal, prompt):
    image_calls.append({"prompt": prompt, "tool_count": len(body.get("tools") or [])})
    return JSONResponse({
        "id": "resp_route_owner_image",
        "object": "response",
        "status": "completed",
        "output": [{"type": "image_generation_call", "status": "completed"}],
    })

responses._dispatch = fake_dispatch
responses._collect = fake_collect
responses._archive_response_media = fake_archive
image_tool.handle_image_tool = fake_image_tool

headers = {"Authorization": "Bearer route-owner-e2e-principal"}
with TestClient(app) as client:
    web = client.post("/v1/responses", headers=headers, json={
        "model": "gpt-5.6",
        "input": "Find a source for the route owner test.",
        "tools": [{"type": "web_search_preview"}],
    })
    assert web.status_code == 200, web.text
    web_payload = web.json()
    assert web_payload["status"] == "completed", web_payload
    assert any(item.get("type") == "web_search_call" for item in web_payload.get("output", [])), web_payload
    assert len(dispatch_calls) == 1, dispatch_calls
    assert dispatch_calls[0]["native_tools"] == ["web_search"]

    response_id = web_payload["id"]
    retrieved = client.get(f"/v1/responses/{response_id}", headers=headers)
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["id"] == response_id

    image = client.post("/v1/responses", headers=headers, json={
        "model": "gpt-5.6",
        "input": "Create a simple test image.",
        "tools": [{"type": "image_generation"}],
    })
    assert image.status_code == 200, image.text
    assert image.json()["id"] == "resp_route_owner_image"
    assert len(image_calls) == 1, image_calls
'''
    repository_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "data")
    env["CHAT2API_API_KEY"] = "responses-route-owner-e2e-key"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
