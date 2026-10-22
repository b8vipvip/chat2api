"""Hosted Responses image generation accepts images-only keys without widening text access."""
from __future__ import annotations

import base64
from collections import OrderedDict
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api_keys import ApiPrincipal
from app.responses_v108_patch import install_responses_v108_patch


def _app():
    app = FastAPI()
    app.state.settings = SimpleNamespace(api_key="master-secret")
    principals = {
        "key-images": ApiPrincipal(key_id="images", name="image-only", kind="managed", scopes=("images",)),
        "key-chat": ApiPrincipal(key_id="chat", name="chat-only", kind="managed", scopes=("chat",)),
        "key-both": ApiPrincipal(key_id="both", name="full", kind="managed", scopes=("chat", "images")),
    }

    async def authenticate(token):
        return principals.get(token)

    app.state.api_keys = SimpleNamespace(authenticate=authenticate)
    calls = []

    async def existing_engine(**kwargs):
        calls.append(kwargs)
        return {
            "data": [{"b64_json": base64.b64encode(b"true-image-result").decode()}],
            "chat2api": {"request_id": kwargs["x_chat2api_request_id"], "owned_media": []},
        }

    app.state.image_generation_executor = existing_engine
    install_responses_v108_patch(app)
    return app, calls


def _image(stream=False):
    return {
        "model": "gpt-6",
        "input": "生成一张猫头像",
        "tools": [{"type": "image_generation"}],
        "stream": stream,
    }


def _post(client, key, body):
    return client.post("/v1/responses", headers={"Authorization": "Bearer " + key}, json=body)


def test_images_only_key_can_complete_hosted_image_tool_but_not_text():
    app, calls = _app()
    with TestClient(app) as client:
        unauthorized = client.post("/v1/responses", json=_image())
        assert unauthorized.status_code == 401
        assert _post(client, "bogus", _image()).status_code == 401
        response = _post(client, "key-images", _image())
        assert response.status_code == 200
        result = response.json()
        assert result["output"][0]["type"] == "image_generation_call"
        assert base64.b64decode(result["output"][0]["result"]) == b"true-image-result"
        assert len(calls) == 1
        assert calls[0]["principal"].key_id == "images"

        denied_text = _post(client, "key-images", {
            "model": "gpt-6", "input": "just text",
        })
        assert denied_text.status_code == 403
        assert len(calls) == 1

        denied_function = _post(client, "key-images", {
            "model": "gpt-6", "input": "call something",
            "tools": [{"type": "function", "name": "foo", "parameters": {"type": "object"}}],
        })
        assert denied_function.status_code == 403
        assert len(calls) == 1

        denied_images = _post(client, "key-chat", _image())
        assert denied_images.status_code == 403
        assert len(calls) == 1

        mixed_tools = _post(client, "key-images", {
            **_image(), "tools": [{"type": "image_generation"}, {"type": "web_search"}],
        })
        assert mixed_tools.status_code == 400
        assert len(calls) == 1

        full = _post(client, "key-both", _image())
        assert full.status_code == 200
        assert len(calls) == 2

        # Retrieval retains the original chat-scope policy until its stored
        # response owner is independently verified by the retrieval endpoint.
        assert client.get(
            "/v1/responses/" + result["id"],
            headers={"Authorization": "Bearer key-images"},
        ).status_code == 403


def test_images_only_key_streams_one_image_request():
    app, calls = _app()
    with TestClient(app) as client:
        result = _post(client, "key-images", _image(stream=True))
        assert result.status_code == 200
        events = [line for line in result.text.splitlines() if line.startswith("event: ")]
        assert events == [
            "event: response.created",
            "event: response.output_item.added",
            "event: response.image_generation_call.completed",
            "event: response.output_item.done",
            "event: response.completed",
        ]
        assert len(calls) == 1
