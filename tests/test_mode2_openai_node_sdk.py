"""Run the official Node SDK against a live Chat2API ASGI server.

The only substituted boundary is the external Chrome/Worker transport. API
routing, auth, request admission, Broker, JSON/SSE adapters, Files endpoints
and error handlers are the real application code.
"""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app.config import Settings
from app.main import create_app
from app.responses_v108_patch import install_responses_v108_patch
from app.v13_patch import install_v13_patch


ROOT = Path(__file__).resolve().parents[1]
MASTER = "mode2-node-real-asgi-master"
EXPECTED_WORKER = "ext_mode2_node_sdk_fake"


@pytest.mark.skipif(not os.environ.get("MODE2_SDK_NODE_MODULES"), reason="Node OpenAI SDK is installed by the dedicated CI lane")
def test_official_node_sdk_uses_real_mode2_routes_and_worker_dispatch(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_API_KEY=MASTER,
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    install_v13_patch(app)
    install_responses_v108_patch(app)
    app.state.registry.model_catalog = lambda online_only=True: [{
        "id": "gpt-5.6-sol", "object": "model", "created": 0, "owned_by": "chat2api",
    }]
    app.state.registry.resolve_client = lambda requested: EXPECTED_WORKER
    commands: list[dict] = []

    async def fake_worker_send(client_id: str, payload: dict) -> None:
        if payload.get("type") != "chat.request":
            return
        assert client_id == EXPECTED_WORKER
        commands.append(payload)
        published = await app.state.broker.publish(payload["request_id"], {
            "type": "chat.completed",
            "text": "mode2-node-sdk-answer",
        })
        assert published

    app.state.registry.send = fake_worker_send

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="on",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "Chat2API ASGI server failed to start"

        env = dict(os.environ)
        env["MODE2_BASE_URL"] = f"http://127.0.0.1:{port}/v1"
        env["MODE2_API_KEY"] = MASTER
        result = subprocess.run(
            ["node", str(ROOT / "tests" / "mode2_sdk_node.mjs")],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=90,
        )
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

        assert len(commands) == 5
        assert "Legacy completion" in commands[0]["prompt"]
        chat_prompt = commands[1]["prompt"]
        assert chat_prompt.index("[System]") < chat_prompt.index("First turn") < chat_prompt.index("Previous reply") < chat_prompt.index("Second turn")
        assert commands[2]["prompt"] == chat_prompt
        assert commands[3]["options"]["response_protocol"] == "responses-v108"
        assert commands[4]["options"]["response_protocol"] == "responses-v108"
        assert app.state.broker.requests == {}
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
