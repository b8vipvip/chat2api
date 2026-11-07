"""Manual Playground chats must not depend on a long-lived browser HTTP hop."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.playground_detached_chat_v292 import install_playground_detached_chat


def test_detached_chat_accepts_once_returns_202_then_completes_same_api_request():
    app = FastAPI()
    app.state.settings = SimpleNamespace(api_key="admin-secret")
    app.state.test_runs = SimpleNamespace(get=lambda run_id: {"run_id": run_id})
    calls = []

    @app.post("/v1/chat/completions")
    async def real_chat_api(request: Request):
        payload = await request.json()
        calls.append({
            "payload": payload,
            "request_id": request.headers.get("x-chat2api-request-id"),
            "credential": request.headers.get("authorization"),
            "detached": request.headers.get("x-chat2api-playground-dispatch"),
        })
        # Longer than the launch HTTP call; the browser can close and the
        # internal job will remain alive on the server event loop.
        await asyncio.sleep(0.15)
        return {"id": "chatcmpl-test", "choices": [{"message": {"content": "ok"}}]}

    install_playground_detached_chat(app, lambda rid: rid)
    request_id = "req_" + "a" * 32
    launch_body = {
        "request_id": request_id,
        "api_key": "managed-user-secret",
        "payload": {
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "long chat test"}],
            "prompt_mode": "full",
            "attachments": [],
            "stream": False,
        },
    }
    headers = {"Authorization": "Bearer admin-secret"}
    with TestClient(app) as client:
        assert client.post("/api/admin/playground/chat-dispatch", json=launch_body).status_code == 403
        started_at = time.monotonic()
        first = client.post("/api/admin/playground/chat-dispatch",
                            headers=headers, json=launch_body)
        elapsed = time.monotonic() - started_at
        assert first.status_code == 202, first.text
        assert elapsed < 0.12, "202 must not wait for the 150ms Worker reply"
        assert first.json()["transport"] == "server-detached-v292"
        repeat = client.post("/api/admin/playground/chat-dispatch",
                             headers=headers, json=launch_body)
        assert repeat.status_code == 202
        assert repeat.json()["reused"] is True
        final = None
        for _ in range(40):
            final = client.get("/api/admin/playground/chat-dispatch/" + request_id,
                               headers=headers)
            assert final.status_code == 200
            if final.json()["state"] != "running":
                break
            time.sleep(0.025)
        assert final.json()["state"] == "done", final.text
        assert final.json()["http_status"] == 200
        assert calls == [{
            "payload": launch_body["payload"],
            "request_id": request_id,
            "credential": "Bearer managed-user-secret",
            "detached": "server-detached-v292",
        }], "retrying the short kickoff must not resubmit the model request"
        assert app.state.playground_detached_chat_jobs[request_id]["task"].done()


def test_detached_chat_refuses_stream_invalid_shape_and_unregistered_dispatch():
    app = FastAPI()
    app.state.settings = SimpleNamespace(api_key="admin-secret")
    app.state.test_runs = SimpleNamespace(get=lambda run_id: None)
    install_playground_detached_chat(app, lambda rid: rid)
    req = {
        "request_id": "req_" + "b" * 32,
        "api_key": "business-secret",
        "payload": {"model": "gpt-6", "messages": [
            {"role": "user", "content": "hi"}], "stream": False},
    }
    h = {"Authorization": "Bearer admin-secret"}
    with TestClient(app) as client:
        assert client.post("/api/admin/playground/chat-dispatch",
                           headers=h, json=req).status_code == 409
        app.state.test_runs = SimpleNamespace(get=lambda run_id: {"run_id": run_id})
        invalid_stream = {**req, "payload": {**req["payload"], "stream": True}}
        assert client.post("/api/admin/playground/chat-dispatch",
                           headers=h, json=invalid_stream).status_code == 422
        poisoned = {**req, "payload": {**req["payload"], "unexpected": "url"}}
        assert client.post("/api/admin/playground/chat-dispatch",
                           headers=h, json=poisoned).status_code == 422
        bad_id = {**req, "request_id": "req_old"}
        assert client.post("/api/admin/playground/chat-dispatch",
                           headers=h, json=bad_id).status_code == 422
        assert app.state.playground_detached_chat_jobs == {}
