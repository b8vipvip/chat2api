"""Exercise the production patch order with a Worker that has no validated models."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import httpx


async def exercise_page_default():
    from app.entry import app

    registry = app.state.registry
    dispatched = []

    class WorkerSocket:
        async def send_json(self, message):
            if message["type"] == "chat.request":
                dispatched.append(message)
                await app.state.broker.publish(
                    message["request_id"], {"type": "chat.completed", "text": "pong"}
                )

    client_id, _ = await registry.register("Free worker", "Chrome", "0.22.295", {})
    socket = WorkerSocket()
    await registry.attach(client_id, socket)
    await registry.touch(client_id, {
        "account_type": "free",
        "account_detection_confidence": "high",
        "account_model_control_present": False,
        "chatgpt_login_state": "ready",
        "chatgpt_login_composer_ready": True,
        "model_validation_state": "pending",
        "model_validation_revision": 145,
        "models": [{"id": "gpt-5.5-mini", "free_default": True}],
    }, websocket=socket)
    assert registry.client_models(client_id) == []
    assert not registry.supports_model(client_id, "auto")
    assert not registry.supports_model(client_id, "chatgpt-web")
    key, token = await app.state.api_keys.create("routing test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        headers={"Authorization": "Bearer " + token},
    ) as client:
        chat = {"messages": [{"role": "user", "content": "ping"}], "model": "default"}
        reply = await client.post("/v1/chat/completions", json=chat)
        assert reply.status_code == 200, reply.text
        assert reply.json()["choices"][0]["message"]["content"] == "pong"
        reply = await client.post("/v1/responses", json={
            "input": "ping", "model": "default", "client_id": client_id,
        })
        assert reply.status_code == 200, reply.text
        assert reply.json()["output"][0]["content"][0]["text"] == "pong"
        assert len(dispatched) == 2
        for message in dispatched:
            options = message["options"]
            assert options["model"] == "default"
            assert not options.get("reasoning_effort")
            assert not options.get("free_default_route")
            authority = options["model_authority"]
            assert authority["default_page_route"] and not authority["strict"]
            assert authority["transport_model"] is None
        reply = await client.post("/v1/chat/completions", json={**chat, "model": "gpt-6-astra"})
        assert reply.status_code == 503, reply.text
        assert "advertises requested model gpt-6-astra" in reply.text

        await registry.touch(client_id, {"chatgpt_login_composer_ready": False}, websocket=socket)
        reply = await client.post("/v1/chat/completions", json={**chat, "client_id": client_id})
        assert reply.status_code != 200 and "ready" in reply.text, reply.text
        await registry.touch(client_id, {"chatgpt_login_composer_ready": True}, websocket=socket)
        app.state.model_entitlements.upsert("key", key["key_id"], "allow", ["gpt-6-astra"])
        reply = await client.post("/v1/chat/completions", json=chat)
        assert reply.status_code == 403, reply.text
        assert len(dispatched) == 2


def test_default_routes_without_a_catalog_in_the_production_app(tmp_path):
    # The entrypoint installs process-global wrappers and holds a data-dir lock.
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, str(Path(__file__).resolve())], cwd=root, check=True, timeout=45,
        env={**os.environ, "PYTHONPATH": str(root), "CHAT2API_DATA_DIR": str(tmp_path)},
    )


def test_page_default_does_not_conflict_with_the_observed_concrete_model():
    from app.model_observability_v144_patch import _authority, _trace

    trace = _trace(_authority({"model": "default"}), {
        "served_model": "gpt-6-sol", "default_model": "gpt-6-sol",
        "request_wire_model": "auto", "response_ok": True, "response_status": 200,
        "model_field": "resolved_model_slug", "evidence_source": "network_response_metadata",
    })
    assert trace["served_model"] == "gpt-6-sol"
    assert not trace["model_conflict"]
    assert not trace["model_default_profile_mismatch"]
    assert not trace["model_authority_violation"]
    assert trace["model_fallback_reason"] is None


if __name__ == "__main__":
    asyncio.run(exercise_page_default())
