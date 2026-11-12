"""Real Python OpenAI SDK -> Chat2API ASGI app interoperability.

The fake registry replaces only the external browser/Worker transport, not the
FastAPI auth, serialization, request admission, Broker or SSE adapter. This
suite is executed with the real openai package by mode2-sdk-contract.yml.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.responses_v108_patch import install_responses_v108_patch
from app.v13_patch import install_v13_patch

openai = pytest.importorskip("openai")

MASTER = "mode2-real-sdk-regression-admin"
EXPECTED_WORKER = "ext_mode2_sdk_fake"


@pytest.fixture
def sdk_app(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_API_KEY=MASTER,
        CHAT2API_DATA_DIR=str(tmp_path),
    ))
    commands: list[dict] = []
    image_buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 60, 90)).save(image_buffer, format="PNG")
    app.state.mode2_test_image_b64 = base64.b64encode(image_buffer.getvalue()).decode("ascii")
    app.state.registry.model_catalog = lambda online_only=True: [{
        "id": "gpt-5.6-sol", "object": "model", "created": 0, "owned_by": "chat2api",
    }]
    app.state.registry.resolve_client = lambda requested: EXPECTED_WORKER

    async def fake_worker_send(client_id: str, payload: dict):
        if payload.get("type") == "chat.request":
            assert client_id == EXPECTED_WORKER
            commands.append(payload)
            published = await app.state.broker.publish(payload["request_id"], {
                "type": "chat.completed",
                "text": "mode2-sdk-fixture-answer",
            })
            assert published
        elif payload.get("type") == "image.request":
            state = app.state.broker.requests[payload["request_id"]]
            await state.queue.put({
                "type": "image.completed",
                "images": [{
                    "b64_json": app.state.mode2_test_image_b64,
                    "mime_type": "image/png",
                }],
            })

    # Install the fake transport first, then let v13 wrap it. Replacing
    # registry.send after install_v13_patch would bypass the production model
    # alias/reasoning adapter and make this SDK fixture lie about dispatch.
    app.state.registry.send = fake_worker_send
    install_v13_patch(app)
    install_responses_v108_patch(app)
    return app, commands


@pytest.fixture
def sdk_transport(sdk_app):
    app, commands = sdk_app
    with TestClient(app) as asgi:
        def forward(request: httpx.Request) -> httpx.Response:
            # MockTransport keeps the stock OpenAI client's request serializer,
            # headers, stream parser and exception types intact. Only the final
            # socket hop is adapted into the real ASGI application.
            path = request.url.raw_path.decode("ascii")
            response = asgi.request(
                request.method, path, headers=dict(request.headers),
                content=request.read(),
            )
            return httpx.Response(
                response.status_code, headers=dict(response.headers),
                content=response.content, request=request,
            )

        transport = httpx.Client(transport=httpx.MockTransport(forward))
        sdk = openai.OpenAI(
            api_key=MASTER,
            base_url="http://testserver/v1",
            max_retries=0,
            http_client=transport,
        )
        try:
            yield sdk, commands
        finally:
            sdk.close()


def test_python_sdk_nonstream_chat_preserves_multiturn_history(sdk_transport) -> None:
    sdk, commands = sdk_transport
    result = sdk.chat.completions.create(
        model="gpt-5.6-sol",
        messages=[
            {"role": "system", "content": "Reply in Chinese."},
            {"role": "user", "content": "First turn"},
            {"role": "assistant", "content": "Acknowledged"},
            {"role": "user", "content": "Second turn"},
        ],
    )
    assert result.object == "chat.completion"
    assert result.choices[0].message.content == "mode2-sdk-fixture-answer"
    assert result.choices[0].finish_reason == "stop"
    assert result.usage.total_tokens >= 1
    assert len(commands) == 1
    prompt = commands[0]["prompt"]
    assert prompt.index("[System]") < prompt.index("First turn") < prompt.index("Acknowledged") < prompt.index("Second turn")


def test_python_sdk_stream_chunks_done_and_requested_model(sdk_transport) -> None:
    sdk, commands = sdk_transport
    stream = sdk.chat.completions.create(
        model="gpt-5.6-sol",
        stream=True,
        messages=[{"role": "user", "content": "Stream this reply."}],
    )
    chunks = list(stream)
    assert chunks
    assert chunks[0].choices[0].delta.role == "assistant"
    text = "".join(str(choice.delta.content or "") for chunk in chunks for choice in chunk.choices)
    assert text == "mode2-sdk-fixture-answer"
    assert any(choice.finish_reason == "stop" for chunk in chunks for choice in chunk.choices)
    assert {chunk.model for chunk in chunks} == {"gpt-5.6-sol"}
    assert len(commands) == 1


def test_python_sdk_models_and_responses_nonstream(sdk_transport) -> None:
    sdk, commands = sdk_transport
    models = sdk.models.list()
    assert models.object == "list"
    assert isinstance(models.data, list)

    model = sdk.models.retrieve("gpt-5.6-sol")
    assert model.id == "gpt-5.6-sol"

    legacy = sdk.completions.create(model="gpt-5.6-sol", prompt="Legacy completion")
    assert legacy.object == "text_completion"
    assert legacy.choices[0].text == "mode2-sdk-fixture-answer"

    response = sdk.responses.create(
        model="gpt-5.6-sol",
        input="Respond from the bridge",
        stream=False,
    )
    assert response.object == "response"
    assert response.status == "completed"
    assert "mode2-sdk-fixture-answer" in response.output_text

    retrieved = sdk.responses.retrieve(response.id)
    assert retrieved.id == response.id
    assert retrieved.output_text == response.output_text

    response_stream = sdk.responses.create(
        model="gpt-5.6-sol",
        input="Stream a Responses event sequence",
        stream=True,
    )
    events = list(response_stream)
    event_types = [event.type for event in events]
    assert event_types[0] == "response.created"
    assert "response.output_text.delta" in event_types
    assert event_types[-1] == "response.completed"
    completed = events[-1]
    assert "mode2-sdk-fixture-answer" in completed.response.output_text
    assert commands[-1]["options"]["response_protocol"] == "responses-v108"


@pytest.mark.parametrize("purpose", ["assistants", "vision", "user_data"])
def test_python_sdk_standard_multipart_files_create_retrieve_content_delete(sdk_transport, purpose: str) -> None:
    sdk, commands = sdk_transport
    created = sdk.files.create(
        file=("测试 sample.txt", io.BytesIO(b"one two three")),
        purpose=purpose,
    )
    assert created.object == "file"
    assert created.filename == "测试_sample.txt"
    assert created.purpose == purpose
    assert created.status == "processed"
    assert created.bytes == len(b"one two three")
    assert created.created_at > 0

    retrieved = sdk.files.retrieve(created.id)
    assert retrieved.id == created.id
    assert retrieved.status == "processed"
    downloaded = sdk.files.content(created.id)
    assert downloaded.read() == b"one two three"

    deleted = sdk.files.delete(created.id)
    assert deleted.id == created.id
    assert deleted.object == "file"
    assert deleted.deleted is True
    assert len(commands) == 0


def test_python_sdk_error_statuses_and_openai_error_envelope(sdk_transport) -> None:
    sdk, _ = sdk_transport
    invalid_sdk = openai.OpenAI(
        api_key="invalid-mode2-key",
        base_url="http://testserver/v1",
        max_retries=0,
        http_client=sdk._client,
    )
    with pytest.raises(openai.AuthenticationError) as unauthorized:
        invalid_sdk.models.list()
    assert unauthorized.value.status_code == 401

    with pytest.raises(openai.APIStatusError) as invalid:
        sdk.chat.completions.create(model="gpt-5.6-sol", messages=[])
    assert invalid.value.status_code == 422
    assert invalid.value.body["type"] == "invalid_request_error"
    assert invalid.value.body["param"] == "messages"


def test_raw_mode2_auth_failure_uses_openai_error_object(sdk_app) -> None:
    app, _ = sdk_app
    with TestClient(app) as client:
        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer invalid-mode2-key"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["type"] == "authentication_error"
    assert response.json()["error"]["message"]

def test_python_sdk_rejects_unsupported_standard_file_purpose(sdk_transport) -> None:
    sdk, _ = sdk_transport
    with pytest.raises(openai.APIStatusError) as error:
        sdk.files.create(
            file=("batch.jsonl", io.BytesIO(b"unsupported batch payload")),
            purpose="batch",
        )
    assert error.value.status_code == 400
    assert error.value.body["type"] == "invalid_request_error"


def test_python_sdk_image_generation_uses_real_mode2_route(sdk_transport) -> None:
    sdk, commands = sdk_transport
    result = sdk.images.generate(
        model="gpt-image",
        prompt="Generate a small test image",
        response_format="b64_json",
    )
    assert result.model == "gpt-image"
    assert result.data and result.data[0].b64_json
    assert len(base64.b64decode(result.data[0].b64_json, validate=True)) > 32
    assert commands == []


def test_python_sdk_model_alias_and_accepted_ignored_chat_parameters(sdk_transport) -> None:
    sdk, commands = sdk_transport
    result = sdk.chat.completions.create(
        model="gpt-5-6-thinking",
        messages=[{"role": "user", "content": "Check alias and parameter contract"}],
        temperature=0.2,
        top_p=0.7,
        max_tokens=24,
        max_completion_tokens=24,
        n=1,
        stop=["STOP"],
        frequency_penalty=0.1,
        presence_penalty=0.1,
        seed=7,
        response_format={"type": "json_object"},
        tools=[{
            "type": "function",
            "function": {"name": "noop", "parameters": {"type": "object"}},
        }],
        tool_choice="auto",
        stream_options={"include_usage": True},
    )
    assert result.model == "gpt-5-6-thinking"
    options = commands[-1]["options"]
    assert options["model"] == "gpt-5.6-sol"
    ignored = {
        "temperature", "top_p", "max_tokens", "max_completion_tokens", "n",
        "stop", "frequency_penalty", "presence_penalty", "seed",
        "response_format", "tools", "tool_choice", "stream_options",
    }
    assert ignored.isdisjoint(options)


def test_python_sdk_responses_echo_only_parameters_are_not_worker_controls(sdk_transport) -> None:
    sdk, commands = sdk_transport
    response = sdk.responses.create(
        model="gpt-5-6-thinking",
        input="Check Responses parameter boundary",
        max_output_tokens=48,
        temperature=0.25,
        top_p=0.75,
        previous_response_id="resp_previous_not_loaded",
        parallel_tool_calls=False,
        text={"format": {"type": "json_object"}},
        truncation="auto",
        metadata={"contract": "mode2"},
    )
    assert response.model == "gpt-5-6-thinking"
    assert response.max_output_tokens == 48
    assert response.temperature == 0.25
    assert response.top_p == 0.75
    assert response.previous_response_id == "resp_previous_not_loaded"
    assert response.parallel_tool_calls is False
    assert response.text.format.type == "json_object"
    assert response.truncation == "auto"
    assert response.metadata == {"contract": "mode2"}
    options = commands[-1]["options"]
    assert options["model"] == "gpt-5.6-sol"
    assert not {
        "max_output_tokens", "temperature", "top_p", "previous_response_id",
        "parallel_tool_calls", "text", "truncation", "metadata",
    }.intersection(options)
